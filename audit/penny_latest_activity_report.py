# -*- coding: utf-8 -*-
"""
Generate an HTML report for e46m3 / 0xp3nny latest Polymarket activity.

Default:
  python audit/penny_latest_activity_report.py

Continuously refresh:
  python audit/penny_latest_activity_report.py --watch --interval 300
"""

from __future__ import annotations

import argparse
import html
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
DATA_API = "https://data-api.polymarket.com"
DEFAULT_OUT = Path("reports") / "penny_latest_3500.html"
DEFAULT_CACHE = Path("reports") / "penny_latest_3500_cache.json"


def money(x: float) -> str:
    return f"${x:,.2f}"


def num(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".")


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except (TypeError, ValueError):
        return default


def esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def ts_to_dt(ts: int, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz)


def activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash"),
        row.get("asset"),
        row.get("type"),
        row.get("timestamp"),
        row.get("outcomeIndex"),
        row.get("side"),
        row.get("usdcSize"),
    )


def get_json(url: str, tries: int = 6) -> Any:
    last: Any = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 penny-report"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:160]
            last = f"HTTP {exc.code}: {body}"
            if exc.code in (408, 429, 500, 502, 503):
                time.sleep(0.7 + i * 0.7)
                continue
            raise RuntimeError(last) from exc
        except Exception as exc:
            last = repr(exc)
            time.sleep(0.7 + i * 0.7)
    raise RuntimeError(f"GET failed after retries: {url} last={last}")


def fetch_activity_page(wallet: str, limit: int, offset: int = 0, end: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"user": wallet, "limit": limit, "offset": offset}
    if end is not None:
        params["end"] = end
    qs = urllib.parse.urlencode(params)
    data = get_json(f"{DATA_API}/activity?{qs}")
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def fetch_latest_activity(wallet: str, total: int, page_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    page_size = max(1, min(page_size, 1000))
    end: int | None = None

    while len(rows) < total:
        window_min_ts: int | None = None
        window_new = 0
        window_rows = 0
        for offset in range(0, 3000, page_size):
            limit = min(page_size, total - len(rows), 3000 - window_rows)
            if limit <= 0:
                break
            data = fetch_activity_page(wallet, limit, offset=offset, end=end)
            if not data:
                break
            window_rows += len(data)
            for row in data:
                if not isinstance(row, dict):
                    continue
                ts = inum(row.get("timestamp"))
                if ts:
                    window_min_ts = ts if window_min_ts is None else min(window_min_ts, ts)
                key = activity_key(row)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                window_new += 1
                if len(rows) >= total:
                    break
            if len(rows) >= total or len(data) < limit:
                break
            time.sleep(0.2)
        if len(rows) >= total:
            break
        if window_min_ts is None or window_new == 0:
            break
        new_end = window_min_ts - 1
        if end is not None and new_end >= end:
            break
        end = new_end
        time.sleep(0.2)

    rows.sort(key=lambda r: inum(r.get("timestamp")), reverse=True)
    return rows[:total]


def load_cache(path: Path, wallet: str, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if data.get("wallet", "").lower() != wallet.lower():
        return []
    rows = data.get("rows")
    if not isinstance(rows, list):
        return []
    cleaned = [row for row in rows if isinstance(row, dict)]
    cleaned.sort(key=lambda r: inum(r.get("timestamp")), reverse=True)
    return cleaned[:limit]


def save_cache(path: Path, wallet: str, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "wallet": wallet,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def same_head(cached_rows: list[dict[str, Any]], latest_rows: list[dict[str, Any]]) -> bool:
    if not cached_rows or not latest_rows:
        return False
    n = min(len(cached_rows), len(latest_rows))
    return all(activity_key(cached_rows[i]) == activity_key(latest_rows[i]) for i in range(n))


def get_rows_with_cache(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    cache_path = Path(args.cache)
    cached = load_cache(cache_path, args.wallet, args.limit)
    if cached and not args.force_refresh:
        latest = fetch_activity_page(args.wallet, min(args.head_check, args.limit), offset=0)
        if same_head(cached, latest):
            return cached[: args.limit], f"cache hit: latest {len(latest)} rows unchanged"
        print(f"detected new activity: refreshing full latest {args.limit} rows")

    rows = fetch_latest_activity(args.wallet, args.limit, args.page_size)
    save_cache(cache_path, args.wallet, rows)
    return rows, f"refreshed from API and saved cache: {cache_path}"


@dataclass
class Agg:
    rows: int = 0
    activity_usdc: float = 0.0
    trade_usdc: float = 0.0
    buy_usdc: float = 0.0
    sell_usdc: float = 0.0
    size: float = 0.0
    types: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    outcomes: defaultdict[str, dict[str, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))

    def add(self, row: dict[str, Any]) -> None:
        typ = str(row.get("type") or "")
        side = str(row.get("side") or "").upper()
        outcome = str(row.get("outcome") or "(no outcome)")
        usdc = fnum(row.get("usdcSize"))
        size = fnum(row.get("size"))
        self.rows += 1
        self.activity_usdc += usdc
        self.size += size
        self.types[typ] += 1
        if typ == "TRADE":
            self.trade_usdc += usdc
            if side == "BUY":
                self.buy_usdc += usdc
            elif side == "SELL":
                self.sell_usdc += usdc
        out = self.outcomes[outcome]
        out["rows"] += 1
        out["activity_usdc"] += usdc
        out["trade_usdc"] += usdc if typ == "TRADE" else 0.0
        out["buy_usdc"] += usdc if typ == "TRADE" and side == "BUY" else 0.0
        out["sell_usdc"] += usdc if typ == "TRADE" and side == "SELL" else 0.0
        out["size"] += size


def market_key(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("conditionId") or row.get("title") or "unknown-market")


def market_url(row: dict[str, Any]) -> str:
    event_slug = row.get("eventSlug")
    slug = row.get("slug")
    if event_slug:
        return f"https://polymarket.com/event/{event_slug}"
    if slug:
        return f"https://polymarket.com/market/{slug}"
    return "https://polymarket.com"


def build_report(
    rows: list[dict[str, Any]],
    wallet: str,
    tz_name: str,
    min_market_trade_usdc: float,
    source_note: str,
) -> str:
    tz = ZoneInfo(tz_name)
    generated = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    tss = [inum(r.get("timestamp")) for r in rows if r.get("timestamp")]
    span = "-"
    if tss:
        newest = ts_to_dt(max(tss), tz).strftime("%Y-%m-%d %H:%M:%S")
        oldest = ts_to_dt(min(tss), tz).strftime("%Y-%m-%d %H:%M:%S")
        span = f"{oldest} -> {newest}"

    daily: dict[str, Agg] = defaultdict(Agg)
    daily_market: dict[tuple[str, str], Agg] = defaultdict(Agg)
    market_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    first_row_by_market: dict[tuple[str, str], dict[str, Any]] = {}
    overall = Agg()

    for row in rows:
        ts = inum(row.get("timestamp"))
        day = ts_to_dt(ts, tz).strftime("%Y-%m-%d") if ts else "unknown-date"
        key = market_key(row)
        overall.add(row)
        daily[day].add(row)
        daily_market[(day, key)].add(row)
        market_rows[(day, key)].append(row)
        first_row_by_market.setdefault((day, key), row)

    display_pairs = [
        (day, key, agg)
        for (day, key), agg in daily_market.items()
        if agg.trade_usdc >= min_market_trade_usdc
    ]
    displayed_markets = {key for _, key, _ in display_pairs}
    displayed_market_days = len(display_pairs)
    displayed_trade = sum(agg.trade_usdc for _, _, agg in display_pairs)
    displayed_buy = sum(agg.buy_usdc for _, _, agg in display_pairs)
    displayed_sell = sum(agg.sell_usdc for _, _, agg in display_pairs)

    days_html = []
    for day in sorted(daily.keys(), reverse=True):
        markets = [
            (key, agg)
            for (d_day, key), agg in daily_market.items()
            if d_day == day and agg.trade_usdc >= min_market_trade_usdc
        ]
        markets.sort(key=lambda item: (item[1].trade_usdc, item[1].activity_usdc, item[1].rows), reverse=True)
        if not markets:
            continue
        day_rows = sum(agg.rows for _, agg in markets)
        day_trade = sum(agg.trade_usdc for _, agg in markets)
        day_buy = sum(agg.buy_usdc for _, agg in markets)
        day_sell = sum(agg.sell_usdc for _, agg in markets)
        market_blocks = []
        for key, agg in markets:
            first = first_row_by_market[(day, key)]
            title = first.get("title") or key
            url = market_url(first)
            rows_for_market = sorted(market_rows[(day, key)], key=lambda r: inum(r.get("timestamp")), reverse=True)
            option_rows = []
            for outcome, o in sorted(agg.outcomes.items(), key=lambda kv: (kv[1]["trade_usdc"], kv[1]["activity_usdc"]), reverse=True):
                option_rows.append(
                    "<tr>"
                    f"<td>{esc(outcome)}</td>"
                    f"<td>{int(o['rows'])}</td>"
                    f"<td>{money(o['trade_usdc'])}</td>"
                    f"<td>{money(o['buy_usdc'])}</td>"
                    f"<td>{money(o['sell_usdc'])}</td>"
                    f"<td>{num(o['size'])}</td>"
                    "</tr>"
                )
            tx_rows = []
            for row in rows_for_market:
                ts = inum(row.get("timestamp"))
                tstr = ts_to_dt(ts, tz).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
                tx = str(row.get("transactionHash") or "")
                tx_short = tx[:10] + "..." + tx[-6:] if len(tx) > 20 else tx
                tx_link = f"https://polygonscan.com/tx/{tx}" if tx else ""
                tx_cell = f'<a href="{esc(tx_link)}" target="_blank">{esc(tx_short)}</a>' if tx else "-"
                tx_rows.append(
                    "<tr>"
                    f"<td>{esc(tstr)}</td>"
                    f"<td>{esc(row.get('type'))}</td>"
                    f"<td>{esc(row.get('side'))}</td>"
                    f"<td>{esc(row.get('outcome'))}</td>"
                    f"<td>{esc(row.get('price'))}</td>"
                    f"<td>{num(fnum(row.get('size')))}</td>"
                    f"<td>{money(fnum(row.get('usdcSize')))}</td>"
                    f"<td>{tx_cell}</td>"
                    "</tr>"
                )

            types = ", ".join(f"{esc(k)}:{v}" for k, v in sorted(agg.types.items()))
            market_blocks.append(
                "<details class='market'>"
                "<summary>"
                f"<span class='market-title'>{esc(title)}</span>"
                f"<span>{esc(types)}</span>"
                f"<span>{money(agg.trade_usdc)} trade</span>"
                f"<span>{money(agg.activity_usdc)} activity</span>"
                f"<a href='{esc(url)}' target='_blank' onclick='event.stopPropagation()'>open</a>"
                "</summary>"
                "<div class='detail-grid'>"
                "<section><h4>Options</h4><table>"
                "<thead><tr><th>Option</th><th>Rows</th><th>Trade USDC</th><th>Buy</th><th>Sell</th><th>Size</th></tr></thead>"
                f"<tbody>{''.join(option_rows)}</tbody></table></section>"
                "<section><h4>Activity rows</h4><table>"
                "<thead><tr><th>Time</th><th>Type</th><th>Side</th><th>Option</th><th>Price</th><th>Size</th><th>USDC</th><th>Tx</th></tr></thead>"
                f"<tbody>{''.join(tx_rows)}</tbody></table></section>"
                "</div>"
                "</details>"
            )
        days_html.append(
            "<details class='day' open>"
            "<summary>"
            f"<b>{esc(day)}</b>"
            f"<span>{len(markets)} markets</span>"
            f"<span>{day_rows} rows</span>"
            f"<span>{money(day_trade)} trade</span>"
            f"<span>{money(day_buy)} buy</span>"
            f"<span>{money(day_sell)} sell</span>"
            "</summary>"
            f"{''.join(market_blocks)}"
            "</details>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>penny latest activity report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; margin: 24px; color: #17202a; background: #f6f8fb; }}
h1 {{ margin: 0 0 8px; font-size: 24px; }}
.meta {{ color: #5f6b7a; margin-bottom: 18px; line-height: 1.5; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 16px 0 20px; }}
.card {{ background: white; border: 1px solid #d8dee9; border-radius: 8px; padding: 12px; }}
.card b {{ display: block; font-size: 20px; margin-top: 5px; }}
details {{ background: white; border: 1px solid #d8dee9; border-radius: 8px; margin: 10px 0; }}
summary {{ cursor: pointer; display: grid; grid-template-columns: minmax(280px, 1fr) repeat(5, auto); gap: 14px; align-items: center; padding: 10px 12px; }}
.market summary {{ grid-template-columns: minmax(360px, 1fr) repeat(4, auto); border-top: 1px solid #eef1f5; }}
.market-title {{ font-weight: 600; }}
.detail-grid {{ padding: 0 12px 12px; }}
h4 {{ margin: 12px 0 6px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; background: #fff; }}
th, td {{ border-bottom: 1px solid #edf0f4; padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ color: #435061; background: #f8fafc; }}
a {{ color: #1257b7; text-decoration: none; }}
@media (max-width: 760px) {{
  body {{ margin: 12px; }}
  summary, .market summary {{ grid-template-columns: 1fr; gap: 4px; }}
  table {{ display: block; overflow-x: auto; white-space: nowrap; }}
}}
</style>
</head>
<body>
<h1>penny 最新 {len(rows)} 条 activity 总结</h1>
<div class="meta">
钱包: <code>{esc(wallet)}</code><br>
生成时间: {esc(generated)}<br>
覆盖时间: {esc(span)}<br>
数据来源: {esc(source_note)}<br>
展示规则: 只展示单日市场 TRADE 金额 >= {money(min_market_trade_usdc)} 的市场
</div>
<div class="cards">
  <div class="card">Scanned rows<b>{len(rows):,}</b></div>
  <div class="card">Shown markets<b>{len(displayed_markets):,}</b></div>
  <div class="card">Shown market-days<b>{displayed_market_days:,}</b></div>
  <div class="card">Shown trade USDC<b>{money(displayed_trade)}</b></div>
  <div class="card">Shown buy USDC<b>{money(displayed_buy)}</b></div>
  <div class="card">Shown sell USDC<b>{money(displayed_sell)}</b></div>
</div>
{''.join(days_html)}
</body>
</html>
"""


def run_once(args: argparse.Namespace) -> None:
    rows, source_note = get_rows_with_cache(args)
    html_text = build_report(rows, args.wallet, args.tz, args.min_market_trade_usdc, source_note)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] wrote {out.resolve()} rows={len(rows)} | {source_note}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate clickable HTML summary for penny latest Polymarket activity.")
    p.add_argument("--wallet", default=WALLET)
    p.add_argument("--limit", type=int, default=3500)
    p.add_argument("--page-size", type=int, default=1000)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--cache", default=str(DEFAULT_CACHE))
    p.add_argument("--min-market-trade-usdc", type=float, default=1000.0)
    p.add_argument("--head-check", type=int, default=50)
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--tz", default="Asia/Shanghai")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=float, default=300.0)
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.watch:
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except KeyboardInterrupt:
            print("stopped")
            return 130
        except Exception as exc:
            print(f"error: {exc}")
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
