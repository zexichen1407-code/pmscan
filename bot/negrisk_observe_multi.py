# -*- coding: utf-8 -*-
"""
negrisk_observe_multi.py — 同时纸面观察 top-K 个 neg-risk 事件(只读, 不下单)

为什么多盘: 单个 neg-risk 盘脉冲式、长时间 0 成交; 同时盯 K 个活跃盘, 成交样本攒得快得多。

每个事件按报价引擎在每条腿 NO 上"挂"被动买单, 然后:
  HIT(直接)   = 该腿 NO 的一笔 SELL 成交价 <= 我们 NO 买价
  COMP(互补)  = 该腿 YES 的一笔 BUY 成交价 >= 1−我们NO买价 (交易所 mint 一套也成交我们)
  配套吞吐/事件 = 该事件最慢腿的累计成交股数(一套需每腿各 1 股) = neg-risk 命门
聚合: 全体腿成交率分布 + 各事件能配几套 + 净值(毛边际×套 − gas).

用法: python negrisk_observe_multi.py [--seconds 1800] [--events 8] [--min-vol 15000] [--max-n 16] [--clip 5] [--margin 0]
"""
import json, sys, os, time, threading, urllib.request, math
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket
from negrisk_quote import quote_event, QParams

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HERE = os.path.dirname(__file__)
CAND = os.path.join(HERE, "negrisk_candidates.json")
CKPT = os.path.join(HERE, "negrisk_obsmulti_ckpt.json")
GAMMA = "https://gamma-api.polymarket.com"

a = sys.argv[1:]
def av(f, d): return a[a.index(f)+1] if f in a else d
SECONDS = int(av("--seconds", "1800"))
KEV = int(av("--events", "8"))
MINVOL = float(av("--min-vol", "15000"))
MAXN = int(av("--max-n", "16"))
CLIP = float(av("--clip", "5"))
MARGIN = float(av("--margin", "0.0"))
GAS = float(av("--gas", "0.01"))

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

def legs_from_slug(slug):
    evs = get(f"{GAMMA}/events?slug={slug}")
    if not evs: return None, []
    e = evs[0] if isinstance(evs,list) else evs
    legs=[]
    for m in (e.get("markets") or []):
        outs=jload(m.get("outcomes"),[]); toks=jload(m.get("clobTokenIds"),[])
        if len(outs)!=2 or len(toks)!=2: continue
        no_idx=1 if str(outs[1]).lower()=="no" else (0 if str(outs[0]).lower()=="no" else 1)
        ya=fnum(m.get("bestAsk")); yb=fnum(m.get("bestBid"))
        if no_idx==0: ya,yb=(1-yb if yb is not None else None),(1-ya if ya is not None else None)
        if not (m.get("acceptingOrders") and m.get("enableOrderBook")): continue
        if ya is None or yb is None or not (0<ya<1) or ya<yb: continue
        legs.append({"q":str(m.get("question") or "")[:30],"no_token":toks[no_idx],"yes_token":toks[1-no_idx],
                     "yes_bid":yb,"yes_ask":ya,"tick":fnum(m.get("orderPriceMinTickSize")),
                     "rew_max_spread":fnum(m.get("rewardsMaxSpread")),"rew_min_size":fnum(m.get("rewardsMinSize"))})
    return e.get("title"), legs

# ---- 选 top-K 事件 ----
if not os.path.exists(CAND):
    print("先跑 negrisk_scan.py"); sys.exit(1)
cands=json.load(open(CAND,encoding="utf-8"))
def sc(x):
    if (x.get("avg_spread") or 1)>0.06 or x.get("maker_edge_per_set",0)<=0: return -9
    if (x.get("vol24") or 0)<MINVOL or x.get("N_tradeable",0)>MAXN: return -9
    return (x.get("edge_per_leg") or 0)*math.log10(max(10,x.get("vol24",10)))
ranked=sorted([x for x in cands if sc(x)>-9], key=sc, reverse=True)[:KEV]
if not ranked:
    print("没有符合条件的事件(放宽 --min-vol/--max-n)"); sys.exit(1)

events=[]; asset_no={}; asset_yes={}
print(f"挑选 {len(ranked)} 个事件(边际×流量, N<={MAXN}, vol>=${MINVOL:,.0f}):")
for ei,c in enumerate(ranked):
    title, legs = legs_from_slug(c.get("slug"))
    if len(legs)<3: continue
    q=quote_event(legs, QParams(clip=CLIP, margin=MARGIN))
    qlegs=[]
    for li,(ql,raw) in enumerate(zip(q["legs"], legs)):
        qlegs.append({"q":ql["q"],"no_token":ql["no_token"],"yes_token":raw.get("yes_token"),
                      "no_bid":ql["no_bid"],"is_tail":ql["is_tail"]})
        if ql["no_token"]: asset_no[ql["no_token"]]=(len(events),li)
        if raw.get("yes_token"): asset_yes[raw["yes_token"]]=(len(events),li)
    events.append({"title":title or c.get("title"),"N":q["N"],
                   "gross":q["gross_edge_per_set"],"legs":qlegs,
                   "no_sell":[0]*q["N"],"hit":[0]*q["N"],"filled":[0.0]*q["N"],
                   "comp":[0]*q["N"],"filled_c":[0.0]*q["N"],"deficit":[[] for _ in range(q["N"])]})
    print(f"  [{ei}] {str(title)[:34]:<34} N={q['N']:>2} 毛边际{ (q['gross_edge_per_set'] or 0)*100:>5.1f}¢/套 "
          f"vol24=${c.get('vol24',0):,.0f} 尾腿{sum(1 for l in qlegs if l['is_tail'])}")

t0=time.time()
def on_msg(ws,msg):
    try: data=json.loads(msg)
    except: return
    for ev in (data if isinstance(data,list) else [data]):
        if not isinstance(ev,dict): continue
        et=ev.get("event_type") or ev.get("type")
        if et not in ("last_trade_price","trade"): continue
        asset=ev.get("asset_id") or ev.get("asset")
        price=fnum(ev.get("price")); size=fnum(ev.get("size")) or 0
        side=(ev.get("side") or "").upper()
        if price is None: continue
        if asset in asset_no:
            ei,li=asset_no[asset]; E=events[ei]; bid=E["legs"][li]["no_bid"]
            if side=="SELL":
                E["no_sell"][li]+=1; E["deficit"][li].append(price-bid)
                if price<=bid+1e-9: E["hit"][li]+=1; E["filled"][li]+=size
        if asset in asset_yes:
            ei,li=asset_yes[asset]; E=events[ei]; bid=E["legs"][li]["no_bid"]
            if side=="BUY" and price>=(1-bid)-1e-9:
                E["comp"][li]+=1; E["filled_c"][li]+=size   # 互补成交也填我们的NO仓

def on_open(ws):
    ids=list(asset_no.keys())+list(asset_yes.keys())
    ws.send(json.dumps({"assets_ids":ids,"type":"market"}))
    print(f"\n已订阅 {len(ids)} token ({len(asset_no)}NO+{len(asset_yes)}YES) across {len(events)} 事件, 观察 {SECONDS}s...\n")
def on_err(ws,e): print("ws err:",e)

def snapshot():
    rows=[]; tot_d=0.0; tot_u=0.0; tot_gross_d=0.0; tot_gross_u=0.0
    tot_nosell=0; tot_hit=0; tot_comp=0
    for E in events:
        sets_d=min(E["filled"]) if E["filled"] else 0                          # 仅直接NO卖单(下界)
        sets_u=min(f+c for f,c in zip(E["filled"],E["filled_c"])) if E["filled"] else 0  # +互补(上界)
        ns=sum(E["no_sell"]); h=sum(E["hit"]); cp=sum(E["comp"])
        tot_d+=sets_d; tot_u+=sets_u
        tot_gross_d+=(E["gross"] or 0)*sets_d; tot_gross_u+=(E["gross"] or 0)*sets_u
        tot_nosell+=ns; tot_hit+=h; tot_comp+=cp
        rows.append((E["title"],E["N"],sets_d,sets_u,ns,h,cp,E["gross"]))
    return rows, tot_d, tot_u, tot_gross_d, tot_gross_u, tot_nosell, tot_hit, tot_comp

def write_ckpt():
    rows,td,tu,tgd,tgu,tn,th,tc=snapshot()
    json.dump({"elapsed_s":round(time.time()-t0,1),
               "sets_direct":round(td,1),"sets_with_complement":round(tu,1),
               "gross_usd_direct":round(tgd,4),"gross_usd_upper":round(tgu,4),
               "agg_no_sell":tn,"agg_hit":th,"agg_comp_buy":tc,
               "events":[{"title":r[0],"N":r[1],"sets_d":round(r[2],1),"sets_u":round(r[3],1),
                          "no_sell":r[4],"hit":r[5],"comp":r[6]} for r in rows]},
              open(CKPT,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
def ckpt_loop():
    while time.time()<t0+SECONDS+5:
        time.sleep(60)
        try: write_ckpt()
        except: pass
threading.Thread(target=ckpt_loop,daemon=True).start()

def summary():
    el=time.time()-t0
    rows,td,tu,tgd,tgu,tn,th,tc=snapshot()
    print(f"\n==== 多盘观察 {el:.0f}s 汇总(纸面, 不下单) ====")
    print(f"{'#':>2} {'事件':<30}{'N':>3}{'配套直接':>8}{'配套+互补':>9}{'NO卖':>6}{'互补YES买':>9}{'毛边际':>7}")
    for i,(title,N,sd,su,ns,h,cp,g) in enumerate(rows):
        print(f"{i:>2} {str(title)[:30]:<30}{N:>3}{sd:>8.0f}{su:>9.0f}{ns:>6}{cp:>9}{(g or 0)*100:>6.1f}¢")
    print(f"\n  聚合成交流: NO主动卖单 {tn} 笔(直接命中{th}); 互补YES买入 {tc} 笔(经mint也成交我们NO)")
    print(f"  ★ 配套(仅直接NO卖) = {td:.0f} 套   ★ 配套(含互补YES买) = {tu:.0f} 套")
    print(f"    —— 真实值在两者之间(互补单是否成交我们, 取决于当时YES卖侧深度, 这里给上下界)")
    nd=sum(1 for r in rows if r[2]>0); nu=sum(1 for r in rows if r[3]>0)
    print(f"  能配≥1套的事件: 仅直接 {nd}/{len(rows)}; 含互补 {nu}/{len(rows)}")
    if tu>0 and el>0:
        for lab,ts,tg in (("下界(仅直接)",td,tgd),("上界(含互补)",tu,tgu)):
            n_conv=max(1, ts/3); gas=n_conv*GAS; net=tg-gas
            print(f"  {lab}: 毛利${tg:.4f} − gas${gas:.4f} = 净${net:.4f}  外推 ${net/el*3600:.3f}/小时")
        print(f"  (clip {CLIP}股/腿, {len(events)}盘并行; 未计返佣[clip小多半没]; 跑越久越准)")
    else:
        print(f"\n  ★ 含互补也配齐 0 套 —— 这批盘当前流量太稀(脉冲). 需更久或换更活的盘.")
    alld=[x for E in events for s in E["deficit"] for x in s]
    if alld:
        alld.sort()
        print(f"\n  边际vs成交率: 每腿再抬d -> 接到NO卖单%:")
        for d in (0.0,0.005,0.01,0.02):
            print(f"    +{d*100:.1f}¢ -> {100*sum(1 for x in alld if x<=d+1e-9)/len(alld):.0f}%")
        print(f"  样本 {len(alld)} 笔, 中位差 {alld[len(alld)//2]*100:.1f}¢")

ws=websocket.WebSocketApp(WS_URL,on_open=on_open,on_message=on_msg,on_error=on_err)
threading.Timer(SECONDS, ws.close).start()
try:
    ws.run_forever(ping_interval=10,ping_timeout=5,reconnect=5)
finally:
    summary()
