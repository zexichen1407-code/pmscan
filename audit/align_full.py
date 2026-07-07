import json, bisect, statistics
from datetime import datetime, timezone

ph = json.load(open(r"C:/Users/zexi/pmscan/audit/_ph_all.json"))
fills = json.load(open(r"C:/Users/zexi/pmscan/audit/_tennis_fills.json"))
trades = [f for f in fills if f["type"]=="TRADE" and f["side"]=="BUY"]

series = {}
for name in ("COBOLLI","ZVEREV"):
    s = sorted(ph[name], key=lambda x:x["t"])
    series[name] = ([x["t"] for x in s], [x["p"] for x in s])

def interp(ts, ps, t):
    i = bisect.bisect_left(ts, t)
    if i<=0: return ps[0], "extrap_left"
    if i>=len(ts): return ps[-1], "extrap_right"
    t0,t1=ts[i-1],ts[i]; p0,p1=ps[i-1],ps[i]
    if t1==t0: return p0,"exact"
    return p0+(p1-p0)*(t-t0)/(t1-t0), "interp"

def nearest_prev(ts, ps, t):
    # last known market price at or before t (more realistic for a resting bid)
    i = bisect.bisect_right(ts, t)-1
    if i<0: return ps[0]
    return ps[i]

OUT = {"Flavio Cobolli":"COBOLLI", "Alexander Zverev":"ZVEREV"}

rows = []
for f in trades:
    name = OUT.get(f["outcome"])
    if not name: continue
    ts, ps = series[name]
    t = f["timestamp"]
    mkt_i, mode = interp(ts, ps, t)
    mkt_prev = nearest_prev(ts, ps, t)
    rows.append({
        "t": t, "side_outcome": f["outcome"], "token": name,
        "fill_price": f["price"], "size": f["size"], "usdc": f["usdcSize"],
        "mkt_interp": round(mkt_i,4), "mkt_prev": round(mkt_prev,4),
        "offset_interp": round(f["price"]-mkt_i,4),     # his - market
        "offset_prev": round(f["price"]-mkt_prev,4),
    })
rows.sort(key=lambda r:r["t"])

print("Aligned BUY fills:", len(rows))
for name in ("Flavio Cobolli","Alexander Zverev"):
    rs=[r for r in rows if r["side_outcome"]==name]
    offs=[r["offset_interp"] for r in rs]
    offp=[r["offset_prev"] for r in rs]
    fp=[r["fill_price"] for r in rs]
    mp=[r["mkt_interp"] for r in rs]
    usdc=sum(r["usdc"] for r in rs)
    print(f"\n=== {name} ({len(rs)} buys, ${usdc:,.0f}) ===")
    print(f"  fill price   range {min(fp):.3f}..{max(fp):.3f}  mean {statistics.mean(fp):.4f}")
    print(f"  market price range {min(mp):.3f}..{max(mp):.3f}  mean {statistics.mean(mp):.4f}")
    print(f"  OFFSET (fill - market, interp):  mean {statistics.mean(offs):+.4f}  median {statistics.median(offs):+.4f}  stdev {statistics.pstdev(offs):.4f}")
    print(f"     min {min(offs):+.4f}  max {max(offs):+.4f}")
    print(f"  OFFSET (fill - last-known market): mean {statistics.mean(offp):+.4f}  median {statistics.median(offp):+.4f}")
    below = sum(1 for o in offs if o<0); above=sum(1 for o in offs if o>0)
    print(f"  fills BELOW market: {below}/{len(rs)} ({100*below/len(rs):.0f}%)   above: {above}")
    # how far below in cents on the fills that are below
    belows=[o for o in offs if o<0]
    if belows:
        print(f"     when below: mean {statistics.mean(belows):+.4f} ({statistics.mean(belows)*100:+.2f} cents), median {statistics.median(belows)*100:+.2f}c")

json.dump(rows, open(r"C:/Users/zexi/pmscan/audit/_aligned.json","w"), indent=1)
