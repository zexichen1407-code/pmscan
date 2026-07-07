import pickle, statistics, math, json
import ijson
from collections import defaultdict, deque

# We need MERGE sizes per conditionId too (to cap matched sets at actual merge volume).
# The stream pkl didn't store merge size, only count. Re-stream merge sizes cheaply.
PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
with open(r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl","rb") as f:
    sel = pickle.load(f)
selset=set(sel.keys())

merge_sz=defaultdict(float)
for row in ijson.items(open(PATH,"r",encoding="utf-8"),"item"):
    if row.get("type")=="MERGE":
        cid=row.get("conditionId","") or ""
        if cid in selset:
            try: merge_sz[cid]+=float(row.get("size",0) or 0)
            except: pass
print("merge sizes loaded for", len(merge_sz), "conds")

def pct(data,p):
    d=sorted(data)
    if not d: return float('nan')
    k=(len(d)-1)*p; lo=int(math.floor(k)); hi=int(math.ceil(k))
    if lo==hi: return d[lo]
    return d[lo]+(d[hi]-d[lo])*(k-lo)

def pearson(x,y):
    n=len(x)
    if n<3: return float('nan')
    mx=sum(x)/n; my=sum(y)/n
    cov=sum((a-mx)*(b-my) for a,b in zip(x,y))
    vx=sum((a-mx)**2 for a in x); vy=sum((b-my)**2 for b in y)
    if vx==0 or vy==0: return float('nan')
    return cov/math.sqrt(vx*vy)

# ===== TARGET EDGE via MERGE-CAPPED FIFO (matches audit methodology) =====
# matched sets = min(b0s, b1s, merge_size). vwap of each leg taken FIFO over the
# CHEAPEST fills up to matched size? No -- the audit uses leg vwap on the buys that
# formed the pairs. Cleanest unbiased proxy used in audit ex.1/ex.2: per-leg vwap of
# the FULL leg, but merge is capped at min leg. That biases when legskew large.
# Better: matched = min(b0s,b1s,merge_sz); take FIFO-by-TIME both legs up to matched,
# pair them, cost=sum(pr0+pr1 weighted). This is the genuine paired spread.
mk_edge=[]; mk_pq=[]; mk_w=[]
for cid,c in sel.items():
    msz = merge_sz.get(cid,0)
    if msz<=0: continue
    b0=sorted([(t,pr,sz) for (t,oi,side,pr,sz) in c["fills"] if side=="BUY" and oi==0])
    b1=sorted([(t,pr,sz) for (t,oi,side,pr,sz) in c["fills"] if side=="BUY" and oi==1])
    tot0=sum(x[2] for x in b0); tot1=sum(x[2] for x in b1)
    matched=min(tot0,tot1,msz)
    if matched<=0: continue
    # take cheapest `matched` shares from each leg (the shares he'd rationally pair to merge)
    b0c=sorted(b0,key=lambda r:r[1]); b1c=sorted(b1,key=lambda r:r[1])
    def vwap_cheapest(legs,q):
        rem=q; notion=0.0; got=0.0
        for (t,pr,sz) in legs:
            take=min(sz,rem)
            notion+=pr*take; got+=take; rem-=take
            if rem<=1e-9: break
        return notion/got if got>0 else float('nan')
    p=vwap_cheapest(b0c,matched); q=vwap_cheapest(b1c,matched)
    pq=p+q
    mk_pq.append(pq); mk_edge.append(1-pq); mk_w.append(matched)

print("\n=== TARGET EDGE (merge-capped, cheapest-paired vwap) ===")
print(f"n markets = {len(mk_edge)}")
print(f"p+q:  median={statistics.median(mk_pq):.4f}  p25={pct(mk_pq,0.25):.4f}  p75={pct(mk_pq,0.75):.4f}  p90={pct(mk_pq,0.90):.4f}  p95={pct(mk_pq,0.95):.4f}")
print(f"edge: median={statistics.median(mk_edge):.4f}  p25={pct(mk_edge,0.25):.4f}  p75={pct(mk_edge,0.75):.4f}")
neg=sum(1 for e in mk_edge if e<0); print(f"negative-edge: {neg} ({neg/len(mk_edge)*100:.1f}%)")
# size weighted
vw=sum(e*w for e,w in zip(mk_edge,mk_w))/sum(mk_w)
print(f"size(matched)-weighted mean edge = {vw:.4f}")

# ===== INTER-FILL CADENCE (all fills, per market, no cross-market gaps) =====
gaps=[]
for cid,c in sel.items():
    ts=sorted(t for (t,oi,side,pr,sz) in c["fills"])
    gaps+=[b-a for a,b in zip(ts,ts[1:]) if b-a>=0]
print("\n=== INTER-FILL CADENCE (sec) ===")
print(f"n={len(gaps)} median={statistics.median(gaps):.1f} p25={pct(gaps,0.25):.1f} p75={pct(gaps,0.75):.1f}")

# ===== INVENTORY-SKEW (rebalancing) =====
# Proper test: when he is LONG more yes than no (imbalance>0), does his NEXT buy
# lean toward NO (rebalancing) ? signal = directional choice y=+1 buyYes/-1 buyNo.
# Rebalancing => negative corr. We also exclude the very first fill of each market.
xs=[]; ys=[]
for cid,c in sel.items():
    fills=sorted(c["fills"])
    inv0=inv1=0.0; first=True
    for (ts,oi,side,pr,sz) in fills:
        if side=="BUY":
            if not first:
                xs.append(inv0-inv1); ys.append(1.0 if oi==0 else -1.0)
            first=False
        if side=="BUY":
            if oi==0: inv0+=sz
            else: inv1+=sz
        else:
            if oi==0: inv0-=sz
            else: inv1-=sz
r_dir=pearson(xs,ys)
print("\n=== INVENTORY-SKEW ===")
print(f"n={len(xs)} corr(imbalance, next-buy-direction +yes/-no) = {r_dir:.4f}")

# normalize imbalance by market scale to avoid big markets dominating
xs2=[]; ys2=[]
for cid,c in sel.items():
    fills=sorted(c["fills"])
    scale=max(c["b0s"],c["b1s"],1)
    inv0=inv1=0.0; first=True
    for (ts,oi,side,pr,sz) in fills:
        if side=="BUY":
            if not first:
                xs2.append((inv0-inv1)/scale); ys2.append(1.0 if oi==0 else -1.0)
            first=False
            if oi==0: inv0+=sz
            else: inv1+=sz
        else:
            if oi==0: inv0-=sz
            else: inv1-=sz
r_dir_norm=pearson(xs2,ys2)
print(f"n={len(xs2)} corr(NORMALIZED imbalance, next-buy-direction) = {r_dir_norm:.4f}")

# ===== EDGE vs VOLATILITY =====
rows=[]
for cid,c,e,pq in [(cid,sel[cid],mk_edge[i],mk_pq[i]) for i,cid in enumerate([k for k in sel if merge_sz.get(k,0)>0]) ] if False else []:
    pass
# rebuild aligned (mk lists were built in dict order over msz>0)
keys=[k for k in sel if merge_sz.get(k,0)>0]
vol_rows=[]
for k,e,pq in zip(keys,mk_edge,mk_pq):
    c=sel[k]
    prices=[pr for (ts,oi,side,pr,sz) in c["fills"] if pr>0]
    if not prices: continue
    prange=max(prices)-min(prices)
    nlev=len(set(round(pr,3) for pr in prices))
    vol_rows.append((e,prange,nlev))
er=[r[0] for r in vol_rows]; pr_=[r[1] for r in vol_rows]; nl=[r[2] for r in vol_rows]
print("\n=== EDGE vs VOLATILITY ===")
print(f"corr(edge, price_range)={pearson(er,pr_):.4f}  corr(edge, #levels)={pearson(er,nl):.4f}")
srt=sorted(vol_rows,key=lambda r:r[2]); nq=len(srt)//3
for nm,b in [("low",srt[:nq]),("mid",srt[nq:2*nq]),("high",srt[2*nq:])]:
    print(f"  vol={nm} n={len(b)} median_edge={statistics.median([r[0] for r in b]):.4f} median_levels={statistics.median([r[2] for r in b]):.0f}")

out={
 "n_markets":len(mk_edge),
 "edge_median":statistics.median(mk_edge),"edge_p25":pct(mk_edge,0.25),"edge_p75":pct(mk_edge,0.75),
 "pq_median":statistics.median(mk_pq),"pq_p90":pct(mk_pq,0.90),"pq_p95":pct(mk_pq,0.95),
 "edge_neg_pct":neg/len(mk_edge)*100,"edge_sizew_mean":vw,
 "interfill_median":statistics.median(gaps),"interfill_p25":pct(gaps,0.25),"interfill_p75":pct(gaps,0.75),
 "skew_corr_dir":r_dir,"skew_corr_dir_norm":r_dir_norm,"skew_n":len(xs),
 "edge_vol_corr_range":pearson(er,pr_),"edge_vol_corr_levels":pearson(er,nl),
}
with open(r"C:\Users\zexi\pmscan\audit\twosided_final_results.json","w") as f:
    json.dump(out,f,indent=2)
print("\nsaved twosided_final_results.json")
print(json.dumps(out,indent=2))
