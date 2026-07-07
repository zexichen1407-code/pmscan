import json, urllib.request, urllib.error
from datetime import datetime, timezone
import bisect, statistics

UA = {"User-Agent": "Mozilla/5.0"}
COBOLLI = "104121662816591135157633034467926697984733660874566468356740527855523275425437"
ZVEREV  = "32113956590504382638811631788145724904940827912112275742691865237227049243037"

def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

# Pull finest fidelity over the 2026 match window. interval=max worked; try fidelity=1 with that.
def pull(tok):
    # Try explicit 2026 window first
    s, e = 1780770000, 1780870000   # 2026-06-07 ~ pre-match to post
    url=f"https://clob.polymarket.com/prices-history?market={tok}&startTs={s}&endTs={e}&fidelity=1"
    st,b=get(url); d=json.loads(b); h=d.get("history",[])
    if not h:
        url=f"https://clob.polymarket.com/prices-history?market={tok}&interval=max&fidelity=1"
        st,b=get(url); d=json.loads(b); h=d.get("history",[])
    return h

ph = {"COBOLLI": pull(COBOLLI), "ZVEREV": pull(ZVEREV)}
for k,v in ph.items():
    a=datetime.fromtimestamp(v[0]['t'],timezone.utc); b=datetime.fromtimestamp(v[-1]['t'],timezone.utc)
    ts=[x['t'] for x in v]; difs=sorted(ts[i+1]-ts[i] for i in range(len(ts)-1))
    print(k, "points",len(v),"range",a.isoformat(),"->",b.isoformat(),"median_spacing_s",difs[len(difs)//2] if difs else None)

fills = json.load(open(r"C:/Users/zexi/pmscan/audit/_tennis_fills.json"))
trades=[f for f in fills if f["type"]=="TRADE" and f["side"]=="BUY"]

# Determine offset between fill epoch and CLOB epoch.
# Fill ts ~1749301479 (2025); CLOB ~1780770000+ (2026). Add ~1 yr.
YEAR = 31536000
def best_offset():
    # candidate offsets: 0, +1yr, +1yr+1day ... pick the one minimizing |fill_price - clob_price|
    cands = [365*86400, 366*86400, 31543033, 0]
    # build interp for cobolli
    cob = ph["COBOLLI"]; cts=[x['t'] for x in cob]; cps=[x['p'] for x in cob]
    def interp(series_ts, series_p, t):
        i=bisect.bisect_left(series_ts,t)
        if i<=0: return series_p[0]
        if i>=len(series_ts): return series_p[-1]
        t0,t1=series_ts[i-1],series_ts[i]; p0,p1=series_p[i-1],series_p[i]
        if t1==t0: return p0
        return p0+(p1-p0)*(t-t0)/(t1-t0)
    cobfills=[f for f in trades if f["outcome"]=="Flavio Cobolli"]
    results=[]
    for off in cands:
        errs=[]
        for f in cobfills:
            t=f["timestamp"]+off
            if cts[0]<=t<=cts[-1]:
                errs.append(abs(f["price"]-interp(cts,cps,t)))
        if errs:
            results.append((statistics.median(errs), off, len(errs)))
    results.sort()
    return results

print("\nOffset search (median |fill-clob|, offset_s, n_in_range):")
for r in best_offset():
    print("  ", round(r[0],4), r[1], r[2])

json.dump(ph, open(r"C:/Users/zexi/pmscan/audit/_ph_all.json","w"))
