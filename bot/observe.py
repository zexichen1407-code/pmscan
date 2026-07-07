"""
Phase 1 观察模式 — 接 Polymarket 实时盘口(CLOB websocket), 让做市循环在活盘口上空跑。
不下任何真单。只回答一个核心问题: 如果我真挂了引擎算出的那些被动买单,
真实盘口里会不会成交、多久成交一次、单腿暴露多大。

关键真实信号(不依赖任何成交假设):
  HIT = 一笔真实成交(last_trade_price)的价格打中了我们当前在该腿的挂单(price <= 我们买价)。
  HIT 数 / 挂单时长 = 真实成交率。这是这套策略能不能活的命门。

数据源(只读): wss://ws-subscriptions-clob.polymarket.com/ws/market
用法: python observe.py [--seconds N] [--markets K] [--tokens id1,id2,...]
"""
import json, sys, time, threading, urllib.request
sys.path.insert(0, r"C:\Users\zexi\pmscan\bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket
from quote_engine import Params
from live_loop import MarketMaker

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
LOG = r"C:\Users\zexi\pmscan\bot\observe_log.jsonl"
CKPT = r"C:\Users\zexi\pmscan\bot\observe_checkpoint.json"

# ---- 参数 ----
args = sys.argv[1:]
def argval(flag, default):
    return args[args.index(flag)+1] if flag in args else default
SECONDS = int(argval("--seconds", "60"))
KMKT = int(argval("--markets", "3"))
WARMUP = int(argval("--warmup", "75"))   # 主跑前先实测活跃度多少秒
TOKENS_ARG = argval("--tokens", "")

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

# ---- 选市场: 按 maker 边际(价差宽)选肥盘, 不是按成交量选热门盘 ----
def fnum(x, d=0.0):
    try: return float(x)
    except: return d

def pick_markets(k, vol_floor=2000.0, spread_min=0.04, spread_max=0.25):
    cand = []
    for off in range(0, 2000, 100):
        page = get(f"https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
        if not page: break
        for m in page:
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                outs = json.loads(m.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except Exception:
                continue
            if not (len(toks) == 2 and len(prices) == 2 and m.get("acceptingOrders")
                    and m.get("enableOrderBook") and 0.06 <= prices[0] <= 0.94):
                continue
            bb, ba = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
            spread = (ba - bb) if (ba > bb > 0) else fnum(m.get("spread"))
            v24 = fnum(m.get("volume24hr"))
            if v24 < vol_floor or not (spread_min <= spread <= spread_max):
                continue
            hi = 0 if prices[0] >= prices[1] else 1
            lo = 1 - hi
            cand.append({
                "q": (m.get("question") or m.get("slug"))[:48],
                "leg_hi": outs[hi], "leg_lo": outs[lo],
                "tok_hi": toks[hi], "tok_lo": toks[lo],
                "px_hi": prices[hi], "px_lo": prices[lo],
                "spread": spread, "v24": v24,
            })
        if len(page) < 100: break
    cand.sort(key=lambda c: -(c["spread"] * c["v24"]))   # 甜区: 边际 x 成交量 都要好
    return cand[:k]

def screen_active(cands, k, warmup=75):
    """活跃度预筛: 真去盘口订阅候选池, 数 warmup 秒内的【主动卖单】(能成交我们买单的那侧),
    只留真在交易的盘。gamma 没有实时活跃字段、24h量是滞后的, 只能这样实测。"""
    tok2idx = {}
    for i, c in enumerate(cands):
        tok2idx[c["tok_hi"]] = i; tok2idx[c["tok_lo"]] = i
    sell_cnt = [0]*len(cands)
    def on_msg(ws, msg):
        try: data = json.loads(msg)
        except: return
        for ev in (data if isinstance(data, list) else [data]):
            if not isinstance(ev, dict): continue
            if (ev.get("event_type") or ev.get("type")) in ("last_trade_price", "trade"):
                a = ev.get("asset_id") or ev.get("asset")
                if a in tok2idx and (ev.get("side") or "").upper() == "SELL":
                    sell_cnt[tok2idx[a]] += 1
    ids = list(tok2idx.keys())
    def on_open(ws): ws.send(json.dumps({"assets_ids": ids, "type": "market"}))
    w = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_msg)
    threading.Timer(warmup, w.close).start()
    print(f"活跃度预筛: 订阅 {len(cands)} 个候选盘, {warmup}s 数主动卖单...")
    w.run_forever(ping_interval=10, ping_timeout=5)
    order = sorted(range(len(cands)), key=lambda i: -sell_cnt[i])
    active = [cands[i] for i in order if sell_cnt[i] > 0][:k]
    nz = sum(1 for x in sell_cnt if x > 0)
    print(f"  候选 {len(cands)} -> {nz} 个有主动卖单; 取前 {len(active)}:")
    for i in order[:len(active)]:
        print(f"    {sell_cnt[i]:>3} 卖单 | {cands[i]['q']}")
    return active

if TOKENS_ARG:
    ids = TOKENS_ARG.split(",")
    mkts = [{"q": "custom", "leg_hi": "HI", "leg_lo": "LO",
             "tok_hi": ids[0], "tok_lo": ids[1], "px_hi": 0.5, "px_lo": 0.5}]
else:
    pool = pick_markets(60)                  # 拉大候选池
    mkts = screen_active(pool, KMKT, WARMUP)  # 实测活跃度后只留真在交易的
    if not mkts:
        print("预筛后无活跃盘(都没主动卖单), 退回按价差×量 top-K"); mkts = pool[:KMKT]

if not mkts:
    print("没选到合适的二元盘"); sys.exit(1)

# 每个市场一个做市循环; 资产id -> (市场idx, 腿名)
mms, asset_map = [], {}
print(f"观察 {len(mkts)} 个市场, 时长 {SECONDS}s, 不下真单:")
for i, m in enumerate(mkts):
    mm = MarketMaker(m["leg_hi"], m["leg_lo"], Params(), end_t=None)  # live: 不触发临盘撤单
    mm.mid[m["leg_hi"]] = m["px_hi"]; mm.mid[m["leg_lo"]] = m["px_lo"]
    mms.append(mm)
    asset_map[m["tok_hi"]] = (i, m["leg_hi"])
    asset_map[m["tok_lo"]] = (i, m["leg_lo"])
    sp = m.get("spread"); spstr = f" 价差{sp*100:.0f}分" if sp else ""
    print(f"  [{i}] {m['q']}{spstr}  | 贵腿 {m['leg_hi']}@{m['px_hi']:.2f} / 便宜腿 {m['leg_lo']}@{m['px_lo']:.2f}")

# ---- 实时盘口状态 ----
books = {}  # asset_id -> {"bids":{price:size}, "asks":{price:size}}
hits = [0]*len(mkts)        # 真实成交打中我们挂单的次数(命中=会成交)
hit_size = [0.0]*len(mkts)
trades_seen = [0]*len(mkts) # 该腿所在市场总成交笔数
deficits = [[] for _ in mkts]  # 每笔成交价比我们买价高多少(>0=够不着, <=0=命中)
# 分腿(贵腿HI/便宜腿LO): 检验"绝对等距下两腿是否等概率成交"
hits_hi = [0]*len(mkts); hits_lo = [0]*len(mkts)
trades_hi = [0]*len(mkts); trades_lo = [0]*len(mkts)
deficits_hi = [[] for _ in mkts]; deficits_lo = [[] for _ in mkts]
raw_logged = [0]
t0 = time.time()
logf = open(LOG, "w", encoding="utf-8")

def best(asset):
    b = books.get(asset)
    if not b: return None, None
    bb = max((p for p, s in b["bids"].items() if s > 0), default=None)
    ba = min((p for p, s in b["asks"].items() if s > 0), default=None)
    return bb, ba

def apply_book(asset, bids, asks):
    bk = books.setdefault(asset, {"bids": {}, "asks": {}})
    bk["bids"] = {float(x["price"]): float(x["size"]) for x in bids}
    bk["asks"] = {float(x["price"]): float(x["size"]) for x in asks}

def apply_change(asset, changes):
    bk = books.setdefault(asset, {"bids": {}, "asks": {}})
    for ch in changes:
        side = (ch.get("side") or "").lower()
        p, s = float(ch["price"]), float(ch["size"])
        book = bk["bids"] if side in ("buy", "bid") else bk["asks"]
        if s == 0: book.pop(p, None)
        else: book[p] = s

def feed_mid(asset):
    if asset not in asset_map: return
    bb, ba = best(asset)
    if bb is None or ba is None: return
    mid = (bb + ba) / 2
    i, leg = asset_map[asset]
    mms[i].on_book(time.time(), leg, mid)

def on_message(ws, msg):
    try:
        data = json.loads(msg)
    except Exception:
        return
    events = data if isinstance(data, list) else [data]
    for ev in events:
        if not isinstance(ev, dict): continue
        et = ev.get("event_type") or ev.get("type")
        asset = ev.get("asset_id") or ev.get("asset")
        if raw_logged[0] < 3:  # 头几条原始消息存档, 便于核对协议
            logf.write(json.dumps({"raw": ev})[:1000] + "\n"); raw_logged[0] += 1
        if et == "book":
            apply_book(asset, ev.get("bids") or ev.get("buys") or [], ev.get("asks") or ev.get("sells") or [])
            feed_mid(asset)
        elif et in ("price_change", "agg_orderbook", "tick_size_change"):
            if ev.get("changes"): apply_change(asset, ev["changes"])
            feed_mid(asset)
        elif et in ("last_trade_price", "trade"):
            if asset not in asset_map: continue
            if (ev.get("side") or "").upper() != "SELL": continue  # 只有主动卖单能成交我们的买单, 否则不计入分母
            i, leg = asset_map[asset]
            price = float(ev.get("price", 0)); size = float(ev.get("size", 0) or 100)
            trades_seen[i] += 1
            is_hi = (leg == mkts[i]["leg_hi"])
            (trades_hi if is_hi else trades_lo)[i] += 1
            mm = mms[i]
            r = mm.rest.get(leg)
            if r and r[1] > 0:
                d = price - r[0]
                deficits[i].append(d)   # >0: 成交价比我们买价高这么多(够不着)
                (deficits_hi if is_hi else deficits_lo)[i].append(d)
            if mm.hit_test(leg, price):     # ★真实成交打中我们挂单 = 会成交
                hits[i] += 1; hit_size[i] += size
                (hits_hi if is_hi else hits_lo)[i] += 1
                logf.write(json.dumps({"t": round(time.time()-t0,1), "mkt": i, "leg": "HI" if is_hi else "LO", "HIT": leg[:12], "px": price, "bid": (r[0] if r else None), "sz": size})+"\n")
            mm.on_trade(time.time(), leg, price, size)

def on_open(ws):
    ids = list(asset_map.keys())
    ws.send(json.dumps({"assets_ids": ids, "type": "market"}))
    print(f"\n已订阅 {len(ids)} 个 token, 开始观察...\n")

def on_error(ws, err):
    print("ws error:", err)

def summary():
    el = time.time() - t0
    print(f"\n==== 观察 {el:.0f}s 汇总(不下真单) ====")
    tot_h = tot_t = 0
    for i, m in enumerate(mkts):
        mm = mms[i]
        q = mm.rest
        rest_str = f"挂:贵{q[m['leg_hi']][0] if q[m['leg_hi']] else '-'} 便宜{q[m['leg_lo']][0] if q[m['leg_lo']] else '-'}"
        print(f"  [{i}] {m['q'][:36]}")
        print(f"       重报{mm.c['requote']} 真实成交打中我们(HIT){hits[i]}/{trades_seen[i]}笔 追腿{mm.c['chase']} {rest_str}")
        tot_h += hits[i]; tot_t += trades_seen[i]
    print(f"  合计: 真实成交 {tot_t} 笔, 其中会打中我们挂单(成交) {tot_h} 笔")
    if tot_t:
        print(f"  => 当前目标边际下命中率 ≈ {100*tot_h/tot_t:.0f}%")

    # ★边际 vs 成交率 权衡曲线: 每条腿再贴紧 d 分能接到多少成交(代价=每对少赚 2d)
    alld = [x for sub in deficits for x in sub]
    if alld:
        print("\n  == 边际 vs 成交率 权衡(核心) ==")
        print("  每条腿在当前买价上再抬 d, 能打中的成交占比(代价: 每凑一对边际少 2d):")
        for d in (0.0, 0.005, 0.01, 0.02, 0.03):
            catch = sum(1 for x in alld if x <= d + 1e-9)
            give_up = 2 * d
            print(f"    抬 {d*100:>4.1f}分/腿  ->  接到 {100*catch/len(alld):>3.0f}% 成交   (每对让掉 {give_up*100:.1f}分边际)")
        print(f"  样本成交 {len(alld)} 笔; 成交价比我们买价高的中位 {sorted(alld)[len(alld)//2]*100:.1f} 分")
        print("  读法: 若某行'接到%'高、而'让掉边际'仍 < 你扣费后的安全垫, 那一档就是可行报价点;")
        print("        若要接到单就得让掉的边际 > 你的安全垫 -> 这赛道对你不可行(边际已被吃光)。")
    # ★分腿成交对称性: 绝对等距下两腿是否等概率成交(本次改动的关键验证)
    th_h, th_l = sum(trades_hi), sum(trades_lo)
    hh, hl = sum(hits_hi), sum(hits_lo)
    print("\n  == 分腿成交对称性(等距改动的关键验证; 分母=能成交我们的主动卖单) ==")
    if th_h: print(f"  贵腿:   {hh}/{th_h} 命中 = {100*hh/th_h:.0f}%")
    if th_l: print(f"  便宜腿: {hl}/{th_l} 命中 = {100*hl/th_l:.0f}%")
    print("  读法: 两腿命中率接近 -> 绝对等距确实给两腿等概率成交(改动达成目标);")
    print("        仍差很多 -> 订单流不对称, 需按实测给某腿微调距离(LEG_OFFSET)。")
    for nm, dd in (("贵腿", [x for s in deficits_hi for x in s]), ("便宜腿", [x for s in deficits_lo for x in s])):
        if dd:
            cells = "  ".join(f"{d*100:.0f}分:{100*sum(1 for x in dd if x <= d + 1e-9)/len(dd):.0f}%" for d in (0.0, 0.01, 0.02, 0.03))
            print(f"  [{nm} n={len(dd)}] 再抬d接到%: {cells}")
    print(f"\n  原始消息+命中明细已存: {LOG}")
    print("  注: 分母只算主动卖单(SELL,能打到我们买单的那侧); 命中=该卖单成交价 <= 我们买价。")
    print("      仍未扣队列优先权/延迟/部分成交/手续费 -> 真实成交率还更低; 跑越久越准。")

def write_checkpoint():
    el = time.time() - t0
    th_h, th_l = sum(trades_hi), sum(trades_lo)
    hh, hl = sum(hits_hi), sum(hits_lo)
    cp = {"elapsed_s": round(el, 1), "total_trades": sum(trades_seen), "total_hits": sum(hits),
          "hi_贵腿": {"hits": hh, "trades": th_h, "rate": (round(hh/th_h, 3) if th_h else None)},
          "lo_便宜腿": {"hits": hl, "trades": th_l, "rate": (round(hl/th_l, 3) if th_l else None)},
          "per_market": [{"q": m["q"][:30], "hits": hits[i], "trades": trades_seen[i]} for i, m in enumerate(mkts)]}
    with open(CKPT, "w", encoding="utf-8") as f:
        json.dump(cp, f, ensure_ascii=False, indent=1)

def _ckpt_loop():   # 每120秒存档一次, 重启/断电只丢2分钟, 也方便中途查进度
    while time.time() < t0 + SECONDS + 5:
        time.sleep(120)
        try: write_checkpoint()
        except Exception: pass

threading.Thread(target=_ckpt_loop, daemon=True).start()

ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message, on_error=on_error)
threading.Timer(SECONDS, ws.close).start()
try:
    ws.run_forever(ping_interval=10, ping_timeout=5, reconnect=5)  # 断线自动重连
finally:
    logf.close()
    summary()
