# -*- coding: utf-8 -*-
"""
Realtime full-set NO edge monitor for a Polymarket neg-risk event.

Default target:
  Next leader out of power before 2027? (No Orban)

This script is read-only. It never places orders.
It polls order books every N seconds, computes:
  edge = (quoted_NO_legs - 1) - sum(NO_ask_price) - sum(taker_fee) - gas_buffer

The local HTTP dashboard is served at:
  http://127.0.0.1:5188/
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

HOST = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
DEFAULT_SLUG = "next-leader-out-of-power-before-2027-no-orban"
DEFAULT_LOG = Path(__file__).resolve().parent / "leader_out_no_orban_edge.jsonl"
DEFAULT_TOP_LOG = Path(__file__).resolve().parent / "top_markets_direct_no_ignore_missing_history.jsonl"


@dataclass
class Leg:
    title: str
    condition_id: str
    yes_token: str
    no_token: str
    fee_rate: float
    fee_exp: float


class State:
    def __init__(self, max_points: int):
        self.lock = threading.Lock()
        self.max_points = max_points
        self.event_title = ""
        self.legs: list[Leg] = []
        self.history: list[dict[str, Any]] = []
        self.latest_point: dict[str, Any] | None = None
        self.top_markets: dict[str, Any] = {
            "ts": None,
            "rows": [],
            "error": None,
            "elapsed_sec": None,
        }
        self.last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "event_title": self.event_title,
                "legs": len(self.legs),
                "history": list(self.history),
                "latest": self.latest_point,
                "top_markets": self.top_markets,
                "last_error": self.last_error,
            }

    def set_event(self, title: str, legs: list[Leg]) -> None:
        with self.lock:
            self.event_title = title
            self.legs = legs

    def add_point(self, point: dict[str, Any]) -> None:
        with self.lock:
            self.history.append(summarize_point(point))
            if len(self.history) > self.max_points:
                self.history = self.history[-self.max_points :]
            self.latest_point = point
            self.last_error = None

    def load_points(self, points: list[dict[str, Any]]) -> None:
        with self.lock:
            trimmed = [summarize_point(point) for point in points[-self.max_points :]]
            self.history = [summarize_point(point) for point in trimmed]
            self.latest_point = trimmed[-1] if trimmed else None

    def set_error(self, error: str) -> None:
        with self.lock:
            self.last_error = error

    def set_top_markets(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.top_markets = payload


def summarize_point(point: dict[str, Any]) -> dict[str, Any]:
    if point.get("edge_per_dollar") is None:
        point = dict(point)
        point["edge_per_dollar"] = edge_per_dollar(
            as_float(point.get("edge")),
            as_float(point.get("no_sum"), 0.0) or 0.0,
            as_float(point.get("fee_sum"), 0.0) or 0.0,
            as_float(point.get("gas_buffer"), 0.0) or 0.0,
        )
    keys = (
        "ts",
        "complete",
        "direct_complete",
        "legs",
        "included_legs",
        "quoted_legs",
        "missing_no_ask",
        "receive",
        "no_sum",
        "fee_sum",
        "gas_buffer",
        "edge",
        "edge_cents",
        "edge_per_dollar",
        "executable_size",
        "executable_profit",
    )
    return {key: point.get(key) for key in keys}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "y"}


def jloads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def get_json(session: requests.Session, url: str, params: dict[str, Any] | None = None, timeout: int = 12) -> Any:
    last: Exception | None = None
    for i in range(3):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise RuntimeError(f"GET failed {url} params={params}: {last}") from last


def post_json(session: requests.Session, url: str, payload: Any, timeout: int = 12) -> Any:
    last: Exception | None = None
    for i in range(3):
        try:
            response = session.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            time.sleep(0.25 * (i + 1))
    raise RuntimeError(f"POST failed {url}: {last}") from last


def best_ask(book: dict[str, Any]) -> tuple[float | None, float]:
    rows: list[tuple[float, float]] = []
    for row in book.get("asks") or []:
        price = as_float(row.get("price"))
        size = as_float(row.get("size"), 0.0) or 0.0
        if price is not None and size > 0:
            rows.append((price, size))
    rows.sort(key=lambda x: x[0])
    return rows[0] if rows else (None, 0.0)


def best_bid(book: dict[str, Any]) -> tuple[float | None, float]:
    rows: list[tuple[float, float]] = []
    for row in book.get("bids") or []:
        price = as_float(row.get("price"))
        size = as_float(row.get("size"), 0.0) or 0.0
        if price is not None and size > 0:
            rows.append((price, size))
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows[0] if rows else (None, 0.0)


def taker_fee(price: float, fee_rate: float, fee_exp: float) -> float:
    if fee_rate <= 0:
        return 0.0
    return fee_rate * ((price * (1.0 - price)) ** max(fee_exp, 1.0))


def edge_per_dollar(edge: float | None, no_sum: float, fee_sum: float, gas_buffer: float) -> float | None:
    if edge is None:
        return None
    invested = no_sum + fee_sum + gas_buffer
    if invested <= 0:
        return None
    return edge / invested


def load_fee(session: requests.Session, condition_id: str) -> tuple[float, float]:
    info = get_json(session, f"{HOST}/clob-markets/{condition_id}")
    fd = info.get("fd") or {}
    return as_float(fd.get("r"), 0.0) or 0.0, as_float(fd.get("e"), 1.0) or 1.0


def load_event(session: requests.Session, slug: str) -> tuple[str, list[Leg]]:
    events = get_json(session, f"{GAMMA}/events", {"slug": slug}, timeout=20)
    if not events:
        raise RuntimeError(f"event not found: {slug}")
    event = events[0] if isinstance(events, list) else events
    legs: list[Leg] = []
    for market in event.get("markets") or []:
        if not (as_bool(market.get("acceptingOrders")) and as_bool(market.get("enableOrderBook"))):
            continue
        outcomes = jloads(market.get("outcomes"), [])
        tokens = jloads(market.get("clobTokenIds"), [])
        if len(outcomes) != 2 or len(tokens) != 2:
            continue
        no_index = None
        for i, outcome in enumerate(outcomes):
            if str(outcome).strip().lower() == "no":
                no_index = i
                break
        if no_index is None:
            continue
        yes_index = 1 - no_index
        condition_id = str(market.get("conditionId") or "")
        if not condition_id:
            continue
        fee_rate, fee_exp = load_fee(session, condition_id)
        title = str(market.get("groupItemTitle") or market.get("question") or condition_id)
        legs.append(
            Leg(
                title=title,
                condition_id=condition_id,
                yes_token=str(tokens[yes_index]),
                no_token=str(tokens[no_index]),
                fee_rate=fee_rate,
                fee_exp=fee_exp,
            )
        )
    if len(legs) < 3:
        raise RuntimeError(f"not enough active NO legs: {len(legs)}")
    return str(event.get("title") or slug), legs


def fetch_books(session: requests.Session, legs: list[Leg], workers: int) -> dict[str, dict[str, Any]]:
    tokens = sorted({token for leg in legs for token in (leg.no_token, leg.yes_token)})
    books: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(get_json, session, f"{HOST}/book", {"token_id": token}, 8): token
            for token in tokens
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                books[token] = future.result()
            except Exception:
                books[token] = {}
    return books


def fetch_books_batch(session: requests.Session, legs: list[Leg], chunk_size: int = 200) -> dict[str, dict[str, Any]]:
    tokens = sorted({token for leg in legs for token in (leg.no_token, leg.yes_token)})
    books: dict[str, dict[str, Any]] = {}
    for start in range(0, len(tokens), chunk_size):
        chunk = tokens[start : start + chunk_size]
        payload = [{"token_id": token} for token in chunk]
        rows = post_json(session, f"{HOST}/books", payload, timeout=20)
        if not isinstance(rows, list):
            continue
        for i, book in enumerate(rows):
            if not isinstance(book, dict):
                continue
            token = str(book.get("asset_id") or book.get("token_id") or "")
            if not token and i < len(chunk):
                token = chunk[i]
            if token:
                books[token] = book
    return books


def fetch_no_books_batch(session: requests.Session, legs: list[Leg], chunk_size: int = 200) -> dict[str, dict[str, Any]]:
    tokens = sorted({leg.no_token for leg in legs})
    books: dict[str, dict[str, Any]] = {}
    for start in range(0, len(tokens), chunk_size):
        chunk = tokens[start : start + chunk_size]
        payload = [{"token_id": token} for token in chunk]
        rows = post_json(session, f"{HOST}/books", payload, timeout=20)
        if not isinstance(rows, list):
            continue
        for i, book in enumerate(rows):
            if not isinstance(book, dict):
                continue
            token = str(book.get("asset_id") or book.get("token_id") or "")
            if not token and i < len(chunk):
                token = chunk[i]
            if token:
                books[token] = book
    return books


def compute_point_from_books(books: dict[str, dict[str, Any]], legs: list[Leg], gas_buffer: float) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    no_sum = 0.0
    fee_sum = 0.0
    complete = True
    direct_complete = True
    executable_sizes: list[float] = []

    for leg in legs:
        no_ask, no_size = best_ask(books.get(leg.no_token) or {})
        yes_bid, yes_size = best_bid(books.get(leg.yes_token) or {})
        synthetic = (1.0 - yes_bid) if yes_bid is not None else None

        if no_ask is None:
            direct_complete = False

        effective = None
        source = None
        effective_size = 0.0
        if no_ask is not None and synthetic is not None:
            if no_ask < synthetic - 1e-12:
                effective, source, effective_size = no_ask, "direct_no_ask", no_size
            elif synthetic < no_ask - 1e-12:
                effective, source, effective_size = synthetic, "yes_bid_implied_no", yes_size
            else:
                effective, source, effective_size = no_ask, "direct_or_yes_bid", max(no_size, yes_size)
        elif no_ask is not None:
            effective, source, effective_size = no_ask, "direct_no_ask", no_size
        elif synthetic is not None:
            effective, source, effective_size = synthetic, "yes_bid_implied_no", yes_size

        if effective is None:
            complete = False
            rows.append({"title": leg.title, "ok": False})
            continue

        fee = taker_fee(effective, leg.fee_rate, leg.fee_exp)
        no_sum += effective
        fee_sum += fee
        executable_sizes.append(effective_size)
        rows.append(
            {
                "title": leg.title,
                "ok": True,
                "direct_no_ask": no_ask,
                "direct_no_size": no_size,
                "yes_bid": yes_bid,
                "yes_bid_size": yes_size,
                "effective_no": effective,
                "effective_size": effective_size,
                "source": source,
                "fee": fee,
            }
        )

    receive = len(legs) - 1.0
    edge = receive - no_sum - fee_sum - gas_buffer if complete else None
    edge_roi = edge_per_dollar(edge, no_sum, fee_sum, gas_buffer)
    executable_size = min(executable_sizes) if complete and executable_sizes else None
    executable_profit = None
    if executable_size is not None:
        executable_profit = (receive - no_sum - fee_sum) * executable_size - gas_buffer
    return {
        "ts": utc_now(),
        "complete": complete,
        "direct_complete": direct_complete,
        "legs": len(legs),
        "receive": receive,
        "no_sum": no_sum,
        "fee_sum": fee_sum,
        "gas_buffer": gas_buffer,
        "edge": edge,
        "edge_cents": None if edge is None else edge * 100.0,
        "edge_per_dollar": edge_roi,
        "executable_size": executable_size,
        "executable_profit": executable_profit,
        "rows": rows,
    }


def compute_direct_no_zero_point_from_books(
    books: dict[str, dict[str, Any]],
    legs: list[Leg],
    total_legs: int,
    gas_buffer: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    no_sum = 0.0
    fee_sum = 0.0
    quoted_legs = 0
    missing_no_ask = max(0, total_legs - len(legs))
    executable_sizes: list[float] = []

    for leg in legs:
        no_ask, no_size = best_ask(books.get(leg.no_token) or {})
        missing = no_ask is None
        effective = 0.0 if missing else no_ask
        fee = 0.0 if missing else taker_fee(effective, leg.fee_rate, leg.fee_exp)
        if missing:
            missing_no_ask += 1
            source = "missing_no_ask_zero"
        else:
            quoted_legs += 1
            source = "direct_no_ask"
            executable_sizes.append(no_size)
        no_sum += effective
        fee_sum += fee
        rows.append(
            {
                "title": leg.title,
                "ok": True,
                "missing_no_ask": missing,
                "direct_no_ask": no_ask,
                "direct_no_size": no_size,
                "effective_no": effective,
                "effective_size": 0.0 if missing else no_size,
                "source": source,
                "fee": fee,
            }
        )

    receive = max(0.0, quoted_legs - 1.0)
    edge = receive - no_sum - fee_sum - gas_buffer
    edge_roi = edge_per_dollar(edge, no_sum, fee_sum, gas_buffer)
    executable_size = min(executable_sizes) if executable_sizes else None
    executable_profit = None
    if executable_size is not None:
        executable_profit = (receive - no_sum - fee_sum) * executable_size - gas_buffer
    return {
        "ts": utc_now(),
        "complete": True,
        "direct_complete": missing_no_ask == 0,
        "legs": total_legs,
        "included_legs": quoted_legs,
        "quoted_legs": quoted_legs,
        "missing_no_ask": missing_no_ask,
        "receive": receive,
        "no_sum": no_sum,
        "fee_sum": fee_sum,
        "gas_buffer": gas_buffer,
        "edge": edge,
        "edge_cents": edge * 100.0,
        "edge_per_dollar": edge_roi,
        "executable_size": executable_size,
        "executable_profit": executable_profit,
        "rows": rows,
    }


def compute_point(session: requests.Session, legs: list[Leg], workers: int, gas_buffer: float) -> dict[str, Any]:
    books = fetch_no_books_batch(session, legs)
    return compute_direct_no_zero_point_from_books(books, legs, len(legs), gas_buffer)


def load_existing_history(log_path: Path, max_points: int) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    points: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                point = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(point, dict) and "ts" in point and "edge" in point:
                points.append(point)
                if len(points) > max_points * 2:
                    points = points[-max_points:]
    return points[-max_points:]


def make_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def load_fee_cached(
    session: requests.Session,
    condition_id: str,
    fee_cache: dict[str, tuple[float, float, bool]],
    fee_lock: threading.Lock,
) -> tuple[float, float, bool]:
    with fee_lock:
        cached = fee_cache.get(condition_id)
    if cached is not None:
        return cached

    info = get_json(session, f"{HOST}/clob-markets/{condition_id}", timeout=10)
    fd = info.get("fd") or {}
    row = (
        as_float(fd.get("r"), 0.0) or 0.0,
        as_float(fd.get("e"), 1.0) or 1.0,
        as_bool(info.get("nr")),
    )
    with fee_lock:
        fee_cache[condition_id] = row
    return row


def fee_from_market(market: dict[str, Any]) -> tuple[float, float] | None:
    schedule = jloads(market.get("feeSchedule"), {})
    if not isinstance(schedule, dict):
        return None
    rate = as_float(schedule.get("rate"))
    exp = as_float(schedule.get("exponent"), 1.0)
    if rate is None:
        return None
    return rate, exp or 1.0


def top_events_by_volume(session: requests.Session, count: int, pages: int) -> list[dict[str, Any]]:
    events_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(max(1, pages)):
        events = get_json(
            session,
            f"{GAMMA}/events",
            {
                "closed": "false",
                "active": "true",
                "limit": 100,
                "offset": page * 100,
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=20,
        )
        if not events:
            break

        for event in events:
            if not as_bool(event.get("negRisk")):
                continue
            slug = str(event.get("slug") or event.get("id") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            events_out.append(event)
            if len(events_out) >= count:
                return events_out

        if len(events) < 100:
            break
    return events_out


def top_market_key(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("title") or "")


def apply_top_market_stats(stats: dict[str, dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    ts = payload.get("ts") or utc_now()
    for row in payload.get("rows") or []:
        key = top_market_key(row)
        if not key:
            continue
        stat = stats.setdefault(
            key,
            {
                "title": row.get("title") or key,
                "slug": row.get("slug") or "",
                "samples": 0,
                "complete_samples": 0,
                "positive_count": 0,
                "max_edge": None,
                "max_edge_per_dollar": None,
                "max_edge_ts": None,
            },
        )
        stat["title"] = row.get("title") or stat.get("title") or key
        stat["slug"] = row.get("slug") or stat.get("slug") or ""
        stat["samples"] = int(stat.get("samples") or 0) + 1
        stat["last_seen_ts"] = ts

        edge = as_float(row.get("edge"))
        if edge is not None:
            edge_roi = as_float(row.get("edge_per_dollar"))
            if edge_roi is None:
                edge_roi = edge_per_dollar(
                    edge,
                    as_float(row.get("no_sum"), 0.0) or 0.0,
                    as_float(row.get("fee_sum"), 0.0) or 0.0,
                    as_float(row.get("gas_buffer"), 0.0) or 0.0,
                )
                row["edge_per_dollar"] = edge_roi
            stat["complete_samples"] = int(stat.get("complete_samples") or 0) + 1
            if edge > 0:
                stat["positive_count"] = int(stat.get("positive_count") or 0) + 1
            max_edge_roi = as_float(stat.get("max_edge_per_dollar"))
            if edge_roi is not None and (max_edge_roi is None or edge_roi > max_edge_roi):
                stat["max_edge"] = edge
                stat["max_edge_per_dollar"] = edge_roi
                stat["max_edge_ts"] = ts

        row["history_samples"] = stat.get("samples", 0)
        row["history_complete_samples"] = stat.get("complete_samples", 0)
        row["positive_edge_count"] = stat.get("positive_count", 0)
        row["max_edge"] = stat.get("max_edge")
        row["max_edge_per_dollar"] = stat.get("max_edge_per_dollar")
        row["max_edge_ts"] = stat.get("max_edge_ts")

    best: dict[str, Any] | None = None
    positive_total = 0
    for stat in stats.values():
        positive_total += int(stat.get("positive_count") or 0)
        max_edge_roi = as_float(stat.get("max_edge_per_dollar"))
        if max_edge_roi is None:
            continue
        if best is None or max_edge_roi > (as_float(best.get("max_edge_per_dollar")) or -999999.0):
            best = stat

    summary = payload.setdefault("summary", {})
    summary["history_markets"] = len(stats)
    summary["history_positive"] = positive_total
    summary["history_best_edge"] = None if best is None else best.get("max_edge")
    summary["history_best_edge_per_dollar"] = None if best is None else best.get("max_edge_per_dollar")
    summary["history_best_title"] = None if best is None else best.get("title")
    summary["history_best_ts"] = None if best is None else best.get("max_edge_ts")
    return payload


def load_top_market_stats(log_path: Path) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not log_path.exists():
        return stats
    with log_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                apply_top_market_stats(stats, payload)
    return stats


def append_top_market_history(log_path: Path, payload: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def top_legs_from_event(
    session: requests.Session,
    event: dict[str, Any],
    fee_cache: dict[str, tuple[float, float, bool]],
    fee_lock: threading.Lock,
) -> tuple[int, list[Leg]]:
    total_legs = 0
    legs: list[Leg] = []
    for market in event.get("markets") or []:
        outcomes = jloads(market.get("outcomes"), [])
        tokens = jloads(market.get("clobTokenIds"), [])
        if len(outcomes) != 2 or len(tokens) != 2:
            continue

        no_index = None
        for i, outcome in enumerate(outcomes):
            if str(outcome).strip().lower() == "no":
                no_index = i
                break
        if no_index is None:
            continue

        condition_id = str(market.get("conditionId") or "")
        if not condition_id:
            continue

        total_legs += 1
        if not (as_bool(market.get("acceptingOrders")) and as_bool(market.get("enableOrderBook"))):
            continue
        if as_bool(market.get("closed")):
            continue

        fee_info = fee_from_market(market)
        if fee_info is None:
            try:
                fee_rate, fee_exp, supports_neg_risk = load_fee_cached(session, condition_id, fee_cache, fee_lock)
            except Exception:
                continue
            if not supports_neg_risk:
                continue
        else:
            fee_rate, fee_exp = fee_info

        yes_index = 1 - no_index
        legs.append(
            Leg(
                title=str(market.get("groupItemTitle") or market.get("question") or condition_id),
                condition_id=condition_id,
                yes_token=str(tokens[yes_index]),
                no_token=str(tokens[no_index]),
                fee_rate=fee_rate,
                fee_exp=fee_exp,
            )
        )
    return total_legs, legs


def scan_top_market(
    rank: int,
    event: dict[str, Any],
    args: argparse.Namespace,
    fee_cache: dict[str, tuple[float, float, bool]],
    fee_lock: threading.Lock,
) -> dict[str, Any]:
    title = str(event.get("title") or event.get("slug") or "")
    slug = str(event.get("slug") or "")
    volume = as_float(event.get("volume24hr"), 0.0) or 0.0
    row: dict[str, Any] = {
        "rank": rank,
        "title": title,
        "slug": slug,
        "volume24hr": volume,
        "ts": utc_now(),
    }

    session = make_session("pmscan-top-market-edge-monitor/0.1")
    try:
        total_legs, legs = top_legs_from_event(session, event, fee_cache, fee_lock)
        row.update({"legs": total_legs, "active_legs": len(legs)})

        if total_legs < 3:
            row.update({"status": "too_few_legs", "quoted_legs": 0, "missing_legs": total_legs})
            return row
        if total_legs > args.top_max_legs:
            row.update({"status": "too_many_legs", "quoted_legs": 0, "missing_legs": total_legs})
            return row
        if not legs:
            receive = 0.0
            edge = receive - args.gas_buffer
            edge_roi = edge_per_dollar(edge, 0.0, 0.0, args.gas_buffer)
            row.update(
                {
                    "status": "complete",
                    "included_legs": 0,
                    "quoted_legs": 0,
                    "missing_legs": total_legs,
                    "missing_no_ask": total_legs,
                    "receive": receive,
                    "no_sum": 0.0,
                    "fee_sum": 0.0,
                    "gas_buffer": args.gas_buffer,
                    "edge": edge,
                    "edge_cents": edge * 100.0,
                    "edge_per_dollar": edge_roi,
                    "direct_no_ask_complete": False,
                }
            )
            return row

        books = fetch_no_books_batch(session, legs)
        point = compute_direct_no_zero_point_from_books(books, legs, total_legs, args.gas_buffer)
        quoted_legs = int(point.get("quoted_legs") or 0)
        missing_no_ask = int(point.get("missing_no_ask") or 0)
        row.update(
            {
                "status": "complete",
                "included_legs": point.get("included_legs"),
                "quoted_legs": quoted_legs,
                "missing_legs": missing_no_ask,
                "missing_no_ask": missing_no_ask,
                "receive": point.get("receive"),
                "no_sum": point.get("no_sum"),
                "fee_sum": point.get("fee_sum"),
                "gas_buffer": point.get("gas_buffer"),
                "edge": point.get("edge"),
                "edge_cents": point.get("edge_cents"),
                "edge_per_dollar": point.get("edge_per_dollar"),
                "direct_no_ask_complete": point.get("direct_complete"),
            }
        )
        return row
    except Exception as exc:
        row.update({"status": "error", "error": str(exc)})
        return row


def scan_top_market_snapshot(rank: int, event: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    title = str(event.get("title") or event.get("slug") or "")
    slug = str(event.get("slug") or "")
    volume = as_float(event.get("volume24hr"), 0.0) or 0.0
    total_legs = 0
    active_legs = 0
    quoted_legs = 0
    no_sum = 0.0
    fee_sum = 0.0

    for market in event.get("markets") or []:
        outcomes = jloads(market.get("outcomes"), [])
        tokens = jloads(market.get("clobTokenIds"), [])
        if len(outcomes) != 2 or len(tokens) != 2:
            continue
        if not any(str(outcome).strip().lower() == "no" for outcome in outcomes):
            continue

        total_legs += 1
        if not (as_bool(market.get("acceptingOrders")) and as_bool(market.get("enableOrderBook"))):
            continue
        if as_bool(market.get("closed")):
            continue
        active_legs += 1

        fee_info = fee_from_market(market)
        yes_bid = as_float(market.get("bestBid"))
        if fee_info is None or yes_bid is None or yes_bid <= 0:
            continue

        effective_no = 1.0 - yes_bid
        if effective_no < 0 or effective_no > 1:
            continue
        fee_rate, fee_exp = fee_info
        no_sum += effective_no
        fee_sum += taker_fee(effective_no, fee_rate, fee_exp)
        quoted_legs += 1

    row: dict[str, Any] = {
        "rank": rank,
        "title": title,
        "slug": slug,
        "volume24hr": volume,
        "ts": utc_now(),
        "legs": total_legs,
        "active_legs": active_legs,
        "quoted_legs": quoted_legs,
        "missing_legs": max(0, total_legs - quoted_legs),
        "no_sum": no_sum,
        "fee_sum": fee_sum,
        "gas_buffer": args.gas_buffer,
    }

    if total_legs < 3:
        row["status"] = "too_few_legs"
        return row

    complete = active_legs == total_legs and quoted_legs == total_legs
    if not complete:
        row["status"] = "incomplete"
        return row

    receive = total_legs - 1.0
    edge = receive - no_sum - fee_sum - args.gas_buffer
    edge_roi = edge_per_dollar(edge, no_sum, fee_sum, args.gas_buffer)
    row.update(
        {
            "status": "complete",
            "receive": receive,
            "edge": edge,
            "edge_cents": edge * 100.0,
            "edge_per_dollar": edge_roi,
        }
    )
    return row


def scan_top_markets(
    args: argparse.Namespace,
    fee_cache: dict[str, tuple[float, float, bool]],
    fee_lock: threading.Lock,
) -> dict[str, Any]:
    started = time.time()
    session = make_session("pmscan-top-market-loader/0.1")
    events = top_events_by_volume(session, args.top_market_count, args.top_pages)
    rows: list[dict[str, Any]] = []
    if not args.top_use_snapshot:
        with ThreadPoolExecutor(max_workers=max(1, args.top_event_workers)) as executor:
            futures = {
                executor.submit(scan_top_market, i + 1, event, args, fee_cache, fee_lock): i
                for i, event in enumerate(events)
            }
            for future in as_completed(futures):
                rows.append(future.result())
    else:
        rows = [scan_top_market_snapshot(i + 1, event, args) for i, event in enumerate(events)]

    rows.sort(key=lambda item: item.get("rank", 999999))
    complete = [row for row in rows if row.get("status") == "complete"]
    positive = [row for row in complete if (row.get("edge") or 0.0) > 0]
    best = None
    if complete:
        best = max(complete, key=lambda item: item.get("edge") or -999999.0)
    return {
        "ts": utc_now(),
        "rows": rows,
        "error": None,
        "elapsed_sec": time.time() - started,
        "summary": {
            "requested": args.top_market_count,
            "found": len(events),
            "complete": len(complete),
            "positive": len(positive),
            "best_edge": None if best is None else best.get("edge"),
            "best_title": None if best is None else best.get("title"),
        },
    }


def top_markets_loop(args: argparse.Namespace, state: State) -> None:
    fee_cache: dict[str, tuple[float, float, bool]] = {}
    fee_lock = threading.Lock()
    log_path = Path(args.top_log)
    stats = load_top_market_stats(log_path)
    if stats:
        print(f"loaded_top_market_history markets={len(stats)} from {log_path}", flush=True)
    while True:
        started = time.time()
        try:
            payload = scan_top_markets(args, fee_cache, fee_lock)
            apply_top_market_stats(stats, payload)
            append_top_market_history(log_path, payload)
        except Exception as exc:
            payload = {
                "ts": utc_now(),
                "rows": [],
                "error": str(exc),
                "elapsed_sec": time.time() - started,
                "summary": {},
            }
            apply_top_market_stats(stats, payload)
        state.set_top_markets(payload)
        summary = payload.get("summary") or {}
        best = summary.get("history_best_edge_per_dollar")
        print(
            f"{payload.get('ts')} top_markets rows={len(payload.get('rows') or [])} "
            f"complete={summary.get('complete', 0)} history_positive={summary.get('history_positive', 0)} "
            f"history_best_edge_pct={best if best is not None else 'NA'} "
            f"elapsed={payload.get('elapsed_sec', 0.0):.2f}s",
            flush=True,
        )
        elapsed = time.time() - started
        time.sleep(max(0.5, args.interval - elapsed))


HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket NO Edge Monitor</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, sans-serif; }
    body { margin: 0; background: #111418; color: #e7edf3; }
    header { padding: 16px 20px; border-bottom: 1px solid #27313a; }
    h1 { margin: 0 0 6px; font-size: 20px; }
    .sub { color: #9fb0bf; font-size: 13px; }
    main { padding: 18px 20px; display: grid; gap: 16px; }
    .stats { display: grid; grid-template-columns: repeat(9, minmax(120px, 1fr)); gap: 10px; }
    .stat { background: #181e24; border: 1px solid #27313a; border-radius: 6px; padding: 10px; }
    .label { color: #8fa0ae; font-size: 12px; }
    .value { font-size: 20px; margin-top: 4px; }
    canvas { width: 100%; height: 440px; background: #0d1116; border: 1px solid #27313a; border-radius: 6px; }
    .chart-wrap { position: relative; }
    .tooltip {
      display: none;
      position: absolute;
      pointer-events: none;
      background: #202832;
      border: 1px solid #40505f;
      border-radius: 6px;
      padding: 8px 10px;
      color: #e7edf3;
      font-size: 12px;
      line-height: 1.45;
      box-shadow: 0 8px 24px rgba(0,0,0,.35);
      white-space: nowrap;
      z-index: 2;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 7px 8px; border-bottom: 1px solid #27313a; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    th { color: #9fb0bf; font-weight: 500; }
    .pos { color: #40d17d; }
    .neg { color: #ff6b6b; }
    .muted { color: #8fa0ae; }
    .section-title { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }
    .section-title h2 { margin: 0; font-size: 18px; }
    .top-meta { color: #9fb0bf; font-size: 13px; margin-top: 6px; }
    .market-cell { text-align: left; min-width: 320px; max-width: 540px; }
    .market-cell a { color: #e7edf3; text-decoration: none; }
    .market-cell a:hover { color: #4ea1ff; text-decoration: underline; }
    .status { color: #9fb0bf; }
    .status.complete { color: #40d17d; }
    .status.incomplete { color: #f4c95d; }
    .status.error, .status.too_many_legs, .status.too_few_legs { color: #ff9c6b; }
    @media (max-width: 900px) {
      table { display: block; overflow-x: auto; white-space: nowrap; }
    }
  </style>
</head>
<body>
  <header>
    <h1 id="title">Loading...</h1>
    <div class="sub">Updates every 5 seconds. Read-only monitor. No orders are placed.</div>
  </header>
  <main>
    <section class="stats">
      <div class="stat"><div class="label">Latest edge</div><div class="value" id="edge">-</div></div>
      <div class="stat"><div class="label">Edge Percent</div><div class="value" id="edgePct">-</div></div>
      <div class="stat"><div class="label">Legs Used</div><div class="value" id="legsUsed">-</div></div>
      <div class="stat"><div class="label">NO sum</div><div class="value" id="nosum">-</div></div>
      <div class="stat"><div class="label">Fee sum</div><div class="value" id="fee">-</div></div>
      <div class="stat"><div class="label">Receive</div><div class="value" id="receive">-</div></div>
      <div class="stat"><div class="label">Min Size</div><div class="value" id="execSize">-</div></div>
      <div class="stat"><div class="label">Profit @ Min Size</div><div class="value" id="execProfit">-</div></div>
      <div class="stat"><div class="label">Last tick</div><div class="value" id="tick">-</div></div>
    </section>
    <div class="chart-wrap">
      <canvas id="chart" width="1400" height="440"></canvas>
      <div id="tooltip" class="tooltip"></div>
    </div>
    <section>
      <table>
        <thead><tr><th>Leg</th><th>Effective NO</th><th>Size</th><th>Direct NO</th><th>YES bid</th><th>Fee</th><th>Route</th></tr></thead>
        <tbody id="legs"></tbody>
      </table>
    </section>
    <section>
      <div class="section-title">
        <h2>24h Volume Top 20 Neg-Risk Markets</h2>
        <div class="sub">Direct NO ask history; missing NO asks are ignored</div>
      </div>
      <div id="topMeta" class="top-meta">Loading top markets...</div>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Market</th>
            <th>24h Vol</th>
            <th>Legs</th>
            <th>NO Ask</th>
            <th>No Ask</th>
            <th>Samples</th>
            <th>Complete</th>
            <th>Edge &gt; 0 Count</th>
            <th>Edge Percent</th>
            <th>Best Time</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="topMarkets"></tbody>
      </table>
    </section>
  </main>
  <script>
    const chart = document.getElementById('chart');
    const ctx = chart.getContext('2d');
    const tooltip = document.getElementById('tooltip');
    let hoverPoints = [];
    function esc(x) {
      return String(x ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    }
    function fmt(x, n=6) { return x === null || x === undefined ? '-' : Number(x).toFixed(n); }
    function pct(x, n=3) { return x === null || x === undefined ? '-' : (Number(x) * 100).toFixed(n) + '%'; }
    function money(x) {
      if (x === null || x === undefined) return '-';
      return '$' + Number(x).toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    function tsLabel(ts) { return (ts || '').replace('T', ' ').replace('+00:00', ' UTC'); }
    function axisTimeLabel(ms) {
      const d = new Date(ms);
      const hh = String(d.getUTCHours()).padStart(2, '0');
      const mm = String(d.getUTCMinutes()).padStart(2, '0');
      return `${hh}:${mm} UTC`;
    }
    function draw(history) {
      ctx.clearRect(0, 0, chart.width, chart.height);
      ctx.fillStyle = '#0d1116';
      ctx.fillRect(0, 0, chart.width, chart.height);
      const padLeft = 48, padRight = 48, padTop = 48, padBottom = 70;
      const points = history.filter(p => p.edge !== null && p.edge !== undefined);
      hoverPoints = [];
      if (points.length < 1) return;
      const ys = points.map(p => p.edge);
      let minY = Math.min(...ys, 0), maxY = Math.max(...ys, 0);
      if (Math.abs(maxY - minY) < 1e-6) { maxY += 0.01; minY -= 0.01; }
      const plotW = chart.width - padLeft - padRight;
      const plotH = chart.height - padTop - padBottom;
      const x = i => padLeft + i * plotW / Math.max(1, points.length - 1);
      const y = v => chart.height - padBottom - (v - minY) * plotH / (maxY - minY);
      ctx.strokeStyle = '#2b3641';
      ctx.lineWidth = 1;
      for (let i = 0; i <= 5; i++) {
        const yy = padTop + i * plotH / 5;
        ctx.beginPath(); ctx.moveTo(padLeft, yy); ctx.lineTo(chart.width - padRight, yy); ctx.stroke();
      }
      ctx.strokeStyle = '#6d7884';
      ctx.beginPath(); ctx.moveTo(padLeft, y(0)); ctx.lineTo(chart.width - padRight, y(0)); ctx.stroke();
      const firstMs = Date.parse(points[0].ts);
      const lastMs = Date.parse(points[points.length - 1].ts);
      if (!Number.isNaN(firstMs) && !Number.isNaN(lastMs)) {
        ctx.fillStyle = '#9fb0bf';
        ctx.font = '13px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        for (let i = 0; i < 5; i++) {
          const px = padLeft + i * plotW / 4;
          const t = firstMs + (lastMs - firstMs) * i / 4;
          ctx.strokeStyle = '#2b3641';
          ctx.beginPath(); ctx.moveTo(px, chart.height - padBottom); ctx.lineTo(px, chart.height - padBottom + 6); ctx.stroke();
          ctx.fillText(axisTimeLabel(t), px, chart.height - padBottom + 12);
        }
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
      }
      ctx.strokeStyle = '#4ea1ff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      if (points.length === 1) {
        ctx.moveTo(x(0), y(points[0].edge));
        ctx.lineTo(x(0) + 0.01, y(points[0].edge));
      } else {
        ctx.moveTo(x(0), y(points[0].edge));
        for (let i = 1; i < points.length - 1; i++) {
          const xc = (x(i) + x(i + 1)) / 2;
          const yc = (y(points[i].edge) + y(points[i + 1].edge)) / 2;
          ctx.quadraticCurveTo(x(i), y(points[i].edge), xc, yc);
        }
        const lastIndex = points.length - 1;
        ctx.quadraticCurveTo(
          x(lastIndex - 1),
          y(points[lastIndex - 1].edge),
          x(lastIndex),
          y(points[lastIndex].edge)
        );
      }
      ctx.stroke();
      points.forEach((p, i) => {
        const px = x(i), py = y(p.edge);
        hoverPoints.push({ x: px, y: py, p });
      });
      ctx.fillStyle = '#9fb0bf';
      ctx.font = '13px Arial';
      ctx.textAlign = 'left';
      ctx.fillText('edge / set', padLeft, 20);
      ctx.fillText(maxY.toFixed(4), 8, y(maxY) + 4);
      ctx.fillText('0', 20, y(0) + 4);
      ctx.fillText(minY.toFixed(4), 8, y(minY) + 4);
    }
    function statusLabel(status) {
      const labels = {
        complete: '完整',
        incomplete: '报价不全',
        too_many_legs: '腿数过多',
        too_few_legs: '腿数过少',
        error: '错误'
      };
      return labels[status] || status || '-';
    }
    function renderTopMarkets(payload) {
      const meta = document.getElementById('topMeta');
      const tbody = document.getElementById('topMarkets');
      if (!payload) {
        meta.textContent = 'Waiting for first top-market scan...';
        tbody.innerHTML = '';
        return;
      }
      const summary = payload.summary || {};
      const rows = payload.rows || [];
      const bits = [
        `last: ${tsLabel(payload.ts) || '-'}`,
        `rows: ${rows.length}`,
        `complete: ${summary.complete ?? 0}`,
        `history positive: ${summary.history_positive ?? 0}`,
        `elapsed: ${fmt(payload.elapsed_sec, 2)}s`
      ];
      if (summary.history_best_title) {
        bits.push(`max edge percent: ${summary.history_best_title} (${pct(summary.history_best_edge_per_dollar)} at ${tsLabel(summary.history_best_ts)})`);
      }
      if (payload.error) {
        bits.push(`error: ${payload.error}`);
      }
      meta.textContent = bits.join(' | ');
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="12" class="muted">No top-market rows yet.</td></tr>';
        return;
      }
      tbody.innerHTML = rows.map(r => {
        const edgePercent = r.max_edge_per_dollar;
        const pctCls = edgePercent === null || edgePercent === undefined ? 'muted' : (edgePercent >= 0 ? 'pos' : 'neg');
        const url = r.slug ? `https://polymarket.com/event/${encodeURIComponent(r.slug)}` : '';
        const title = esc(r.title || r.slug || '-');
        const link = url ? `<a href="${url}" target="_blank" rel="noreferrer">${title}</a>` : title;
        const legs = r.legs === undefined ? '-' : `${r.legs}`;
        const quoted = r.quoted_legs === undefined ? '-' : `${r.quoted_legs}/${r.legs ?? '-'}`;
        const missingNoAsk = r.missing_no_ask ?? r.missing_legs ?? 0;
        const status = esc(statusLabel(r.status));
        return `
          <tr>
            <td>${r.rank ?? '-'}</td>
            <td class="market-cell">${link}</td>
            <td>${money(r.volume24hr)}</td>
            <td>${legs}</td>
            <td>${quoted}</td>
            <td class="${missingNoAsk > 0 ? 'neg' : 'muted'}">${missingNoAsk}</td>
            <td>${r.history_samples ?? 0}</td>
            <td>${r.history_complete_samples ?? 0}</td>
            <td class="${(r.positive_edge_count || 0) > 0 ? 'pos' : 'muted'}">${r.positive_edge_count ?? 0}</td>
            <td class="${pctCls}">${pct(edgePercent)}</td>
            <td>${tsLabel(r.max_edge_ts)}</td>
            <td class="status ${esc(r.status || '')}">${status}</td>
          </tr>`;
      }).join('');
    }
    chart.addEventListener('mousemove', ev => {
      const rect = chart.getBoundingClientRect();
      const sx = chart.width / rect.width;
      const sy = chart.height / rect.height;
      const mx = (ev.clientX - rect.left) * sx;
      const my = (ev.clientY - rect.top) * sy;
      let nearest = null;
      let best = Infinity;
      for (const hp of hoverPoints) {
        const d = Math.hypot(hp.x - mx, hp.y - my);
        if (d < best) { best = d; nearest = hp; }
      }
      if (!nearest || best > 22) {
        tooltip.style.display = 'none';
        return;
      }
      const p = nearest.p;
      tooltip.innerHTML = `
        <div>${tsLabel(p.ts)}</div>
        <div>edge: <b class="${p.edge >= 0 ? 'pos' : 'neg'}">${fmt(p.edge, 6)}</b></div>
        <div>edge percent: <b class="${p.edge_per_dollar === null || p.edge_per_dollar === undefined ? 'muted' : (p.edge_per_dollar >= 0 ? 'pos' : 'neg')}">${pct(p.edge_per_dollar)}</b></div>
        <div>min size: ${fmt(p.executable_size, 2)}</div>
        <div>profit @ min size: <b class="${p.executable_profit === null || p.executable_profit === undefined ? 'muted' : (p.executable_profit >= 0 ? 'pos' : 'neg')}">${fmt(p.executable_profit, 4)}</b></div>
        <div>NO sum: ${fmt(p.no_sum, 6)}</div>
        <div>fee: ${fmt(p.fee_sum, 6)}</div>
      `;
      tooltip.style.display = 'block';
      tooltip.style.left = `${Math.min(rect.width - 180, Math.max(8, ev.clientX - rect.left + 12))}px`;
      tooltip.style.top = `${Math.max(8, ev.clientY - rect.top - 58)}px`;
    });
    chart.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
    });
    async function refresh() {
      const res = await fetch('/history');
      const data = await res.json();
      const hist = data.history || [];
      const latest = data.latest || hist[hist.length - 1];
      document.getElementById('title').textContent = data.event_title || 'Polymarket NO Edge Monitor';
      if (latest) {
        const edge = latest.edge;
        const edgeEl = document.getElementById('edge');
        edgeEl.textContent = fmt(edge, 6);
        edgeEl.className = 'value ' + (edge >= 0 ? 'pos' : 'neg');
        const edgePct = latest.edge_per_dollar;
        const edgePctEl = document.getElementById('edgePct');
        edgePctEl.textContent = pct(edgePct);
        edgePctEl.className = 'value ' + (edgePct === null || edgePct === undefined ? 'muted' : (edgePct >= 0 ? 'pos' : 'neg'));
        const quotedLegs = latest.quoted_legs ?? latest.included_legs;
        const totalLegs = latest.legs;
        const legsUsedEl = document.getElementById('legsUsed');
        legsUsedEl.textContent = quotedLegs === null || quotedLegs === undefined ? '-' : `${quotedLegs}/${totalLegs ?? '-'}`;
        legsUsedEl.className = 'value ' + ((latest.missing_no_ask || 0) > 0 ? 'muted' : 'pos');
        document.getElementById('nosum').textContent = fmt(latest.no_sum, 6);
        document.getElementById('fee').textContent = fmt(latest.fee_sum, 6);
        document.getElementById('receive').textContent = fmt(latest.receive, 2);
        const execSize = latest.executable_size;
        const execSizeEl = document.getElementById('execSize');
        execSizeEl.textContent = fmt(execSize, 2);
        execSizeEl.className = 'value ' + (execSize === null || execSize === undefined ? 'muted' : '');
        const execProfit = latest.executable_profit;
        const execProfitEl = document.getElementById('execProfit');
        execProfitEl.textContent = fmt(execProfit, 4);
        execProfitEl.className = 'value ' + (execProfit === null || execProfit === undefined ? 'muted' : (execProfit >= 0 ? 'pos' : 'neg'));
        document.getElementById('tick').textContent = tsLabel(latest.ts);
        document.getElementById('legs').innerHTML = (latest.rows || []).map(r => `
          <tr>
            <td>${esc(r.title || '-')}</td>
            <td>${fmt(r.effective_no, 4)}</td>
            <td>${fmt(r.effective_size, 2)}</td>
            <td>${fmt(r.direct_no_ask, 4)}</td>
            <td>${fmt(r.yes_bid, 4)}</td>
            <td>${fmt(r.fee, 6)}</td>
            <td>${esc(r.source || '-')}</td>
          </tr>`).join('');
      }
      renderTopMarkets(data.top_markets);
      draw(hist);
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    state: State

    def send_json(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            raw = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/history":
            self.send_json(self.state.snapshot())
            return
        if path == "/health":
            snap = self.state.snapshot()
            self.send_json({"ok": True, "points": len(snap["history"]), "last_error": snap["last_error"]})
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format: str, *args: Any) -> None:
        return


def monitor_loop(args: argparse.Namespace, state: State) -> None:
    session = make_session("pmscan-exact-score-edge-monitor/0.1")
    log_path = Path(args.log)
    while True:
        try:
            title, legs = load_event(session, args.slug)
            state.set_event(title, legs)
            previous_points = load_existing_history(log_path, args.max_points)
            if previous_points:
                state.load_points(previous_points)
                print(f"loaded_history={len(previous_points)} from {log_path}", flush=True)
            break
        except Exception as exc:
            state.set_error(str(exc))
            time.sleep(args.interval)

    while True:
        started = time.time()
        try:
            point = compute_point(session, state.legs, args.workers, args.gas_buffer)
            state.add_point(point)
            with log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(point, ensure_ascii=False, separators=(",", ":")) + "\n")
            edge = point.get("edge")
            print(
                f"{point['ts']} edge={edge if edge is not None else 'NA'} "
                f"no_sum={point['no_sum']:.6f} fee={point['fee_sum']:.6f}",
                flush=True,
            )
        except Exception as exc:
            state.set_error(str(exc))
            print(f"{utc_now()} error={exc}", flush=True)
        elapsed = time.time() - started
        time.sleep(max(0.5, args.interval - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Realtime exact-score full-set NO edge monitor")
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--port", type=int, default=5188)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--gas-buffer", type=float, default=0.0)
    parser.add_argument("--max-points", type=int, default=30000)
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument("--top-log", default=str(DEFAULT_TOP_LOG))
    parser.add_argument("--top-market-count", type=int, default=20)
    parser.add_argument("--top-pages", type=int, default=10)
    parser.add_argument("--top-max-legs", type=int, default=160)
    parser.add_argument("--top-event-workers", type=int, default=4)
    parser.add_argument("--top-book-workers", type=int, default=16)
    parser.add_argument("--top-use-snapshot", action="store_true")
    parser.add_argument("--disable-top-markets", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = State(max_points=args.max_points)
    Handler.state = state
    thread = threading.Thread(target=monitor_loop, args=(args, state), daemon=True)
    thread.start()
    if not args.disable_top_markets:
        top_thread = threading.Thread(target=top_markets_loop, args=(args, state), daemon=True)
        top_thread.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard=http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
