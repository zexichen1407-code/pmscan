# -*- coding: utf-8 -*-
"""
Read-only p3nny recent-market edge monitor.

It reuses the existing NO edge dashboard from arg_egy_exact_score_edge_monitor.py,
but changes the target selection:
  1. Pull p3nny/e46m3 latest trades.
  2. Monitor the latest N distinct neg-risk events he traded.
  3. Draw the curve for the current best-edge event.
  4. If p3nny has more than K BUY trades in one event within W seconds,
     switch the curve to that triggered event.

No orders are placed. No convert/merge is sent.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import arg_egy_exact_score_edge_monitor as edgeui


WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
HERE = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = HERE / "penny_recent_edge_logs"
DEFAULT_TOP_LOG = HERE / "penny_recent_20_edge_history.jsonl"


edgeui.HTML = edgeui.HTML.replace(
    "24h Volume Top 20 Neg-Risk Markets",
    "p3nny Recent 20 Traded Neg-Risk Markets",
).replace(
    "Direct NO ask history; missing NO asks are ignored",
    "Same table layout; target set is p3nny's latest traded events",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_name(slug: str) -> str:
    out: list[str] = []
    for ch in slug:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)[:120] or "unknown"


def make_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def fetch_latest_trades(session: requests.Session, wallet: str, limit: int) -> list[dict[str, Any]]:
    params = {"user": wallet, "limit": limit, "offset": 0}
    rows = edgeui.get_json(session, f"{DATA_API}/trades", params, timeout=30)
    if not isinstance(rows, list):
        return []
    out = [row for row in rows if isinstance(row, dict)]
    out.sort(key=lambda row: as_int(row.get("timestamp")), reverse=True)
    return out


def event_slug_from_trade(row: dict[str, Any]) -> str:
    return str(row.get("eventSlug") or row.get("event_slug") or row.get("slug") or "").strip()


def latest_distinct_event_slugs(rows: list[dict[str, Any]], count: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        slug = event_slug_from_trade(row)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
        if len(out) >= count:
            break
    return out


def load_event_by_slug(session: requests.Session, slug: str) -> dict[str, Any] | None:
    events = edgeui.get_json(session, f"{GAMMA_API}/events", {"slug": slug}, timeout=20)
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0]
    if isinstance(events, dict):
        return events
    return None


def load_recent_events(session: requests.Session, slugs: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for slug in slugs:
        try:
            event = load_event_by_slug(session, slug)
        except Exception:
            continue
        if not event:
            continue
        if not as_bool(event.get("negRisk")):
            continue
        events.append(event)
    return events


@dataclass
class Trigger:
    event_slug: str
    count: int
    first_ts: int
    last_ts: int
    notional: float
    title: str


def find_buy_trigger(rows: list[dict[str, Any]], window_seconds: int, threshold: int) -> Trigger | None:
    cutoff = int(time.time()) - window_seconds
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("side") or "").upper() != "BUY":
            continue
        ts = as_int(row.get("timestamp"))
        if ts < cutoff:
            continue
        slug = event_slug_from_trade(row)
        if not slug:
            continue
        grouped[slug].append(row)

    triggers: list[Trigger] = []
    for slug, event_rows in grouped.items():
        if len(event_rows) <= threshold:
            continue
        timestamps = [as_int(row.get("timestamp")) for row in event_rows]
        notional = sum(as_float(row.get("price")) * as_float(row.get("size")) for row in event_rows)
        triggers.append(
            Trigger(
                event_slug=slug,
                count=len(event_rows),
                first_ts=min(timestamps),
                last_ts=max(timestamps),
                notional=notional,
                title=str(event_rows[0].get("title") or slug),
            )
        )
    if not triggers:
        return None
    triggers.sort(key=lambda item: (item.count, item.notional, item.last_ts), reverse=True)
    return triggers[0]


def scan_penny_recent_markets(
    args: argparse.Namespace,
    trades: list[dict[str, Any]],
    fee_cache: dict[str, tuple[float, float, bool]],
    fee_lock: threading.Lock,
) -> dict[str, Any]:
    started = time.time()
    session = make_session("pmscan-penny-recent-events/0.1")
    slugs = latest_distinct_event_slugs(trades, args.market_count)
    events = load_recent_events(session, slugs)
    slug_to_recent_rank = {slug: i + 1 for i, slug in enumerate(slugs)}

    rows: list[dict[str, Any]] = []
    with edgeui.ThreadPoolExecutor(max_workers=max(1, args.top_event_workers)) as executor:
        futures = {
            executor.submit(
                edgeui.scan_top_market,
                slug_to_recent_rank.get(str(event.get("slug") or ""), i + 1),
                event,
                args,
                fee_cache,
                fee_lock,
            ): event
            for i, event in enumerate(events)
        }
        for future in edgeui.as_completed(futures):
            rows.append(future.result())

    rows.sort(key=lambda item: item.get("rank", 999999))
    complete = [row for row in rows if row.get("status") == "complete" and row.get("edge") is not None]
    positive = [row for row in complete if (row.get("edge") or 0.0) > 0]
    best = max(complete, key=lambda item: item.get("edge") or -999999.0) if complete else None
    trigger = find_buy_trigger(trades, args.trigger_window_seconds, args.trigger_threshold)

    return {
        "ts": utc_now(),
        "rows": rows,
        "error": None,
        "elapsed_sec": time.time() - started,
        "trigger": None
        if trigger is None
        else {
            "event_slug": trigger.event_slug,
            "count": trigger.count,
            "first_ts": trigger.first_ts,
            "last_ts": trigger.last_ts,
            "notional": trigger.notional,
            "title": trigger.title,
        },
        "summary": {
            "requested": args.market_count,
            "found": len(events),
            "complete": len(complete),
            "positive": len(positive),
            "best_edge": None if best is None else best.get("edge"),
            "best_title": None if best is None else best.get("title"),
            "best_slug": None if best is None else best.get("slug"),
            "latest_trade_ts": max([as_int(row.get("timestamp")) for row in trades] or [0]),
        },
    }


def usable_curve_row(row: dict[str, Any]) -> bool:
    if row.get("status") != "complete" or row.get("edge") is None:
        return False
    active_legs = as_int(row.get("active_legs"))
    quoted_legs = as_int(row.get("quoted_legs") or row.get("included_legs"))
    return active_legs >= 3 and quoted_legs >= 3 and bool(row.get("slug"))


def target_from_payload(payload: dict[str, Any], current_target: str | None) -> tuple[str | None, str]:
    rows = payload.get("rows") or []
    usable_rows = [row for row in rows if usable_curve_row(row)]
    row_slugs = {str(row.get("slug") or "") for row in usable_rows}
    trigger = payload.get("trigger") or {}
    trigger_slug = str(trigger.get("event_slug") or "")
    if trigger_slug and trigger_slug in row_slugs:
        return trigger_slug, f"p3nny trigger: {trigger.get('title') or trigger_slug}"

    if usable_rows:
        best = max(usable_rows, key=lambda item: item.get("edge") or -999999.0)
        slug = str(best.get("slug") or "")
        return slug or current_target, f"p3nny best edge: {best.get('title') or slug}"

    return current_target, "p3nny recent market edge monitor"


def log_path_for_slug(args: argparse.Namespace, slug: str) -> Path:
    return Path(args.log_dir) / f"{safe_name(slug)}_edge.jsonl"


def reset_curve_target(
    args: argparse.Namespace,
    session: requests.Session,
    state: edgeui.State,
    slug: str,
    title_prefix: str,
) -> tuple[list[edgeui.Leg], Path]:
    title, legs = edgeui.load_event(session, slug)
    log_path = log_path_for_slug(args, slug)
    history = edgeui.load_existing_history(log_path, args.max_points)
    with state.lock:
        state.event_title = f"{title_prefix} | {title}"
        state.legs = legs
        state.history = [edgeui.summarize_point(point) for point in history[-args.max_points :]]
        state.latest_point = history[-1] if history else None
        state.last_error = None
    return legs, log_path


def monitor_loop(args: argparse.Namespace, state: edgeui.State) -> None:
    activity_session = make_session("pmscan-penny-activity-monitor/0.1")
    edge_session = make_session("pmscan-penny-edge-monitor/0.1")
    fee_cache: dict[str, tuple[float, float, bool]] = {}
    fee_lock = threading.Lock()
    top_stats = edgeui.load_top_market_stats(Path(args.top_log))
    current_slug: str | None = None
    current_legs: list[edgeui.Leg] = []
    current_log_path: Path | None = None

    while True:
        started = time.time()
        try:
            trades = fetch_latest_trades(activity_session, args.wallet, args.trade_limit)
            payload = scan_penny_recent_markets(args, trades, fee_cache, fee_lock)
            edgeui.apply_top_market_stats(top_stats, payload)
            edgeui.append_top_market_history(Path(args.top_log), payload)
            state.set_top_markets(payload)

            target_slug, target_title = target_from_payload(payload, current_slug)
            if target_slug and target_slug != current_slug:
                current_legs, current_log_path = reset_curve_target(args, edge_session, state, target_slug, target_title)
                current_slug = target_slug
                print(f"{utc_now()} target={current_slug} reason={target_title}", flush=True)
            elif target_slug and current_slug:
                with state.lock:
                    if not state.event_title.startswith(target_title):
                        title = state.event_title.split(" | ", 1)[-1]
                        state.event_title = f"{target_title} | {title}"

            if current_slug and current_legs and current_log_path:
                point = edgeui.compute_point(edge_session, current_legs, args.workers, args.gas_buffer)
                point["target_slug"] = current_slug
                state.add_point(point)
                current_log_path.parent.mkdir(parents=True, exist_ok=True)
                with current_log_path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(point, ensure_ascii=False, separators=(",", ":")) + "\n")

            summary = payload.get("summary") or {}
            trigger = payload.get("trigger") or {}
            print(
                f"{payload.get('ts')} penny_recent rows={len(payload.get('rows') or [])} "
                f"complete={summary.get('complete', 0)} best={summary.get('best_slug') or '-'} "
                f"trigger={trigger.get('event_slug') or '-'} target={current_slug or '-'} "
                f"elapsed={payload.get('elapsed_sec', 0.0):.2f}s",
                flush=True,
            )
        except Exception as exc:
            state.set_error(str(exc))
            print(f"{utc_now()} error={exc}", flush=True)

        if args.once:
            return
        elapsed = time.time() - started
        time.sleep(max(0.5, args.interval - elapsed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="p3nny recent traded market edge dashboard")
    parser.add_argument("--wallet", default=WALLET)
    parser.add_argument("--trade-limit", type=int, default=800)
    parser.add_argument("--market-count", type=int, default=20)
    parser.add_argument("--trigger-window-seconds", type=int, default=180)
    parser.add_argument("--trigger-threshold", type=int, default=10, help="trigger when BUY count is greater than this")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--port", type=int, default=5298)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--gas-buffer", type=float, default=0.0)
    parser.add_argument("--max-points", type=int, default=30000)
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--top-log", default=str(DEFAULT_TOP_LOG))
    parser.add_argument("--top-max-legs", type=int, default=160)
    parser.add_argument("--top-event-workers", type=int, default=4)
    parser.add_argument("--top-book-workers", type=int, default=16)
    parser.add_argument("--top-use-snapshot", action="store_true")
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = edgeui.State(max_points=args.max_points)
    edgeui.Handler.state = state
    thread = threading.Thread(target=monitor_loop, args=(args, state), daemon=True)
    thread.start()
    server = edgeui.ThreadingHTTPServer((args.host, args.port), edgeui.Handler)
    print(f"dashboard=http://{args.host}:{args.port}/", flush=True)
    if args.once:
        thread.join()
        return 0
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

