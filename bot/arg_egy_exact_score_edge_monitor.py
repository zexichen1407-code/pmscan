# -*- coding: utf-8 -*-
"""
Realtime full-set NO edge monitor for a Polymarket exact-score event.

Default target:
  Argentina vs. Egypt - Exact Score

This script is read-only. It never places orders.
It polls order books every N seconds, computes:
  edge = (number_of_legs - 1) - sum(effective_NO_price) - sum(taker_fee) - gas_buffer

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
DEFAULT_SLUG = "fifwc-arg-egy-2026-07-07-exact-score"
DEFAULT_LOG = Path(__file__).resolve().parent / "arg_egy_exact_score_edge.jsonl"


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
        self.last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "event_title": self.event_title,
                "legs": len(self.legs),
                "history": list(self.history),
                "latest": self.latest_point,
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
            trimmed = points[-self.max_points :]
            self.history = [summarize_point(point) for point in trimmed]
            self.latest_point = trimmed[-1] if trimmed else None

    def set_error(self, error: str) -> None:
        with self.lock:
            self.last_error = error


def summarize_point(point: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ts",
        "complete",
        "direct_complete",
        "legs",
        "receive",
        "no_sum",
        "fee_sum",
        "gas_buffer",
        "edge",
        "edge_cents",
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


def compute_point(session: requests.Session, legs: list[Leg], workers: int, gas_buffer: float) -> dict[str, Any]:
    books = fetch_books(session, legs, workers)
    rows: list[dict[str, Any]] = []
    no_sum = 0.0
    fee_sum = 0.0
    complete = True
    direct_complete = True

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
        "rows": rows,
    }


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


HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket Exact Score NO Edge Monitor</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, sans-serif; }
    body { margin: 0; background: #111418; color: #e7edf3; }
    header { padding: 16px 20px; border-bottom: 1px solid #27313a; }
    h1 { margin: 0 0 6px; font-size: 20px; }
    .sub { color: #9fb0bf; font-size: 13px; }
    main { padding: 18px 20px; display: grid; gap: 16px; }
    .stats { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }
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
      <div class="stat"><div class="label">NO sum</div><div class="value" id="nosum">-</div></div>
      <div class="stat"><div class="label">Fee sum</div><div class="value" id="fee">-</div></div>
      <div class="stat"><div class="label">Receive</div><div class="value" id="receive">-</div></div>
      <div class="stat"><div class="label">Last tick</div><div class="value" id="tick">-</div></div>
    </section>
    <div class="chart-wrap">
      <canvas id="chart" width="1400" height="440"></canvas>
      <div id="tooltip" class="tooltip"></div>
    </div>
    <section>
      <table>
        <thead><tr><th>Leg</th><th>Effective NO</th><th>Direct NO</th><th>YES bid</th><th>Fee</th><th>Route</th></tr></thead>
        <tbody id="legs"></tbody>
      </table>
    </section>
  </main>
  <script>
    const chart = document.getElementById('chart');
    const ctx = chart.getContext('2d');
    const tooltip = document.getElementById('tooltip');
    let hoverPoints = [];
    function fmt(x, n=6) { return x === null || x === undefined ? '-' : Number(x).toFixed(n); }
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
        document.getElementById('nosum').textContent = fmt(latest.no_sum, 6);
        document.getElementById('fee').textContent = fmt(latest.fee_sum, 6);
        document.getElementById('receive').textContent = fmt(latest.receive, 2);
        document.getElementById('tick').textContent = tsLabel(latest.ts);
        document.getElementById('legs').innerHTML = (latest.rows || []).map(r => `
          <tr>
            <td>${r.title || '-'}</td>
            <td>${fmt(r.effective_no, 4)}</td>
            <td>${fmt(r.direct_no_ask, 4)}</td>
            <td>${fmt(r.yes_bid, 4)}</td>
            <td>${fmt(r.fee, 6)}</td>
            <td>${r.source || '-'}</td>
          </tr>`).join('');
      }
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
    session = requests.Session()
    session.headers.update({"User-Agent": "pmscan-exact-score-edge-monitor/0.1"})
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = State(max_points=args.max_points)
    Handler.state = state
    thread = threading.Thread(target=monitor_loop, args=(args, state), daemon=True)
    thread.start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dashboard=http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
