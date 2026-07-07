# -*- coding: utf-8 -*-
"""
Scan many Polymarket neg-risk events for full-set NO taker edge.

Read-only. It never places orders.

Default quote mode:
  - direct NO asks only

Optional quote mode:
  - direct NO asks plus YES bids implied as NO at 1 - YES bid

Edge per set:
  (number of outcomes - 1) - sum(NO buy prices) - sum(taker fees) - gas buffer
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

HOST = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"


@dataclass
class Leg:
    title: str
    condition_id: str
    yes_token: str
    no_token: str
    fee_rate: float
    fee_exp: float


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


def get_json(session: requests.Session, url: str, params: dict[str, Any] | None = None, timeout: int = 15) -> Any:
    last: Exception | None = None
    for i in range(3):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            time.sleep(0.3 * (i + 1))
    raise RuntimeError(f"GET failed: {url} params={params} err={last}") from last


def sorted_asks(book: dict[str, Any]) -> list[tuple[float, float, str]]:
    rows: list[tuple[float, float, str]] = []
    for row in book.get("asks") or []:
        price = as_float(row.get("price"))
        size = as_float(row.get("size"), 0.0) or 0.0
        if price is not None and size > 0:
            rows.append((price, size, "direct_no_ask"))
    rows.sort(key=lambda item: item[0])
    return rows


def sorted_bids(book: dict[str, Any]) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for row in book.get("bids") or []:
        price = as_float(row.get("price"))
        size = as_float(row.get("size"), 0.0) or 0.0
        if price is not None and size > 0:
            rows.append((price, size))
    rows.sort(key=lambda item: item[0], reverse=True)
    return rows


def effective_no_levels(
    no_book: dict[str, Any],
    yes_book: dict[str, Any] | None,
    include_yes_bid: bool,
) -> list[tuple[float, float, str]]:
    by_price: dict[float, tuple[float, str]] = {}

    for price, size, source in sorted_asks(no_book):
        key = round(price, 6)
        old = by_price.get(key)
        if old is None or size > old[0]:
            by_price[key] = (size, source)

    if include_yes_bid and yes_book is not None:
        for yes_bid, size in sorted_bids(yes_book):
            price = round(1.0 - yes_bid, 6)
            if price <= 0 or price >= 1:
                continue
            old = by_price.get(price)
            if old is None or size > old[0]:
                by_price[price] = (size, "yes_bid_implied_no")
            elif old[1] != "yes_bid_implied_no":
                by_price[price] = (old[0], "direct_or_yes_bid")

    rows = [(price, size, source) for price, (size, source) in by_price.items()]
    rows.sort(key=lambda item: item[0])
    return rows


def consume_levels(
    levels: list[tuple[float, float, str]],
    shares: float,
    slippage: float,
) -> dict[str, Any] | None:
    if not levels:
        return None
    limit = levels[0][0] + slippage
    remaining = shares
    notional = 0.0
    used: list[dict[str, Any]] = []

    for price, size, source in levels:
        if price > limit + 1e-12:
            break
        take = min(remaining, size)
        if take <= 0:
            continue
        notional += price * take
        remaining -= take
        used.append({"price": price, "shares": take, "source": source})
        if remaining <= 1e-9:
            route_summary: dict[str, float] = {}
            for item in used:
                route_summary[item["source"]] = route_summary.get(item["source"], 0.0) + float(item["shares"])
            return {
                "shares": shares,
                "avg_price": notional / shares,
                "limit_price": price,
                "notional": notional,
                "route_summary": route_summary,
                "levels": used,
            }
    return None


def taker_fee(price: float, fee_rate: float, fee_exp: float) -> float:
    if fee_rate <= 0:
        return 0.0
    return fee_rate * ((price * (1.0 - price)) ** max(fee_exp, 1.0))


def load_events(session: requests.Session, pages: int, min_vol: float, max_events: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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
            volume = as_float(event.get("volume24hr"), 0.0) or 0.0
            if volume < min_vol:
                return out
            slug = str(event.get("slug") or event.get("id") or "")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            if not as_bool(event.get("negRisk")):
                continue
            out.append(event)
            if len(out) >= max_events:
                return out
        if len(events) < 100:
            break
    return out


def load_fee_info(session: requests.Session, condition_id: str) -> tuple[float, float, bool]:
    info = get_json(session, f"{HOST}/clob-markets/{condition_id}", timeout=10)
    fee_data = info.get("fd") or {}
    fee_rate = as_float(fee_data.get("r"), 0.0) or 0.0
    fee_exp = as_float(fee_data.get("e"), 1.0) or 1.0
    return fee_rate, fee_exp, as_bool(info.get("nr"))


def legs_from_event(session: requests.Session, event: dict[str, Any]) -> list[Leg]:
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

        condition_id = str(market.get("conditionId") or "")
        if not condition_id:
            continue
        yes_index = 1 - no_index
        try:
            fee_rate, fee_exp, supports_neg_risk = load_fee_info(session, condition_id)
        except Exception:
            continue
        if not supports_neg_risk:
            continue

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
    return legs


def fetch_books(session: requests.Session, legs: list[Leg], include_yes_bid: bool, workers: int) -> dict[str, dict[str, Any]]:
    tokens = {leg.no_token for leg in legs}
    if include_yes_bid:
        tokens.update(leg.yes_token for leg in legs)

    books: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(get_json, session, f"{HOST}/book", {"token_id": token}, 8): token
            for token in sorted(tokens)
        }
        for future in as_completed(futures):
            token = futures[future]
            try:
                books[token] = future.result()
            except Exception:
                books[token] = {}
    return books


def scan_event(session: requests.Session, event: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    title = str(event.get("title") or event.get("slug") or "")
    slug = str(event.get("slug") or "")
    volume = as_float(event.get("volume24hr"), 0.0) or 0.0
    legs = legs_from_event(session, event)
    expected = len(legs)

    if expected < 3:
        return {"status": "too_few_legs", "title": title, "slug": slug, "volume24hr": volume, "legs": expected}
    if expected > args.max_legs:
        return {"status": "too_many_legs", "title": title, "slug": slug, "volume24hr": volume, "legs": expected}

    books = fetch_books(session, legs, args.include_yes_bid, args.book_workers)
    plans: list[dict[str, Any]] = []
    no_cost = 0.0
    fee_cost = 0.0
    missing: list[str] = []

    for leg in legs:
        levels = effective_no_levels(
            books.get(leg.no_token) or {},
            books.get(leg.yes_token) or {},
            args.include_yes_bid,
        )
        plan = consume_levels(levels, args.shares, args.slippage)
        if plan is None:
            missing.append(leg.title)
            continue

        price = float(plan["avg_price"])
        fee = taker_fee(price, leg.fee_rate, leg.fee_exp)
        no_cost += price
        fee_cost += fee
        plans.append(
            {
                "title": leg.title,
                "avg_no_price": price,
                "fee": fee,
                "route_summary": plan["route_summary"],
            }
        )

    complete = len(missing) == 0
    receive = expected - 1.0
    edge = receive - no_cost - fee_cost - args.gas_buffer if complete else None
    notional = (no_cost + fee_cost) * args.shares if complete else None
    edge_usd = edge * args.shares if edge is not None else None

    return {
        "status": "complete" if complete else "incomplete",
        "title": title,
        "slug": slug,
        "volume24hr": volume,
        "legs": expected,
        "quoted_legs": len(plans),
        "missing_legs": len(missing),
        "missing_examples": missing[:8],
        "receive_per_set": receive,
        "no_cost_per_set": no_cost if complete else None,
        "fee_per_set": fee_cost if complete else None,
        "gas_buffer_per_set": args.gas_buffer,
        "edge_per_set": edge,
        "edge_usd": edge_usd,
        "notional": notional,
        "plans": plans if args.include_plans else [],
    }


def print_summary(result: dict[str, Any], top: int) -> None:
    summary = result["summary"]
    print("\n=== Scan summary ===")
    print(f"mode: {summary['quote_mode']}")
    print(f"events scanned: {summary['events_scanned']}")
    print(f"complete markets: {summary['complete']}")
    print(f"incomplete markets: {summary['incomplete']}")
    print(f"positive edge > 0: {summary['positive_edge_gt_0']}")
    print(f"positive edge >= min_edge: {summary['positive_edge_ge_min']}")
    print(f"report: {result['report_path']}")

    complete = [row for row in result["markets"] if row["status"] == "complete"]
    complete.sort(key=lambda row: row["edge_per_set"], reverse=True)
    print("\n=== Top complete markets ===")
    if not complete:
        print("No complete markets.")
        return
    for row in complete[:top]:
        edge = row["edge_per_set"]
        print(
            f"edge={edge: .6f} raw={row['no_cost_per_set']:.6f} fee={row['fee_per_set']:.6f} "
            f"N={row['legs']:>3} vol=${row['volume24hr']:,.0f} {row['title'][:88]}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan many Polymarket neg-risk events for full-set NO edge")
    parser.add_argument("--min-vol", type=float, default=10_000.0)
    parser.add_argument("--pages", type=int, default=80)
    parser.add_argument("--max-events", type=int, default=10_000)
    parser.add_argument("--max-legs", type=int, default=120)
    parser.add_argument("--shares", type=float, default=1.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--gas-buffer", type=float, default=0.0)
    parser.add_argument("--min-edge", type=float, default=0.01)
    parser.add_argument("--book-workers", type=int, default=32)
    parser.add_argument("--event-workers", type=int, default=8)
    parser.add_argument("--include-yes-bid", action="store_true")
    parser.add_argument("--include-plans", action="store_true")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", default="")
    return parser


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    args = build_parser().parse_args()
    if args.shares <= 0:
        raise SystemExit("--shares must be positive")

    session = requests.Session()
    session.headers.update({"User-Agent": "pmscan-multi-market-edge-scanner/0.1"})

    events = load_events(session, args.pages, args.min_vol, args.max_events)
    print(f"loaded neg-risk events: {len(events)}")

    markets: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.event_workers)) as executor:
        futures = {executor.submit(scan_event, session, event, args): event for event in events}
        for idx, future in enumerate(as_completed(futures), 1):
            try:
                markets.append(future.result())
            except Exception as exc:
                event = futures[future]
                markets.append(
                    {
                        "status": "error",
                        "title": str(event.get("title") or event.get("slug") or ""),
                        "slug": str(event.get("slug") or ""),
                        "error": str(exc),
                    }
                )
            if idx % 25 == 0:
                print(f"processed {idx}/{len(events)}")

    complete = [row for row in markets if row["status"] == "complete"]
    incomplete = [row for row in markets if row["status"] == "incomplete"]
    positives = [row for row in complete if (row.get("edge_per_set") or 0.0) > 0]
    positives_min = [row for row in complete if (row.get("edge_per_set") or 0.0) >= args.min_edge]

    quote_mode = "direct NO asks + YES bid implied NO" if args.include_yes_bid else "direct NO asks only"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.out) if args.out else REPORT_DIR / f"multi_market_edge_scan_{timestamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "generated_at": utc_now(),
        "report_path": str(report_path),
        "params": vars(args),
        "summary": {
            "quote_mode": quote_mode,
            "events_scanned": len(events),
            "complete": len(complete),
            "incomplete": len(incomplete),
            "positive_edge_gt_0": len(positives),
            "positive_edge_ge_min": len(positives_min),
            "min_edge": args.min_edge,
        },
        "markets": sorted(
            markets,
            key=lambda row: (
                row["status"] != "complete",
                -1.0 * (row.get("edge_per_set") if row.get("edge_per_set") is not None else -999.0),
            ),
        ),
    }

    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(result, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

