# -*- coding: utf-8 -*-
"""
Fee-aware neg-risk taker test engine.

Default mode is scan/paper only. Live orders require both:
  --live --confirm-live YES

This first live version is deliberately conservative:
  - Uses Polymarket CLOB V2 SDK only.
  - Buys NO with FOK marketable limit orders.
  - Requires positive cash edge after current taker fee estimates.
  - Does not auto-send convert/merge adapter transactions yet.

Run examples:
  python bot/p3nny_taker_engine.py --once --events 5 --clip 1
  python bot/p3nny_taker_engine.py --cycles 20 --sleep 3 --events 10 --clip 1
  python bot/p3nny_taker_engine.py --live --confirm-live YES --once --events 3 --clip 1 --max-notional 5
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

HOST = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CHAIN_ID = 137

HERE = Path(__file__).resolve().parent
DEFAULT_ENV = HERE / ".env"
DEFAULT_LOG = HERE / "p3nny_taker_engine_log.jsonl"

MIN_P = 0.001
MAX_P = 0.999


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_float(x: Any, default: float | None = None) -> float | None:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def as_bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    return str(x).lower() in {"1", "true", "yes", "y"}


def jloads(x: Any, default: Any) -> Any:
    if x is None:
        return default
    if isinstance(x, (list, dict)):
        return x
    try:
        return json.loads(x)
    except Exception:
        return default


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def http_get_json(session: requests.Session, url: str, params: dict[str, Any] | None = None) -> Any:
    last: Exception | None = None
    for i in range(3):
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 404:
                raise RuntimeError(f"404: {url} params={params}")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.35 * (i + 1))
    raise RuntimeError(f"GET failed: {url} params={params} err={last}") from last


def fetch_book_quick(session: requests.Session, token_id: str) -> dict[str, Any] | None:
    try:
        r = session.get(f"{HOST}/book", params={"token_id": token_id}, timeout=5)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_clob_market_quick(session: requests.Session, condition_id: str) -> dict[str, Any] | None:
    try:
        r = session.get(f"{HOST}/clob-markets/{condition_id}", timeout=5)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def taker_fee_per_share(price: float, fee_rate: float, fee_exp: float) -> float:
    if fee_rate <= 0:
        return 0.0
    return fee_rate * ((price * (1.0 - price)) ** max(fee_exp, 1.0))


def round_up_tick(price: float, tick: float) -> float:
    if tick <= 0:
        tick = 0.001
    return min(MAX_P, math.ceil((price - 1e-12) / tick) * tick)


def sort_asks(book: dict[str, Any]) -> list[tuple[float, float, str]]:
    asks = book.get("asks") or []
    out: list[tuple[float, float, str]] = []
    for row in asks:
        p = as_float(row.get("price"))
        s = as_float(row.get("size"))
        if p is not None and s is not None and s > 0:
            out.append((p, s, "direct_no_ask"))
    out.sort(key=lambda x: x[0])
    return out


def sort_bids(book: dict[str, Any]) -> list[tuple[float, float]]:
    bids = book.get("bids") or []
    out: list[tuple[float, float]] = []
    for row in bids:
        p = as_float(row.get("price"))
        s = as_float(row.get("size"))
        if p is not None and s is not None and s > 0:
            out.append((p, s))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def effective_no_asks(
    no_book: dict[str, Any] | None,
    yes_book: dict[str, Any] | None,
) -> list[tuple[float, float, str]]:
    # A YES bid implies equivalent taker NO liquidity at 1 - YES bid.
    # At the same effective price this may be a mirrored order, so keep max size.
    by_price: dict[float, tuple[float, str]] = {}
    for price, size, source in sort_asks(no_book or {}):
        key = round(price, 6)
        prev = by_price.get(key)
        if prev is None or size > prev[0]:
            by_price[key] = (size, source)
    for yes_bid, size in sort_bids(yes_book or {}):
        price = round(1.0 - yes_bid, 6)
        if price <= 0 or price >= 1:
            continue
        prev = by_price.get(price)
        source = "synthetic_from_yes_bid"
        if prev is None or size > prev[0]:
            by_price[price] = (size, source)
        elif prev is not None and prev[1] != source:
            by_price[price] = (prev[0], "direct_or_synthetic")
    out = [(price, size, source) for price, (size, source) in by_price.items()]
    out.sort(key=lambda x: x[0])
    return out


def consume_asks(asks: list[tuple[float, float, str]], shares: float, max_price: float) -> dict[str, Any] | None:
    need = shares
    notional = 0.0
    used: list[dict[str, Any]] = []
    for price, size, source in asks:
        if price > max_price + 1e-12:
            break
        take = min(need, size)
        if take <= 0:
            continue
        used.append({"price": price, "shares": take, "source": source})
        notional += price * take
        need -= take
        if need <= 1e-9:
            avg = notional / shares
            route_summary: dict[str, float] = {}
            for level in used:
                route_summary[level["source"]] = route_summary.get(level["source"], 0.0) + float(level["shares"])
            return {
                "shares": shares,
                "notional": notional,
                "avg_price": avg,
                "limit_price": price,
                "levels": used,
                "route_summary": route_summary,
            }
    return None


@dataclass
class Leg:
    title: str
    condition_id: str
    yes_token: str
    no_token: str
    tick: float
    min_order_size: float
    fee_rate: float
    fee_exp: float
    no_asks: list[tuple[float, float, str]] = field(default_factory=list)


@dataclass
class Candidate:
    kind: str
    event_title: str
    event_slug: str
    legs: list[Leg]
    plans: list[dict[str, Any]]
    shares: float
    raw_cost_per_set: float
    fee_per_set: float
    gas_buffer_per_set: float
    net_edge_per_set: float
    net_edge_usd: float
    max_notional: float
    confidence: str
    note: str

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "event_title": self.event_title,
            "event_slug": self.event_slug,
            "legs": [
                {
                    "title": l.title,
                    "condition_id": l.condition_id,
                    "no_token": l.no_token,
                    "fee_rate": l.fee_rate,
                    "fee_exp": l.fee_exp,
                }
                for l in self.legs
            ],
            "plans": self.plans,
            "shares": self.shares,
            "raw_cost_per_set": self.raw_cost_per_set,
            "fee_per_set": self.fee_per_set,
            "gas_buffer_per_set": self.gas_buffer_per_set,
            "net_edge_per_set": self.net_edge_per_set,
            "net_edge_usd": self.net_edge_usd,
            "max_notional": self.max_notional,
            "confidence": self.confidence,
            "note": self.note,
        }


class Engine:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "pmscan-p3nny-taker-engine/0.1"})
        self._clob_public = None
        self._clob_private = None
        self._fee_cache: dict[str, tuple[float, float, float, float, bool]] = {}

    def clob_public(self):
        if self._clob_public is None:
            from py_clob_client_v2.client import ClobClient

            self._clob_public = ClobClient(HOST, chain_id=CHAIN_ID)
        return self._clob_public

    def clob_private(self):
        if self._clob_private is not None:
            return self._clob_private

        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds

        env = load_env(Path(self.args.env))
        key = env.get("PK") or env.get("PRIVATE_KEY") or os.getenv("PRIVATE_KEY") or os.getenv("PK")
        funder = env.get("FUNDER") or env.get("DEPOSIT_WALLET_ADDRESS") or os.getenv("FUNDER")
        sig_type = int(env.get("SIG_TYPE") or os.getenv("SIG_TYPE") or "3")
        if not key or key.startswith("0xYOUR"):
            raise RuntimeError(f"missing PK in {self.args.env}")

        creds = None
        api_key = env.get("CLOB_API_KEY") or os.getenv("CLOB_API_KEY")
        secret = env.get("CLOB_SECRET") or os.getenv("CLOB_SECRET")
        passphrase = env.get("CLOB_PASSPHRASE") or os.getenv("CLOB_PASSPHRASE")
        if api_key and secret and passphrase:
            creds = ApiCreds(api_key=api_key, api_secret=secret, api_passphrase=passphrase)

        kwargs: dict[str, Any] = {
            "host": HOST,
            "chain_id": CHAIN_ID,
            "key": key,
            "creds": creds,
            "signature_type": sig_type,
            "use_server_time": True,
            "retry_on_error": True,
            "fee_slippage": self.args.fee_slippage,
        }
        if sig_type != 0:
            if not funder:
                raise RuntimeError(f"missing FUNDER in {self.args.env} for SIG_TYPE={sig_type}")
            kwargs["funder"] = funder

        client = ClobClient(**kwargs)
        if creds is None:
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            print("已生成/取回 CLOB API 凭证。请把下面三项写回 .env，避免每次重新派生：")
            print(f"CLOB_API_KEY={creds.api_key}")
            print(f"CLOB_SECRET={creds.api_secret}")
            print(f"CLOB_PASSPHRASE={creds.api_passphrase}")

        self._clob_private = client
        return client

    def fetch_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        pages = max(1, self.args.pages)
        for page in range(pages):
            data = http_get_json(
                self.session,
                f"{GAMMA}/events",
                {
                    "closed": "false",
                    "active": "true",
                    "limit": 100,
                    "offset": page * 100,
                    "order": "volume24hr",
                    "ascending": "false",
                },
            )
            if not data:
                break
            for event in data:
                vol = as_float(event.get("volume24hr"), 0.0) or 0.0
                if self.args.all_above_min_vol and vol < self.args.min_vol:
                    return out
                slug = str(event.get("slug") or event.get("id") or "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                if not as_bool(event.get("negRisk")):
                    continue
                if vol < self.args.min_vol:
                    continue
                out.append(event)
                if len(out) >= self.args.events:
                    return out
            if len(data) < 100:
                break
        return out

    def fee_info(self, condition_id: str, no_token: str) -> tuple[float, float, float, float, bool]:
        cached = self._fee_cache.get(condition_id)
        if cached:
            return cached
        info = fetch_clob_market_quick(self.session, condition_id)
        if not info or not info.get("t"):
            raise RuntimeError(f"failed to fetch clob market info for {condition_id}")
        fd = info.get("fd") or {}
        tick = as_float(info.get("mts"), 0.001) or 0.001
        min_size = as_float(info.get("mos"), 1.0) or 1.0
        fee_rate = as_float(fd.get("r"), 0.0) or 0.0
        fee_exp = as_float(fd.get("e"), 1.0) or 1.0
        neg_risk = as_bool(info.get("nr"))
        result = (tick, min_size, fee_rate, fee_exp, neg_risk)
        self._fee_cache[condition_id] = result
        return result

    def legs_from_event(self, event: dict[str, Any]) -> list[Leg]:
        legs: list[Leg] = []
        for market in event.get("markets") or []:
            if not (as_bool(market.get("acceptingOrders")) and as_bool(market.get("enableOrderBook"))):
                continue
            outcomes = jloads(market.get("outcomes"), [])
            tokens = jloads(market.get("clobTokenIds"), [])
            if len(outcomes) != 2 or len(tokens) != 2:
                continue
            no_idx = None
            for i, outcome in enumerate(outcomes):
                if str(outcome).strip().lower() == "no":
                    no_idx = i
                    break
            if no_idx is None:
                continue
            yes_idx = 1 - no_idx
            condition_id = str(market.get("conditionId") or "")
            if not condition_id:
                continue
            no_token = str(tokens[no_idx])
            yes_token = str(tokens[yes_idx])
            tick = as_float(market.get("orderPriceMinTickSize"), 0.001) or 0.001
            min_size = as_float(market.get("orderMinSize"), 1.0) or 1.0
            title = (
                market.get("groupItemTitle")
                or market.get("question")
                or market.get("slug")
                or condition_id
            )
            legs.append(
                Leg(
                    title=str(title),
                    condition_id=condition_id,
                    yes_token=yes_token,
                    no_token=no_token,
                    tick=tick,
                    min_order_size=min_size,
                    fee_rate=0.0,
                    fee_exp=1.0,
                )
            )
        return legs

    def attach_books(self, legs: list[Leg]) -> list[Leg]:
        book_ok: list[Leg] = []
        books: dict[str, dict[str, Any]] = {}
        tokens = sorted({token for leg in legs for token in (leg.no_token, leg.yes_token) if token})
        with ThreadPoolExecutor(max_workers=max(1, self.args.book_workers)) as ex:
            futs = {ex.submit(fetch_book_quick, self.session, token): token for token in tokens}
            for fut in as_completed(futs):
                token = futs[fut]
                book = fut.result()
                if book:
                    books[token] = book

        for leg in legs:
            leg.no_asks = effective_no_asks(books.get(leg.no_token), books.get(leg.yes_token))
            if leg.no_asks:
                book_ok.append(leg)

        ok: list[Leg] = []
        info_map: dict[str, tuple[float, float, float, float, bool]] = {}
        with ThreadPoolExecutor(max_workers=max(1, self.args.info_workers)) as ex:
            futs = {
                ex.submit(self.fee_info, leg.condition_id, leg.no_token): leg
                for leg in book_ok
            }
            for fut in as_completed(futs):
                leg = futs[fut]
                try:
                    info_map[leg.condition_id] = fut.result()
                except Exception as exc:
                    if self.args.verbose:
                        print(f"跳过费率拉取失败 token={leg.no_token[:10]}: {exc}")

        for leg in book_ok:
            try:
                if leg.condition_id not in info_map:
                    continue
                tick, min_size, fee_rate, fee_exp, neg_risk = info_map[leg.condition_id]
                if not neg_risk:
                    continue
                leg.tick = tick
                leg.min_order_size = min_size
                leg.fee_rate = fee_rate
                leg.fee_exp = fee_exp
                ok.append(leg)
            except Exception as exc:
                if self.args.verbose:
                    print(f"跳过盘口/费率拉取失败 token={leg.no_token[:10]}: {exc}")
        return ok

    def full_set_candidate(self, event: dict[str, Any], legs: list[Leg], expected_n: int) -> Candidate | None:
        n = len(legs)
        if n < 3 or n > self.args.max_n:
            return None
        if n != expected_n:
            return None

        shares = self.args.clip
        min_size = max([l.min_order_size for l in legs] + [self.args.min_shares])
        if shares < min_size:
            shares = min_size

        plans: list[dict[str, Any]] = []
        raw_cost = 0.0
        fee_cost = 0.0
        max_notional = 0.0
        for leg in legs:
            best = leg.no_asks[0][0] if leg.no_asks else None
            if best is None:
                return None
            limit_price = round_up_tick(best + self.args.slippage, leg.tick)
            plan = consume_asks(leg.no_asks, shares, limit_price)
            if plan is None:
                return None
            price = float(plan["avg_price"])
            fee = taker_fee_per_share(price, leg.fee_rate, leg.fee_exp)
            raw_cost += price
            fee_cost += fee
            max_notional += float(plan["notional"]) + fee * shares
            plans.append(
                {
                    "leg": leg.title,
                    "condition_id": leg.condition_id,
                    "no_token": leg.no_token,
                    "shares": shares,
                    "avg_price": price,
                    "limit_price": float(plan["limit_price"]),
                    "notional": float(plan["notional"]),
                    "fee_per_share": fee,
                    "fee_usd": fee * shares,
                    "route_summary": plan.get("route_summary", {}),
                }
            )

        gas = self.args.gas_buffer
        net_edge = (n - 1.0) - raw_cost - fee_cost - gas
        net_usd = net_edge * shares
        if net_edge < self.args.min_edge:
            return None
        if max_notional > self.args.max_notional:
            return None
        if max_notional > 0 and net_usd / max_notional < self.args.min_edge_pct:
            return None
        return Candidate(
            kind="full_set_no_convert",
            event_title=str(event.get("title") or event.get("slug") or ""),
            event_slug=str(event.get("slug") or ""),
            legs=legs,
            plans=plans,
            shares=shares,
            raw_cost_per_set=raw_cost,
            fee_per_set=fee_cost,
            gas_buffer_per_set=gas,
            net_edge_per_set=net_edge,
            net_edge_usd=net_usd,
            max_notional=max_notional,
            confidence="中高：真实盘口+动态fee；但多腿下单不是原子交易，convert未自动执行",
            note="全套 NO 到手后，理论上可 full-set convert 释放 (N-1)*q collateral。",
        )

    def subset_paper_candidates(self, event: dict[str, Any], legs: list[Leg]) -> list[Candidate]:
        if not self.args.show_subset:
            return []
        n = len(legs)
        if n < 3 or n > self.args.max_n:
            return []
        pool = sorted(legs, key=lambda l: l.no_asks[0][0] if l.no_asks else 9.0)[: self.args.subset_pool]
        out: list[Candidate] = []
        max_k = min(self.args.max_subset_k, len(pool))
        for k in range(2, max_k + 1):
            for subset in itertools.combinations(pool, k):
                shares = self.args.clip
                plans: list[dict[str, Any]] = []
                raw_cost = 0.0
                fee_cost = 0.0
                max_notional = 0.0
                ok = True
                for leg in subset:
                    best = leg.no_asks[0][0] if leg.no_asks else None
                    if best is None:
                        ok = False
                        break
                    limit_price = round_up_tick(best + self.args.slippage, leg.tick)
                    plan = consume_asks(leg.no_asks, shares, limit_price)
                    if plan is None:
                        ok = False
                        break
                    price = float(plan["avg_price"])
                    fee = taker_fee_per_share(price, leg.fee_rate, leg.fee_exp)
                    raw_cost += price
                    fee_cost += fee
                    max_notional += float(plan["notional"]) + fee * shares
                    plans.append(
                        {
                            "leg": leg.title,
                            "condition_id": leg.condition_id,
                            "no_token": leg.no_token,
                            "shares": shares,
                            "avg_price": price,
                            "limit_price": float(plan["limit_price"]),
                            "notional": float(plan["notional"]),
                            "fee_per_share": fee,
                            "fee_usd": fee * shares,
                            "route_summary": plan.get("route_summary", {}),
                        }
                    )
                if not ok:
                    continue
                net_edge = (k - 1.0) - raw_cost - fee_cost - self.args.gas_buffer
                if net_edge < self.args.subset_cash_edge:
                    continue
                out.append(
                    Candidate(
                        kind="subset_no_convert_paper",
                        event_title=str(event.get("title") or event.get("slug") or ""),
                        event_slug=str(event.get("slug") or ""),
                        legs=list(subset),
                        plans=plans,
                        shares=shares,
                        raw_cost_per_set=raw_cost,
                        fee_per_set=fee_cost,
                        gas_buffer_per_set=self.args.gas_buffer,
                        net_edge_per_set=net_edge,
                        net_edge_usd=net_edge * shares,
                        max_notional=max_notional,
                        confidence="中：仅按现金腿计算；未给生成 YES 包估值",
                        note="subset 只用于观察。若不显式给 YES 包估值，实盘默认不买。",
                    )
                )
        out.sort(key=lambda c: c.net_edge_usd, reverse=True)
        return out[: self.args.max_subset_print]

    def scan_once(self) -> list[Candidate]:
        events = self.fetch_events()
        candidates: list[Candidate] = []
        for event in events:
            parsed_legs = self.legs_from_event(event)
            legs = self.attach_books(parsed_legs)
            if len(legs) < 3:
                continue
            c = self.full_set_candidate(event, legs, len(parsed_legs))
            if c:
                candidates.append(c)
            candidates.extend(self.subset_paper_candidates(event, legs))
        candidates.sort(key=lambda c: c.net_edge_usd, reverse=True)
        return candidates[: self.args.print_top]

    def print_candidates(self, candidates: list[Candidate]) -> None:
        print(f"\n[{utc_now()}] 候选 {len(candidates)} 个")
        if not candidates:
            print("没有发现满足阈值的费后 taker 机会。")
            return
        for i, c in enumerate(candidates, 1):
            print(
                f"\n#{i} {c.kind} | {c.event_title[:72]} | N={len(c.legs)} q={c.shares:g}"
            )
            print(
                f"  raw_cost={c.raw_cost_per_set:.6f} fee={c.fee_per_set:.6f} "
                f"gas_buf={c.gas_buffer_per_set:.6f} net_edge={c.net_edge_per_set:.6f}/set "
                f"net=${c.net_edge_usd:.4f} notional<=${c.max_notional:.4f}"
            )
            print(f"  可信度: {c.confidence}")
            for p in c.plans[: min(8, len(c.plans))]:
                print(
                    f"  BUY NO {p['leg'][:36]:<36} {p['shares']:>8.4g} "
                    f"avg={p['avg_price']:.4f} limit={p['limit_price']:.4f} "
                    f"fee=${p['fee_usd']:.4f} route={p.get('route_summary', {})}"
                )
            if len(c.plans) > 8:
                print(f"  ... 另有 {len(c.plans) - 8} 腿")

    def append_log(self, record: dict[str, Any]) -> None:
        path = Path(self.args.log)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def place_candidate(self, candidate: Candidate) -> None:
        if not self.args.live:
            return
        if self.args.confirm_live != "YES":
            raise RuntimeError("live requires --confirm-live YES")
        if candidate.kind != "full_set_no_convert":
            raise RuntimeError(f"live trading disabled for kind={candidate.kind}")
        if candidate.max_notional > self.args.max_notional:
            raise RuntimeError("candidate exceeds max notional")
        if not self.args.allow_synthetic_live:
            synthetic = [
                p["leg"]
                for p in candidate.plans
                if (p.get("route_summary") or {}).get("synthetic_from_yes_bid", 0) > 0
            ]
            if synthetic:
                raise RuntimeError(
                    "live blocked: candidate uses YES-bid-derived NO liquidity; rerun with "
                    "--allow-synthetic-live only after execution-path validation"
                )

        from py_clob_client_v2.clob_types import (
            AssetType,
            BalanceAllowanceParams,
            OrderArgsV2,
            OrderType,
            PartialCreateOrderOptions,
        )
        from py_clob_client_v2.order_builder.constants import BUY

        client = self.clob_private()
        if not self.args.skip_balance_check:
            try:
                bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                print(f"余额/授权检查: {bal}")
            except Exception as exc:
                raise RuntimeError(f"balance/allowance check failed: {exc}") from exc

        placed: list[dict[str, Any]] = []
        print("\n*** LIVE 下单开始：FOK 多腿不是原子交易，失败腿不会自动回滚已成交腿 ***")
        for leg, plan in zip(candidate.legs, candidate.plans):
            args = OrderArgsV2(
                token_id=plan["no_token"],
                price=float(plan["limit_price"]),
                size=float(plan["shares"]),
                side=BUY,
            )
            opts = PartialCreateOrderOptions(tick_size=str(leg.tick), neg_risk=True)
            before = time.time()
            try:
                resp = client.create_and_post_order(args, opts, OrderType.FOK)
            except Exception as exc:
                resp = {"success": False, "error": str(exc)}
            item = {
                "ts": utc_now(),
                "elapsed_ms": round((time.time() - before) * 1000, 1),
                "leg": plan["leg"],
                "token": plan["no_token"],
                "shares": plan["shares"],
                "limit_price": plan["limit_price"],
                "response": resp,
            }
            placed.append(item)
            print(f"  {plan['leg'][:36]:<36} {plan['shares']:g}@{plan['limit_price']:.4f} -> {resp}")
            self.append_log({"type": "live_order_response", **item})

        self.append_log({"type": "live_candidate_done", "ts": utc_now(), "candidate": candidate.to_json(), "orders": placed})
        print("\nLIVE 下单结束。请立刻在 Polymarket/链上核对成交和持仓；本脚本本版不会自动 convert/merge。")

    def run(self) -> None:
        cycles = 1 if self.args.once else self.args.cycles
        for n in range(cycles):
            candidates = self.scan_once()
            self.print_candidates(candidates)
            self.append_log(
                {
                    "type": "scan",
                    "ts": utc_now(),
                    "cycle": n + 1,
                    "candidates": [c.to_json() for c in candidates],
                }
            )
            live_candidates = [c for c in candidates if c.kind == "full_set_no_convert"]
            if self.args.live and live_candidates:
                self.place_candidate(live_candidates[0])
                if self.args.stop_after_live:
                    return
            if n < cycles - 1:
                time.sleep(self.args.sleep)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Polymarket neg-risk fee-aware taker test engine")
    p.add_argument("--once", action="store_true", help="run one scan cycle")
    p.add_argument("--cycles", type=int, default=30)
    p.add_argument("--sleep", type=float, default=3.0)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--events", type=int, default=10, help="max neg-risk events to scan per cycle")
    p.add_argument("--all-above-min-vol", action="store_true", help="scan every active event above --min-vol")
    p.add_argument("--book-workers", type=int, default=12)
    p.add_argument("--info-workers", type=int, default=16)
    p.add_argument("--print-top", type=int, default=8)
    p.add_argument("--min-vol", type=float, default=5000.0)
    p.add_argument("--max-n", type=int, default=18)
    p.add_argument("--clip", type=float, default=1.0)
    p.add_argument("--min-shares", type=float, default=1.0)
    p.add_argument("--max-notional", type=float, default=10.0)
    p.add_argument("--slippage", type=float, default=0.001)
    p.add_argument("--min-edge", type=float, default=0.003)
    p.add_argument("--min-edge-pct", type=float, default=0.0005)
    p.add_argument("--gas-buffer", type=float, default=0.0001, help="per-set buffer, not total gas")
    p.add_argument("--fee-slippage", type=float, default=0.0)
    p.add_argument("--show-subset", action="store_true", help="print cash-positive subset paper candidates")
    p.add_argument("--subset-cash-edge", type=float, default=0.003)
    p.add_argument("--subset-pool", type=int, default=8)
    p.add_argument("--max-subset-k", type=int, default=4)
    p.add_argument("--max-subset-print", type=int, default=3)
    p.add_argument("--live", action="store_true")
    p.add_argument("--confirm-live", default="")
    p.add_argument("--allow-synthetic-live", action="store_true")
    p.add_argument("--skip-balance-check", action="store_true")
    p.add_argument("--stop-after-live", action="store_true", default=True)
    p.add_argument("--env", default=str(DEFAULT_ENV))
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    args = build_parser().parse_args()
    if args.events and args.print_top == 8:
        args.print_top = args.events
    if args.live:
        print("LIVE 模式已请求。只有 --confirm-live YES 同时存在才会下单。")
    try:
        Engine(args).run()
    except KeyboardInterrupt:
        print("\n已停止。")
        return 130
    except Exception as exc:
        print(f"\n错误: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
