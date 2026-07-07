import json, bisect, statistics
from datetime import datetime, timezone

ph = json.load(open(r"C:/Users/zexi/pmscan/audit/_ph_all.json"))
rows = json.load(open(r"C:/Users/zexi/pmscan/audit/_aligned.json"))

series = {}
for name in ("COBOLLI","ZVEREV"):
    s = sorted(ph[name], key=lambda x:x["t"])
    series[name] = ([x["t"] for x in s], [x["p"] for x in s])

def mkt_at(name, t):
    ts,ps=series[name]
    i=bisect.bisect_left(ts,t)
    if i<=0: return ps[0]
    if i>=len(ts): return ps[-1]
    t0,t1=ts[i-1],ts[i];p0,p1=ps[i-1],ps[i]
    return p0 if t1==t0 else p0+(p1-p0)*(t-t0)/(t1-t0)

# LEAD-LAG: for each fill, compute |fill - market(t+lag)| across lag grid.
# The lag minimizing error tells whether his price tracks PAST market (he lags, lag<0)
# or matches current market (lag~0). We test market shifted by delta seconds.
lags = list(range(-600, 601, 30))  # -10min .. +10min
for name_out, tok in [("Flavio Cobolli","COBOLLI"),("Alexander Zverev","ZVEREV")]:
    rs=[r for r in rows if r["side_outcome"]==name_out]
    best=None
    table=[]
    for lag in lags:
        errs=[]
        for r in rs:
            m=mkt_at(tok, r["t"]+lag)   # market at fill time + lag
            errs.append(abs(r["fill_price"]-m))
        med=statistics.median(errs)
        table.append((lag,med))
        if best is None or med<best[1]: best=(lag,med)
    print(f"\n=== {name_out}: lead-lag (lag s, median|fill-mkt(t+lag)|) ===")
    # print a few around best
    print("  best lag:", best[0], "s  median err:", round(best[1],4))
    for lag,med in table:
        if abs(lag-best[0])<=120 or lag in (-600,-300,0,300,600):
            mark=" <--" if lag==best[0] else ""
            print(f"   lag {lag:+5d}s  med {med:.4f}{mark}")

# CHASE SPEED: split match into time buckets; in each, mean offset + market volatility.
# Does offset widen (he falls behind) when market moves fast?
print("\n\n=== Offset vs market velocity (per fill) ===")
for name_out, tok in [("Flavio Cobolli","COBOLLI"),("Alexander Zverev","ZVEREV")]:
    rs=sorted([r for r in rows if r["side_outcome"]==name_out], key=lambda r:r["t"])
    ts,ps=series[tok]
    pts=[]
    for r in rs:
        t=r["t"]
        # market velocity: change over prior 120s (cents/min)
        m_now=mkt_at(tok,t); m_prev=mkt_at(tok,t-120)
        vel=(m_now-m_prev)/2.0   # per minute
        pts.append((vel, r["offset_interp"], r["offset_prev"]))
    # correlation between |velocity| and offset_prev (lag-against-last-known)
    if len(pts)>5:
        av=[abs(p[0]) for p in pts]; op=[p[2] for p in pts]
        # pearson
        n=len(pts); ma=sum(av)/n; mo=sum(op)/n
        cov=sum((av[i]-ma)*(op[i]-mo) for i in range(n))
        va=sum((x-ma)**2 for x in av)**.5; vo=sum((x-mo)**2 for x in op)**.5
        corr=cov/(va*vo) if va*vo else 0
        # split into rising vs falling market
        rising=[p for p in pts if p[0]>0.002]
        falling=[p for p in pts if p[0]<-0.002]
        flat=[p for p in pts if abs(p[0])<=0.002]
        def mo_(g,idx=2): return statistics.mean([x[idx] for x in g]) if g else float('nan')
        print(f"\n{name_out}:")
        print(f"  corr(|mkt velocity|, offset_prev) = {corr:+.3f}")
        print(f"  market RISING (n={len(rising)}): mean offset_interp {mo_(rising,1):+.4f}  offset_prev {mo_(rising,2):+.4f}")
        print(f"  market FALLING(n={len(falling)}): mean offset_interp {mo_(falling,1):+.4f}  offset_prev {mo_(falling,2):+.4f}")
        print(f"  market FLAT   (n={len(flat)}): mean offset_interp {mo_(flat,1):+.4f}  offset_prev {mo_(flat,2):+.4f}")

# Time between consecutive fills (how fast he re-quotes / chases)
print("\n\n=== Re-quote cadence (gap between consecutive BUY fills, same side) ===")
for name_out in ("Flavio Cobolli","Alexander Zverev"):
    rs=sorted([r for r in rows if r["side_outcome"]==name_out], key=lambda r:r["t"])
    gaps=[rs[i+1]["t"]-rs[i]["t"] for i in range(len(rs)-1)]
    gaps=[g for g in gaps if g>=0]
    if gaps:
        print(f"  {name_out}: n_gaps={len(gaps)} median {statistics.median(gaps):.0f}s  mean {statistics.mean(gaps):.0f}s  p90 {sorted(gaps)[int(len(gaps)*0.9)]:.0f}s  max {max(gaps)}s")
