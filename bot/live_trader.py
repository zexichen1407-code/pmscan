"""
Phase 2 实盘交易器 — 默认 DRY-RUN(只算不下单)。真正下真单要显式 --live, 且由你运行。
硬安全护栏: 总敞口上限 $10、极小单量、限时、限单数、随时撤单。

模式:
  place-cancel : 最安全的管道测试 —— 挂一个远低于市价、几乎不会成交的小买单, 确认能挂上、能撤掉。零成交风险。
  make         : 真做市 —— 在一个甜区盘两腿挂引擎算出的小买单, 跑 N 分钟, 看真实成交, 收尾撤所有单。

用法:
  python live_trader.py                          # dry-run, place-cancel(只打印会做什么)
  python live_trader.py --mode make --minutes 5  # dry-run 看 make 模式会挂什么
  python live_trader.py --live --mode place-cancel        # ★真下单(你来运行, 用你的私钥)
  python live_trader.py --live --mode make --minutes 5 --clip 5
"""
import os, sys, time, json, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from quote_engine import Params, quote

A = sys.argv[1:]
def flag(f): return f in A
def val(f, d): return A[A.index(f)+1] if f in A else d
LIVE = flag("--live")
MODE = val("--mode", "place-cancel")
MINUTES = float(val("--minutes", "3"))
CLIP = float(val("--clip", "5"))            # 每单股数(Polymarket 最小约 5 股/$1)
MAX_NOTIONAL = float(val("--max-notional", "10"))  # 总敞口硬顶 $
TOKENS = val("--tokens", "")                 # "tokHi,tokLo" 手动指定, 否则自动选甜区盘
HOST = "https://clob.polymarket.com"

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

def fnum(x, d=0.0):
    try: return float(x)
    except: return d

def best(tok):
    try:
        b = get(f"{HOST}/book?token_id={tok}")
    except Exception:
        return None, None
    bb = max((float(x["price"]) for x in (b.get("bids") or [])), default=None)
    ba = min((float(x["price"]) for x in (b.get("asks") or [])), default=None)
    return bb, ba

def pick_one():
    """选甜区二元盘: gamma 初筛 -> 按分排序 -> 实拉真实盘口, 取第一个两腿都健康(双边都有挂单、中价合理)的。"""
    cands = []
    for off in range(0, 1500, 100):
        page = get(f"https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
        if not page: break
        for m in page:
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
                outs = json.loads(m.get("outcomes") or "[]")
                pr = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except Exception: continue
            if not (len(toks)==2 and len(pr)==2 and m.get("acceptingOrders") and m.get("enableOrderBook") and 0.12<=pr[0]<=0.88):
                continue
            bb, ba = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
            spread = (ba-bb) if (ba>bb>0) else fnum(m.get("spread"))
            v24 = fnum(m.get("volume24hr"))
            if v24 < 2000 or not (0.04 <= spread <= 0.22): continue
            hi = 0 if pr[0]>=pr[1] else 1; lo = 1-hi
            cands.append({"q": (m.get("question") or m.get("slug"))[:50], "score": spread*v24,
                          "tok_hi": toks[hi], "tok_lo": toks[lo],
                          "leg_hi": outs[hi], "leg_lo": outs[lo], "spread": spread, "v24": v24})
        if len(page) < 100: break
    cands.sort(key=lambda c: -c["score"])
    # 实拉盘口校验: 两腿都要有 bid+ask, 中价在 0.08-0.92
    for c in cands[:25]:
        bb_h, ba_h = best(c["tok_hi"]); bb_l, ba_l = best(c["tok_lo"])
        if None in (bb_h, ba_h, bb_l, ba_l): continue
        mh, ml = (bb_h+ba_h)/2, (bb_l+ba_l)/2
        if not (0.08 <= mh <= 0.92 and 0.08 <= ml <= 0.92): continue
        if ba_h <= bb_h or ba_l <= bb_l: continue
        c.update(bb_h=bb_h, ba_h=ba_h, bb_l=bb_l, ba_l=ba_l)
        return c
    return None

# ---- 选盘 ----
if TOKENS:
    th, tl = TOKENS.split(",")
    bb_h, ba_h = best(th); bb_l, ba_l = best(tl)
    mk = {"q": "custom", "tok_hi": th, "tok_lo": tl, "leg_hi": "HI", "leg_lo": "LO", "spread": None, "v24": None,
          "bb_h": bb_h, "ba_h": ba_h, "bb_l": bb_l, "ba_l": ba_l}
else:
    mk = pick_one()
if not mk:
    print("没选到健康的甜区盘(两腿都有双边挂单的), 稍后再试或用 --tokens 指定。"); sys.exit(1)

bb_h, ba_h, bb_l, ba_l = mk["bb_h"], mk["ba_h"], mk["bb_l"], mk["ba_l"]
mid_h = (bb_h+ba_h)/2 if (bb_h and ba_h) else None
mid_l = (bb_l+ba_l)/2 if (bb_l and ba_l) else None
p = Params()
print(f"{'[实盘 LIVE]' if LIVE else '[DRY-RUN 只算不下单]'} 模式={MODE}  市场: {mk['q']}")
if mk.get("spread"): print(f"  价差≈{mk['spread']*100:.0f}分  24h量${mk['v24']:,.0f}")
print(f"  贵腿 {mk['leg_hi']}: bid {bb_h} ask {ba_h}")
print(f"  便宜腿 {mk['leg_lo']}: bid {bb_l} ask {ba_l}")
print(f"  硬顶: 总敞口 ${MAX_NOTIONAL} / 每单 {CLIP} 股 / 限时 {MINUTES} 分")

if mid_h is None or mid_l is None:
    print("盘口取不到(可能太冷), 换个市场。"); sys.exit(1)

# ---- 计算意图订单 ----
q = quote(mid_h, mid_l, 0, 0, p)
intended = []
if MODE == "place-cancel":
    # 远低于市价、几乎不会成交的探针单(只测能挂能撤)
    probe = round(max(0.01, (bb_l or 0.1) - 0.03), 3)
    intended = [{"leg": mk["leg_lo"], "tok": mk["tok_lo"], "price": probe, "size": CLIP, "note": "远低于市价的探针(不该成交)"}]
elif MODE == "make":
    if q["size_yes"] > 0:
        intended.append({"leg": mk["leg_hi"], "tok": mk["tok_hi"], "price": q["bid_yes"], "size": CLIP, "note": "做市买单(贵腿)"})
    if q["size_no"] > 0:
        intended.append({"leg": mk["leg_lo"], "tok": mk["tok_lo"], "price": q["bid_no"], "size": CLIP, "note": "做市买单(便宜腿)"})

notional = sum(o["price"]*o["size"] for o in intended)
print(f"\n意图订单(每对成本目标 {q.get('pair_cost')}, 引擎边际 ~{(1-q.get('pair_cost',1))*100:.1f}分):")
for o in intended:
    print(f"  买 {o['leg']:<14} {o['size']:.0f}股 @ {o['price']}  = ${o['price']*o['size']:.2f}   [{o['note']}]")
print(f"  合计敞口 ${notional:.2f}  (硬顶 ${MAX_NOTIONAL})")
if notional > MAX_NOTIONAL:
    print("  ✋ 超过硬顶, 终止。"); sys.exit(1)

if not LIVE:
    print("\nDRY-RUN: 以上只是会做什么, 没下任何真单。确认无误后, 你用 --live 亲自运行下真单。")
    sys.exit(0)

# ================= 以下仅在 --live 且由你运行时执行真单 =================
print("\n*** LIVE: 即将下真单 ***")
def load_env(path=os.path.join(os.path.dirname(__file__), ".env")):
    env = {}
    for line in open(path, encoding="utf-8"):
        line=line.strip()
        if line and not line.startswith("#") and "=" in line:
            k,v=line.split("=",1); env[k.strip()]=v.strip()
    return env
env = load_env()
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, ApiCreds
from py_clob_client.order_builder.constants import BUY
SIG = int(env.get("SIG_TYPE","1"))
client = (ClobClient(HOST, key=env["PK"], chain_id=137) if SIG==0
          else ClobClient(HOST, key=env["PK"], chain_id=137, signature_type=SIG, funder=env["FUNDER"]))
client.set_api_creds(ApiCreds(env["CLOB_API_KEY"], env["CLOB_SECRET"], env["CLOB_PASSPHRASE"]))

placed = []
try:
    for o in intended:
        order = client.create_order(OrderArgs(token_id=o["tok"], price=o["price"], size=o["size"], side=BUY))
        resp = client.post_order(order, OrderType.GTC)
        oid = resp.get("orderID") or resp.get("orderId") or str(resp)
        placed.append(oid)
        print(f"  已挂: 买 {o['leg']} {o['size']}@{o['price']} -> {oid}")
    print(f"\n监控 {MINUTES} 分钟成交...")
    end = time.time() + MINUTES*60
    while time.time() < end:
        time.sleep(10)
        try:
            opn = client.get_orders()
            print(f"  [{int(end-time.time())}s 剩] 在册挂单 {len(opn)} 笔")
        except Exception as e:
            print("  查单异常:", e)
finally:
    try:
        client.cancel_all()
        print("\n已撤掉所有挂单(收尾)。")
    except Exception as e:
        print("撤单异常(去 app 手动确认):", e)
    print("成交/持仓请到 Polymarket app 核对。合并(merge)成对持仓本版未自动做, 可在 app 操作或下一版加。")
