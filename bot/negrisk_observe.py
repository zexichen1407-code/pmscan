# -*- coding: utf-8 -*-
"""
negrisk_observe.py — neg-risk maker bot 纸面/观察循环(只读, 不下任何单)

核心问题(决定这 bot 能不能赚钱):
  如果我在一个 neg-risk 事件的每条腿 NO 上挂报价引擎算出的被动买单,
  1) 每条腿真实成交率多少? (HIT = 一笔成交价 <= 我们 NO 买价)
  2) ★配齐一套(每条腿都成交)的瓶颈 = 最慢那条腿 —— 配套吞吐量(套/小时)?
     neg-risk 命门: 一条尾腿不成交 -> 永远配不齐 -> 干净满套模式下零利润。
  3) 净值: 毛边际/套 × 配套吞吐 − gas×转换次数 − fee, 到底正不正?

数据源(只读): wss://ws-subscriptions-clob.polymarket.com/ws/market
用法: python negrisk_observe.py [--seconds N] [--slug EVENT_SLUG] [--margin 0.0] [--clip 5]
     不带 --slug 则用 negrisk_candidates.json 里中价边际最高的事件。
"""
import json, sys, os, time, threading, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket
from negrisk_quote import quote_event, QParams

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HERE = os.path.dirname(__file__)
CAND = os.path.join(HERE, "negrisk_candidates.json")
LOG = os.path.join(HERE, "negrisk_observe_log.jsonl")
CKPT = os.path.join(HERE, "negrisk_observe_ckpt.json")
GAMMA = "https://gamma-api.polymarket.com"

args = sys.argv[1:]
def aval(f, d): return args[args.index(f)+1] if f in args else d
SECONDS = int(aval("--seconds", "120"))
SLUG = aval("--slug", "")
MARGIN = float(aval("--margin", "0.0"))
CLIP = float(aval("--clip", "5"))
GAS_PER_CONV = float(aval("--gas", "0.01"))   # Polygon 每次转换/merge gas, 实测<1分, 占位
CONV_EVERY = float(aval("--conv-every", "3")) # 攒几套配平转一次(摊gas)

def get(url, tries=3):
    last=None
    for _ in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
        except Exception as e: last=e; time.sleep(0.4)
    raise last
def jload(x,d):
    try: return json.loads(x) if isinstance(x,str) else (x if x is not None else d)
    except: return d
def fnum(x):
    try: return float(x)
    except: return None

# ---- 选事件 + 取 N 条腿 ----
def legs_from_slug(slug):
    evs = get(f"{GAMMA}/events?slug={slug}")
    if not evs: return None, None
    e = evs[0] if isinstance(evs, list) else evs
    legs=[]
    for m in (e.get("markets") or []):
        outs=jload(m.get("outcomes"),[]); toks=jload(m.get("clobTokenIds"),[])
        if len(outs)!=2 or len(toks)!=2: continue
        no_idx = 1 if str(outs[1]).lower()=="no" else (0 if str(outs[0]).lower()=="no" else 1)
        ya=fnum(m.get("bestAsk")); yb=fnum(m.get("bestBid"))
        if no_idx==0: ya,yb=(1-yb if yb is not None else None),(1-ya if ya is not None else None)
        if not (m.get("acceptingOrders") and m.get("enableOrderBook")): continue
        if ya is None or yb is None or not (0<ya<1) or ya<yb: continue
        legs.append({"q":str(m.get("question") or "")[:36],
                     "no_token":toks[no_idx], "yes_token":toks[1-no_idx],
                     "yes_bid":yb,"yes_ask":ya,
                     "tick":fnum(m.get("orderPriceMinTickSize")),
                     "rew_max_spread":fnum(m.get("rewardsMaxSpread")),
                     "rew_min_size":fnum(m.get("rewardsMinSize"))})
    return e.get("title"), legs

if not SLUG:
    if not os.path.exists(CAND):
        print("先跑 negrisk_scan.py"); sys.exit(1)
    import math
    cands=json.load(open(CAND,encoding="utf-8"))
    def sc(x):  # 要有边际、价差紧、且有真实流量(否则像MSI MVP那样0成交)
        if (x.get("avg_spread") or 1)>0.06 or x.get("maker_edge_per_set",0)<=0: return -9
        if (x.get("vol24") or 0) < float(aval("--min-vol","20000")): return -9
        return (x.get("edge_per_leg") or 0)*math.log10(max(10,x.get("vol24",10)))
    c=max(cands, key=sc)
    SLUG=c.get("slug")
    print(f"选中(边际×流量): {c['title']}  N={c['N_tradeable']} vol24=${c.get('vol24',0):,.0f} "
          f"中价边际{c['maker_edge_per_set']*100:.1f}¢/套")
title, legs = legs_from_slug(SLUG)   # 一律重新拉取(含 yes_token, 价格最新)

if not legs or len(legs)<3:
    print("没拿到 >=3 条腿的 neg-risk 事件"); sys.exit(1)

q = quote_event(legs, QParams(clip=CLIP, margin=MARGIN))
print(f"事件: {title}  N={q['N']} 腿")
print(f"报价: 每腿挂 NO 中价−{MARGIN*100:.1f}¢, clip {CLIP}股; 全篮毛边际 {q['gross_edge_per_set']*100:.2f}¢/套 "
      f"(每腿{(q['edge_per_leg'] or 0)*100:.2f}¢)  edge_ok={q['edge_ok']}")
rew_ok = sum(1 for l in q["legs"] if l["reward_eligible"])
tails = sum(1 for l in q["legs"] if l["is_tail"])
print(f"  返佣资格腿 {rew_ok}/{q['N']} (clip太小多半拿不到返佣)  冷门尾腿 {tails}/{q['N']}")

# token -> (leg_idx, is_no, our_no_bid)
legmeta=[]   # per leg: {q, no_bid, no_token, yes_token, is_tail}
asset_no={}  # no_token -> leg_idx
asset_yes={} # yes_token -> leg_idx
for i,l in enumerate(q["legs"]):
    legmeta.append(l)
    if l["no_token"]: asset_no[l["no_token"]]=i
    yt = legs[i].get("yes_token")
    if yt: asset_yes[yt]=i

# ---- 实时状态 ----
books={}
no_sell_seen=[0]*q["N"]    # 该腿 NO 侧主动卖单(能成交我们买单的) 总数
hits=[0]*q["N"]            # 命中数(成交价<=我们NO买价)
filled_sh=[0.0]*q["N"]     # 累计被成交股数(按命中的成交size累加)
deficits=[[] for _ in range(q["N"])]   # 每笔NO卖单价 − 我们NO买价 (<=0=命中)
comp_buys=[0]*q["N"]       # 互补: YES BUY at >= 1-no_bid (经mint也能成交我们) 计数
t0=time.time(); raw_logged=[0]
logf=open(LOG,"w",encoding="utf-8")

def on_msg(ws,msg):
    try: data=json.loads(msg)
    except: return
    for ev in (data if isinstance(data,list) else [data]):
        if not isinstance(ev,dict): continue
        et=ev.get("event_type") or ev.get("type")
        asset=ev.get("asset_id") or ev.get("asset")
        if raw_logged[0]<4:
            logf.write(json.dumps({"raw":ev})[:800]+"\n"); raw_logged[0]+=1
        if et in ("last_trade_price","trade"):
            price=fnum(ev.get("price")); size=fnum(ev.get("size")) or 0
            side=(ev.get("side") or "").upper()
            if price is None: continue
            # 直接成交: 我们NO买单被 NO 卖单打中
            if asset in asset_no:
                i=asset_no[asset]; l=legmeta[i]
                if side=="SELL":
                    no_sell_seen[i]+=1
                    deficits[i].append(price - l["no_bid"])
                    if price <= l["no_bid"]+1e-9:
                        hits[i]+=1; filled_sh[i]+=size
                        logf.write(json.dumps({"t":round(time.time()-t0,1),"leg":i,"HIT_NO":l["q"][:16],"px":price,"bid":l["no_bid"],"sz":size})+"\n")
            # 互补成交: YES BUY at >= 1-no_bid (交易所 mint 一套, 我们NO买单也成交)
            if asset in asset_yes:
                i=asset_yes[asset]; l=legmeta[i]
                if side=="BUY" and price >= (1-l["no_bid"])-1e-9:
                    comp_buys[i]+=1

def on_open(ws):
    ids=list(asset_no.keys())+list(asset_yes.keys())
    ws.send(json.dumps({"assets_ids":ids,"type":"market"}))
    print(f"\n已订阅 {len(ids)} 个 token ({len(asset_no)} NO + {len(asset_yes)} YES), 观察 {SECONDS}s...\n")
def on_err(ws,e): print("ws err:",e)

def summary():
    el=time.time()-t0
    print(f"\n==== 观察 {el:.0f}s 汇总(纸面, 不下单) ====")
    # 配套吞吐 = 最慢腿(min filled_sh), 因为一套需每腿各1股
    sets_matched = min(filled_sh) if filled_sh else 0
    binding = filled_sh.index(min(filled_sh)) if filled_sh else -1
    print(f"\n  逐腿成交(命中=成交价<=我们NO买价):")
    print(f"  {'#':>2} {'腿':<22}{'NO卖单':>7}{'命中':>6}{'命中率':>7}{'累计成交股':>11}{'互补YES买':>9}{'尾':>4}")
    for i,l in enumerate(legmeta):
        rate = (100*hits[i]/no_sell_seen[i]) if no_sell_seen[i] else 0
        print(f"  {i:>2} {str(l['q'])[:22]:<22}{no_sell_seen[i]:>7}{hits[i]:>6}{rate:>6.0f}%"
              f"{filled_sh[i]:>11.0f}{comp_buys[i]:>9}{'尾' if l['is_tail'] else '':>4}")
    print(f"\n  ★ 配套瓶颈 = 腿#{binding}({str(legmeta[binding]['q'])[:20] if binding>=0 else '-'}) 只成交 {sets_matched:.0f} 股")
    print(f"  ★ 可配齐套数 ≈ {sets_matched:.0f} 套 (= 最慢腿成交股数)")
    if el>0 and sets_matched>0:
        per_hr = sets_matched/el*3600
        gross = q["gross_edge_per_set"] * sets_matched
        n_conv = max(1, sets_matched/CONV_EVERY)
        gas = n_conv*GAS_PER_CONV
        net = gross - gas
        print(f"\n  == 经济性外推(本窗口实测速率) ==")
        print(f"  配套吞吐 ≈ {per_hr:.1f} 套/小时")
        print(f"  毛利 = {q['gross_edge_per_set']*100:.2f}¢/套 × {sets_matched:.0f}套 = ${gross:.4f}")
        print(f"  gas  = {n_conv:.0f}次转换 × ${GAS_PER_CONV} = ${gas:.4f}")
        print(f"  净利(本窗口) = ${net:.4f}   ({'正' if net>0 else '负'})")
        print(f"  外推: ${net/el*3600:.3f}/小时  (×24h ≈ ${net/el*86400:.2f}/天, 单事件单clip)")
    else:
        print(f"\n  ★ 配齐 0 套 —— 至少一条腿没成交(尾腿饿死)。干净满套模式下零利润。")
        print(f"     说明: neg-risk 命门正在这里。要么换更活的盘, 要么走'子集转换+卖YES抹平'(带方向, 后期).")
    # 边际 vs 成交率 曲线(所有腿合并)
    alld=[x for s in deficits for x in s]
    if alld:
        alld.sort()
        print(f"\n  == 边际 vs 成交率(每腿在NO买价上再抬d, 代价每套少赚 N×d) ==")
        for d in (0.0,0.005,0.01,0.02):
            catch=sum(1 for x in alld if x<=d+1e-9)
            print(f"    抬 {d*100:>4.1f}¢/腿 -> 接到 {100*catch/len(alld):>3.0f}% NO卖单  (每套让掉 {q['N']*d*100:.1f}¢)")
        print(f"  样本 NO卖单 {len(alld)} 笔; 成交价−我们买价 中位 {alld[len(alld)//2]*100:.1f}¢")
    print(f"\n  互补流(YES BUY 命中我们NO买价)合计 {sum(comp_buys)} 笔 —— 若远大于NO直接卖单,")
    print(f"  说明 neg-risk 多数流走 YES 侧, 真实成交率应把这块算进来(本版分开计, 未并入命中).")
    print(f"  原始消息+命中明细: {LOG}")
    print("  口径: 仍未扣队列优先权/延迟/部分成交/返佣(clip小多半没返佣) -> 真实更保守; 跑越久越准.")

def write_ckpt():
    json.dump({"elapsed_s":round(time.time()-t0,1),"sets_matched":min(filled_sh) if filled_sh else 0,
               "per_leg":[{"q":l["q"],"no_sell":no_sell_seen[i],"hit":hits[i],"filled":filled_sh[i]}
                          for i,l in enumerate(legmeta)]},
              open(CKPT,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
def ckpt_loop():
    while time.time()<t0+SECONDS+5:
        time.sleep(60)
        try: write_ckpt()
        except: pass
threading.Thread(target=ckpt_loop,daemon=True).start()

ws=websocket.WebSocketApp(WS_URL,on_open=on_open,on_message=on_msg,on_error=on_err)
threading.Timer(SECONDS, ws.close).start()
try:
    ws.run_forever(ping_interval=10,ping_timeout=5,reconnect=5)
finally:
    logf.close(); summary()
