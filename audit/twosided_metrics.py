import pickle, json, statistics, math

with open(r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl","rb") as f:
    sel = pickle.load(f)

def pct(data,p):
    d=sorted(data)
    if not d: return float('nan')
    k=(len(d)-1)*p; lo=int(math.floor(k)); hi=int(math.ceil(k))
    if lo==hi: return d[lo]
    return d[lo]+(d[hi]-d[lo])*(k-lo)

# ============ 1) TARGET EDGE: p + q and edge = 1 - p - q ============
edges=[]; pqs=[]
for cid,c in sel.items():
    p = c["b0n"]/c["b0s"]   # size-weighted avg BUY price outcome 0 (Yes-side)
    q = c["b1n"]/c["b1s"]   # size-weighted avg BUY price outcome 1 (No-side)
    pq = p+q
    pqs.append(pq); edges.append(1.0-pq)
    c["_p"]=p; c["_q"]=q; c["_pq"]=pq; c["_edge"]=1.0-pq

edge_med=statistics.median(edges)
edge_p25=pct(edges,0.25); edge_p75=pct(edges,0.75)
pq_med=statistics.median(pqs)
# "ceiling he rarely exceeds" = high percentile of p+q (i.e. the worst pair cost he accepts)
pq_p90=pct(pqs,0.90); pq_p95=pct(pqs,0.95)
print("=== 1) TARGET EDGE ===")
print(f"n markets = {len(edges)}")
print(f"p+q  : median={pq_med:.4f}  p25={pct(pqs,0.25):.4f}  p75={pct(pqs,0.75):.4f}  p90={pq_p90:.4f}  p95={pq_p95:.4f}")
print(f"edge(1-p-q): median={edge_med:.4f}  p25={edge_p25:.4f}  p75={edge_p75:.4f}")
print(f"edge p10={pct(edges,0.10):.4f} p90={pct(edges,0.90):.4f}")
neg=sum(1 for e in edges if e<0)
print(f"markets with negative edge (paid >1.0 for pair): {neg} ({neg/len(edges)*100:.1f}%)")

# ============ 2) INTER-FILL CADENCE ============
gaps=[]
for cid,c in sel.items():
    ts=sorted(t for (t,oi,side,pr,sz) in c["fills"])
    for a,b in zip(ts,ts[1:]):
        d=b-a
        if d>=0:
            gaps.append(d)
print("\n=== 2) INTER-FILL CADENCE (seconds between consecutive fills, per market) ===")
print(f"n gaps = {len(gaps)}")
print(f"median={statistics.median(gaps):.2f}s  p25={pct(gaps,0.25):.2f}s  p75={pct(gaps,0.75):.2f}s")
print(f"p10={pct(gaps,0.10):.2f}s  p90={pct(gaps,0.90):.2f}s")
zero=sum(1 for g in gaps if g==0)
print(f"simultaneous fills (gap=0, both legs same block): {zero} ({zero/len(gaps)*100:.1f}%)")

# ============ 3) INVENTORY-SKEW CORRELATION ============
# For each market, walk fills in time order, track running inventory imbalance
# (yes_shares - no_shares) BEFORE the next BUY fill. Skew of that next BUY fill:
# we measure "price skew" = how aggressively he leans into a side.
# Define per-fill skew signal s = signed deviation of the fill's pair-implied richness.
# Operationalize: x = inventory imbalance (oi0_shares - oi1_shares) prior to fill;
#   y = signed price he pays relative to his own per-side mean, oriented so that
#       buying MORE of the under-held side counts as rebalancing.
# Simplest robust test (the literal ask): does the SIDE/price of the next fill skew to
# reduce |imbalance|? We test: x = pre-fill imbalance; y = price * (+1 if oi0 else -1)
#   i.e. signed amount he's willing to pay on the long(yes) axis.
# If he rebalances, when imbalance is +large (too much yes) he should buy NO -> y negative
# => negative correlation between x and y.
xs=[]; ys=[]
xs2=[]; ys2=[]   # alt: x=imbalance, y = +1 buy-yes / -1 buy-no (directional choice)
for cid,c in sel.items():
    fills=sorted(c["fills"], key=lambda r:(r[0],))
    inv0=0.0; inv1=0.0
    for (ts,oi,side,pr,sz) in fills:
        if side=="BUY":
            imb = inv0-inv1
            sign = 1.0 if oi==0 else -1.0
            xs.append(imb); ys.append(sign*pr)
            xs2.append(imb); ys2.append(sign)
        # update inventory after the fill
        if side=="BUY":
            if oi==0: inv0+=sz
            else: inv1+=sz
        elif side=="SELL":
            if oi==0: inv0-=sz
            else: inv1-=sz

def pearson(x,y):
    n=len(x); mx=sum(x)/n; my=sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    vx=sum((a-mx)**2 for a in x); vy=sum((b-my)**2 for b in y)
    if vx==0 or vy==0: return float('nan')
    return cov/math.sqrt(vx*vy)

r_price=pearson(xs,ys)
r_dir=pearson(xs2,ys2)
print("\n=== 3) INVENTORY-SKEW CORRELATION ===")
print(f"n fills (BUY) = {len(xs)}")
print(f"corr(pre-fill imbalance(yes-no shares), signed price paid on yes-axis) = {r_price:.4f}")
print(f"corr(pre-fill imbalance, directional choice +1buyYes/-1buyNo) = {r_dir:.4f}")

# ============ 4) EDGE vs VOLATILITY ============
# volatility proxy per market: price range (max-min over all his fill prices, pooled both sides
# mapped to a common axis) and number of distinct price levels traded.
rows=[]
for cid,c in sel.items():
    prices=[pr for (ts,oi,side,pr,sz) in c["fills"] if pr>0]
    if not prices: continue
    prange=max(prices)-min(prices)
    nlevels=len(set(round(pr,3) for pr in prices))
    rows.append((c["_edge"], prange, nlevels, c["_pq"]))

edges_r=[r[0] for r in rows]
pranges=[r[1] for r in rows]
nlevels_l=[r[2] for r in rows]
r_edge_range=pearson(edges_r,pranges)
r_edge_levels=pearson(edges_r,nlevels_l)
print("\n=== 4) EDGE vs VOLATILITY ===")
print(f"n markets = {len(rows)}")
print(f"corr(edge, price_range)      = {r_edge_range:.4f}")
print(f"corr(edge, n_distinct_levels)= {r_edge_levels:.4f}")
# Bucketed view: low/med/high volatility by nlevels
srt=sorted(rows,key=lambda r:r[2])
nq=len(srt)//3
buckets={"low":srt[:nq],"mid":srt[nq:2*nq],"high":srt[2*nq:]}
for name,b in buckets.items():
    me=statistics.median([r[0] for r in b])
    mr=statistics.median([r[1] for r in b])
    ml=statistics.median([r[2] for r in b])
    print(f"  vol={name:4s} (by #levels) n={len(b):3d}  median_edge={me:.4f}  median_range={mr:.3f}  median_levels={ml:.0f}")
# also by price range terciles
srt2=sorted(rows,key=lambda r:r[1])
b2={"low":srt2[:nq],"mid":srt2[nq:2*nq],"high":srt2[2*nq:]}
print("  -- by price range terciles --")
for name,b in b2.items():
    me=statistics.median([r[0] for r in b])
    mr=statistics.median([r[1] for r in b])
    print(f"  vol={name:4s} (by range)  n={len(b):3d}  median_edge={me:.4f}  median_range={mr:.3f}")

# dump machine-readable
out={
 "n_markets":len(edges),
 "edge_median":edge_med,"edge_p25":edge_p25,"edge_p75":edge_p75,
 "pq_median":pq_med,"pq_p90":pq_p90,"pq_p95":pq_p95,
 "interfill_median":statistics.median(gaps),"interfill_p25":pct(gaps,0.25),"interfill_p75":pct(gaps,0.75),
 "skew_corr_price":r_price,"skew_corr_dir":r_dir,"skew_n":len(xs),
 "edge_vol_corr_range":r_edge_range,"edge_vol_corr_levels":r_edge_levels,
}
with open(r"C:\Users\zexi\pmscan\audit\twosided_results.json","w") as f:
    json.dump(out,f,indent=2)
print("\nsaved twosided_results.json")
