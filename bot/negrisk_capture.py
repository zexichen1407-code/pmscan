# -*- coding: utf-8 -*-
"""
negrisk_capture.py — 竞争感知的纸面捕获模型(只读, 不下单)

把 [0, 假上界] 收成一个现实点估: 我们是小、慢的 maker, 排在职业 bot 后面。
维护实时盘口, 每来一笔成交, 只给我们"现有队列吃不下的溢出量":

  直接 NO 卖单(size S @ p):
     ahead = 该腿 NO 买单簿里 价>=我们买价n 的总挂量(都排在我们前面, 我们最慢)
     我们捕获 = max(0, min(clip, S − ahead))         # 只有卖单大到穿透前面所有量才轮到我们

  互补 YES 买单(size S @ y, 需 y>=1−n 才可能 mint 到我们):
     mint_qty = max(0, S − YES卖单簿里 价<=y 的总挂量)  # 先吃现成YES卖单, 剩下才被迫mint
     ahead    = 该腿 NO 买单簿里 价>=n 的总挂量
     我们捕获 = max(0, min(clip, mint_qty − ahead))

配套吞吐 = 各事件最慢腿的累计真实捕获。同时记"假上界"(全吃)对照。
用法: python negrisk_capture.py [--seconds 1800] [--events 12] [--min-vol 12000] [--max-n 16] [--clip 5] [--margin 0]
"""
import json, sys, os, time, threading, urllib.request, math
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import websocket
from negrisk_quote import quote_event, QParams

WS_URL="wss://ws-subscriptions-clob.polymarket.com/ws/market"
HERE=os.path.dirname(__file__); CAND=os.path.join(HERE,"negrisk_candidates.json")
CKPT=os.path.join(HERE,"negrisk_capture_ckpt.json"); GAMMA="https://gamma-api.polymarket.com"
a=sys.argv[1:]
def av(f,d): return a[a.index(f)+1] if f in a else d
SECONDS=int(av("--seconds","1800")); KEV=int(av("--events","12"))
MINVOL=float(av("--min-vol","12000")); MAXN=int(av("--max-n","16"))
CLIP=float(av("--clip","5")); MARGIN=float(av("--margin","0.0")); GAS=float(av("--gas","0.01"))

def get(url,tries=3):
    last=None
    for _ in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)
        except Exception as e: last=e; time.sleep(0.4)
    raise last
def jload(x,d):
    try: return json.loads(x) if isinstance(x,str) else (x if x is not None else d)
    except: return d
def fnum(x):
    try: return float(x)
    except: return None

def legs_from_slug(slug):
    evs=get(f"{GAMMA}/events?slug={slug}")
    if not evs: return None,[]
    e=evs[0] if isinstance(evs,list) else evs; legs=[]
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
    return e.get("title"),legs

if not os.path.exists(CAND): print("先跑 negrisk_scan.py"); sys.exit(1)
cands=json.load(open(CAND,encoding="utf-8"))
def sc(x):
    if (x.get("avg_spread") or 1)>0.06 or x.get("maker_edge_per_set",0)<=0: return -9
    if (x.get("vol24") or 0)<MINVOL or x.get("N_tradeable",0)>MAXN: return -9
    return (x.get("edge_per_leg") or 0)*math.log10(max(10,x.get("vol24",10)))
ranked=sorted([x for x in cands if sc(x)>-9],key=sc,reverse=True)[:KEV]
if not ranked: print("无符合事件"); sys.exit(1)

events=[]; asset_no={}; asset_yes={}; books={}
print(f"挑选 {len(ranked)} 个事件(竞争感知捕获):")
for c in ranked:
    title,legs=legs_from_slug(c.get("slug"))
    if len(legs)<3: continue
    q=quote_event(legs,QParams(clip=CLIP,margin=MARGIN))
    qlegs=[]
    for ql,raw in zip(q["legs"],legs):
        qlegs.append({"q":ql["q"],"no_token":ql["no_token"],"yes_token":raw.get("yes_token"),
                      "no_bid":ql["no_bid"],"is_tail":ql["is_tail"]})
        if ql["no_token"]: asset_no[ql["no_token"]]=(len(events),len(qlegs)-1)
        if raw.get("yes_token"): asset_yes[raw["yes_token"]]=(len(events),len(qlegs)-1)
    events.append({"title":title or c.get("title"),"N":q["N"],"gross":q["gross_edge_per_set"],"legs":qlegs,
                   "real":[0.0]*q["N"],"naive":[0.0]*q["N"],"no_sell":[0]*q["N"],"comp":[0]*q["N"]})
    print(f"  {str(title)[:34]:<34} N={q['N']:>2} 毛边际{(q['gross_edge_per_set'] or 0)*100:>5.1f}¢ vol=${c.get('vol24',0):,.0f}")

def apply_book(asset,bids,asks):
    bk=books.setdefault(asset,{"bids":{},"asks":{}})
    bk["bids"]={fnum(x["price"]):fnum(x["size"]) for x in bids if fnum(x.get("price")) is not None}
    bk["asks"]={fnum(x["price"]):fnum(x["size"]) for x in asks if fnum(x.get("price")) is not None}
def apply_change(asset,changes):
    bk=books.setdefault(asset,{"bids":{},"asks":{}})
    for ch in changes:
        sd=(ch.get("side") or "").lower(); p=fnum(ch.get("price")); s=fnum(ch.get("size"))
        if p is None: continue
        book=bk["bids"] if sd in ("buy","bid") else bk["asks"]
        if not s: book.pop(p,None)
        else: book[p]=s
def depth_ge(token,price):   # NO买单簿 价>=price 的总挂量
    bk=books.get(token);
    return sum(s for p,s in bk["bids"].items() if p>=price-1e-9) if bk else 0.0
def depth_le_ask(token,price):  # YES卖单簿 价<=price 的总挂量
    bk=books.get(token)
    return sum(s for p,s in bk["asks"].items() if p<=price+1e-9) if bk else 0.0

t0=time.time()
def on_msg(ws,msg):
    try: data=json.loads(msg)
    except: return
    for ev in (data if isinstance(data,list) else [data]):
        if not isinstance(ev,dict): continue
        et=ev.get("event_type") or ev.get("type"); asset=ev.get("asset_id") or ev.get("asset")
        if et=="book":
            apply_book(asset, ev.get("bids") or ev.get("buys") or [], ev.get("asks") or ev.get("sells") or [])
        elif et in ("price_change","agg_orderbook","tick_size_change"):
            if ev.get("changes"): apply_change(asset, ev["changes"])
        elif et in ("last_trade_price","trade"):
            price=fnum(ev.get("price")); S=fnum(ev.get("size")) or 0; side=(ev.get("side") or "").upper()
            if price is None: continue
            if asset in asset_no:
                ei,li=asset_no[asset]; E=events[ei]; n=E["legs"][li]["no_bid"]
                if side=="SELL":
                    E["no_sell"][li]+=1
                    if price<=n+1e-9:
                        E["naive"][li]+=S
                        ahead=depth_ge(asset,n)
                        E["real"][li]+=max(0.0,min(CLIP,S-ahead))
            if asset in asset_yes:
                ei,li=asset_yes[asset]; E=events[ei]; leg=E["legs"][li]; n=leg["no_bid"]
                if side=="BUY" and price>=(1-n)-1e-9:
                    E["comp"][li]+=1; E["naive"][li]+=S
                    mint_qty=max(0.0, S-depth_le_ask(asset,price))
                    ahead=depth_ge(leg["no_token"],n)
                    E["real"][li]+=max(0.0,min(CLIP,mint_qty-ahead))

def on_open(ws):
    ids=list(asset_no.keys())+list(asset_yes.keys())
    ws.send(json.dumps({"assets_ids":ids,"type":"market"}))
    print(f"\n已订阅 {len(ids)} token across {len(events)} 事件, 竞争感知观察 {SECONDS}s...\n")
def on_err(ws,e): print("ws err:",e)

def snap():
    rows=[]; tr=0.0;tn=0.0;gr=0.0;gn=0.0
    for E in events:
        sr=min(E["real"]) if E["real"] else 0; sn=min(E["naive"]) if E["naive"] else 0
        tr+=sr; tn+=sn; gr+=(E["gross"] or 0)*sr; gn+=(E["gross"] or 0)*sn
        rows.append((E["title"],E["N"],sr,sn,sum(E["no_sell"]),sum(E["comp"])))
    return rows,tr,tn,gr,gn
def write_ckpt():
    rows,tr,tn,gr,gn=snap()
    json.dump({"elapsed_s":round(time.time()-t0,1),"sets_realistic":round(tr,1),"sets_naive_upper":round(tn,1),
               "gross_realistic_usd":round(gr,4),"gross_naive_usd":round(gn,4),
               "events":[{"t":r[0][:30],"N":r[1],"real":round(r[2],1),"naive":round(r[3],1),"no_sell":r[4],"comp":r[5]} for r in rows]},
              open(CKPT,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
def ckpt_loop():
    while time.time()<t0+SECONDS+5:
        time.sleep(60)
        try: write_ckpt()
        except: pass
threading.Thread(target=ckpt_loop,daemon=True).start()

def summary():
    el=time.time()-t0; rows,tr,tn,gr,gn=snap()
    print(f"\n==== 竞争感知捕获 {el:.0f}s 汇总(纸面) ====")
    print(f"{'#':>2} {'事件':<30}{'N':>3}{'真实配套':>9}{'假上界':>8}{'NO卖':>6}{'互补买':>7}{'毛边际':>7}")
    for i,(t,N,sr,sn,ns,cp) in enumerate(rows):
        print(f"{i:>2} {str(t)[:30]:<30}{N:>3}{sr:>9.1f}{sn:>8.0f}{ns:>6}{cp:>7}{(events[i]['gross'] or 0)*100:>6.1f}¢")
    print(f"\n  ★ 真实可配套数(竞争感知) = {tr:.1f} 套   假上界(全吃) = {tn:.0f} 套")
    nz=sum(1 for r in rows if r[2]>0.5)
    print(f"  能配≥1套的事件: {nz}/{len(rows)}")
    if el>0:
        n_conv=max(1,tr/3); gas=n_conv*GAS; net=gr-gas
        print(f"\n  真实毛利 ${gr:.4f} − gas ${gas:.4f} = 净 ${net:.4f}  外推 ${net/el*3600:.3f}/小时")
        print(f"  (clip {CLIP}股/腿, {len(events)}盘并行, 我们=队尾最慢; 未计返佣[clip小拿不到])")
        print(f"  对照: 假上界净外推 ${(gn-max(1,tn/3)*GAS)/el*3600:.2f}/小时 —— 真实是它的 {100*net/max(1e-9,gn-max(1,tn/3)*GAS):.0f}%")
    print(f"\n  口径: ahead=盘口里排我们前面的挂量(我们最慢, 全算前面); 比真实更保守一点.")

ws=websocket.WebSocketApp(WS_URL,on_open=on_open,on_message=on_msg,on_error=on_err)
threading.Timer(SECONDS, ws.close).start()
try: ws.run_forever(ping_interval=10,ping_timeout=5,reconnect=5)
finally: summary()
