import ijson, json, collections, urllib.request, urllib.error, bisect, statistics
from datetime import datetime, timezone

PATH = r"C:/Users/zexi/pmscan/audit/raw_activity_full.json"
# Pick a moneyline in-play soccer match: fifwc-ger-kor-2026-06-14 (975 trades)
ESLUG = "fifwc-ger-kor-2026-06-14"
UA={"User-Agent":"Mozilla/5.0"}

fills=[]
conds=set()
with open(PATH,"rb") as f:
    for rec in ijson.items(f,"item"):
        if rec.get("eventSlug")==ESLUG and rec.get("type")=="TRADE":
            d={k:rec.get(k) for k in ("timestamp","size","usdcSize","price","asset","side","outcome","outcomeIndex","slug","conditionId","title")}
            for nk in ("price","size","usdcSize"):
                try: d[nk]=float(d[nk])
                except: pass
            fills.append(d)
            conds.add(rec.get("conditionId"))
print("soccer fills:",len(fills),"conds:",len(conds))
# group by slug+outcome
bm=collections.Counter((f["slug"],f["outcome"]) for f in fills)
for k,c in bm.most_common(12): print("  ",c,k)

# pick the single sub-market with most BUY fills
buys=[f for f in fills if f["side"]=="BUY"]
bc=collections.Counter((f["slug"],f["outcome"],f["asset"]) for f in buys)
(top_slug,top_out,top_asset),topn = bc.most_common(1)[0]
print("\nTOP submarket:",top_slug,top_out,"buys",topn,"asset",top_asset)

def get(url):
    req=urllib.request.Request(url,headers=UA)
    try:
        with urllib.request.urlopen(req,timeout=40) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return {"err":e.code}

ph=get(f"https://clob.polymarket.com/prices-history?market={top_asset}&interval=max&fidelity=1")
hist=ph.get("history",[]) if isinstance(ph,dict) else []
print("price points:",len(hist))
if hist:
    a=datetime.fromtimestamp(hist[0]['t'],timezone.utc);b=datetime.fromtimestamp(hist[-1]['t'],timezone.utc)
    print("range",a.isoformat(),"->",b.isoformat())
    ts=[x['t'] for x in hist];ps=[x['p'] for x in hist]
    def mk(t):
        i=bisect.bisect_left(ts,t)
        if i<=0:return ps[0]
        if i>=len(ts):return ps[-1]
        t0,t1=ts[i-1],ts[i];p0,p1=ps[i-1],ps[i]
        return p0 if t1==t0 else p0+(p1-p0)*(t-t0)/(t1-t0)
    sub=[f for f in buys if f["asset"]==top_asset and ts[0]<=f["timestamp"]<=ts[-1]]
    offs=[f["price"]-mk(f["timestamp"]) for f in sub]
    # lead-lag
    best=None
    for lag in range(-300,301,30):
        e=statistics.median([abs(f["price"]-mk(f["timestamp"]+lag)) for f in sub])
        if best is None or e<best[1]: best=(lag,e)
    if offs:
        print(f"submarket buys in-range: {len(offs)}")
        print(f"OFFSET fill-market: mean {statistics.mean(offs):+.4f} median {statistics.median(offs):+.4f} stdev {statistics.pstdev(offs):.4f}")
        below=sum(1 for o in offs if o<0)
        print(f"below market: {below}/{len(offs)} ({100*below/len(offs):.0f}%)")
        print(f"best lag: {best[0]}s median err {best[1]:.4f}")
