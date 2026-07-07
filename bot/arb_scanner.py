"""
Phase 0 — 实时套利信号扫描器(只读, 不下单)
在当前 Polymarket 真实盘口上找"两边都买凑成 $1"的套利:
  对每个二元市场, 取 YES token 和 NO token 各自的最优卖价(ask),
  若 ask_yes + ask_no < 1 - 边际, 就是当下可吃的套利(taker 视角);
  同时给出 maker 视角: 你挂在中价下方、目标成对成本 C 时能锁的价差。

数据源(只读):
  gamma-api.polymarket.com/markets  -> 列活跃市场 + clobTokenIds
  clob.polymarket.com/book          -> 每个 token 的盘口(best ask/bid)
用法: python arb_scanner.py [扫描市场数,默认60]
"""
import json, sys, time, urllib.request
sys.path.insert(0, r"C:\Users\zexi\pmscan\bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from quote_engine import Params

p = Params()
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60

def get(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(0.3)
    raise last

def best_ask(token_id):
    """clob /book: asks 升序, 最优卖价 = 最低 ask。返回 (ask, bid) 或 (None,None)。"""
    try:
        b = get(f"https://clob.polymarket.com/book?token_id={token_id}")
    except Exception:
        return None, None
    asks = b.get("asks") or []
    bids = b.get("bids") or []
    # polymarket book: asks 可能降序返回, 取最小价; bids 取最大价
    a = min((float(x["price"]) for x in asks), default=None)
    bd = max((float(x["price"]) for x in bids), default=None)
    return a, bd

# 1) 拉活跃市场(按 24h 量排序)
print(f"拉取活跃市场 (目标扫 {N} 个二元盘)...")
markets = []
for off in range(0, 600, 100):
    page = get(f"https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
    if not page:
        break
    markets.extend(page)
    if len(page) < 100:
        break

# 2) 过滤二元、可下单、有盘口
cands = []
for m in markets:
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
    except Exception:
        continue
    if len(toks) != 2 or not m.get("acceptingOrders") or not m.get("enableOrderBook"):
        continue
    cands.append({"q": m.get("question") or m.get("slug"), "slug": m.get("slug"),
                  "toks": toks, "outs": outs, "v24": float(m.get("volume24hr") or 0)})
    if len(cands) >= N:
        break

print(f"候选二元盘: {len(cands)} 个, 逐个查盘口...\n")

# 3) 查每个盘口的两腿 ask, 算 ask_yes + ask_no
rows = []
for c in cands:
    a0, b0 = best_ask(c["toks"][0]); time.sleep(0.05)
    a1, b1 = best_ask(c["toks"][1]); time.sleep(0.05)
    if a0 is None or a1 is None:
        continue
    taker_cost = a0 + a1            # 两边都吃单买齐一对的成本
    taker_edge = 1.0 - taker_cost - 2 * p.FEE
    # maker 视角: 若两边都挂在 ask 下一档(这里用 bid 近似你能挂到的位置), 成对成本
    maker_cost = (b0 or 0) + (b1 or 0)
    rows.append({**c, "a0": a0, "a1": a1, "taker_cost": taker_cost,
                 "taker_edge": taker_edge, "maker_cost": maker_cost})

rows.sort(key=lambda r: -r["taker_edge"])
print("== 当下可吃的套利(taker: 两边卖价之和 < 1) ==")
print(f"{'taker边际':>9}{'两腿ask和':>10}{'24h量$':>11}  市场")
hits = [r for r in rows if r["taker_edge"] > p.EDGE_FLOOR]
if not hits:
    print("  (当前没有 ask 和 < 1-边际 的即时吃单套利——正常,这种瞬时错价很快被抢)")
for r in hits[:15]:
    print(f"{r['taker_edge']*100:>8.1f}%{r['taker_cost']:>10.4f}{r['v24']:>11,.0f}  {r['q'][:50]}")

print("\n== 最接近 $1 的盘(maker 蹲点候选: 两腿卖价和略高于1, 等它掉) ==")
print(f"{'两腿ask和':>10}{'24h量$':>11}  市场")
near = sorted([r for r in rows if r["taker_edge"] <= p.EDGE_FLOOR], key=lambda r: r["taker_cost"])
for r in near[:12]:
    print(f"{r['taker_cost']:>10.4f}{r['v24']:>11,.0f}  {r['q'][:50]}")

print(f"\n共查 {len(rows)} 个盘口; 即时吃单套利 {len(hits)} 个。")
print("说明: 即时 taker 套利稀少且转瞬即逝(被 bot 秒抢)——这正是为什么要做 maker 蹲点,")
print("      在上面那些'接近1'的盘两边挂被动单、等价格掉到成对成本<C 时成交再合并。")
