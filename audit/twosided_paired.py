import pickle, statistics, math, json
from collections import deque

with open(r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl","rb") as f:
    sel = pickle.load(f)

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

# ===== PAIR-MATCHED EDGE (the spread he actually targets) =====
# Within each market, FIFO-match BUY shares of outcome0 against BUY shares of outcome1.
# Each matched complete-set has cost = price0 + price1; edge = 1 - that.
# This isolates the merge-arb spread and ignores unpaired directional overhang.
# We report the per-market SIZE-WEIGHTED-on-matched-shares pair cost.
market_pq=[]; market_edge=[]
all_pair_pq=[]   # every matched set's p+q (pooled)
matched_total=0.0
for cid,c in sel.items():
    buys0=deque(sorted([(t,pr,sz) for (t,oi,side,pr,sz) in c["fills"] if side=="BUY" and oi==0]))
    buys1=deque(sorted([(t,pr,sz) for (t,oi,side,pr,sz) in c["fills"] if side=="BUY" and oi==1]))
    # FIFO match
    pq_notional=0.0; matched_sz=0.0
    b0=list(buys0); b1=list(buys1)
    i=j=0
    rem0 = b0[0][2] if b0 else 0
    rem1 = b1[0][2] if b1 else 0
    while i<len(b0) and j<len(b1):
        take=min(rem0,rem1)
        if take<=0: break
        pr0=b0[i][1]; pr1=b1[j][1]
        pq_notional += (pr0+pr1)*take
        matched_sz += take
        all_pair_pq.append(pr0+pr1)  # one row per matched chunk (approx weight ignored for pooled dist)
        rem0-=take; rem1-=take
        if rem0<=1e-9:
            i+=1; rem0 = b0[i][2] if i<len(b0) else 0
        if rem1<=1e-9:
            j+=1; rem1 = b1[j][2] if j<len(b1) else 0
    if matched_sz>0:
        avg_pq = pq_notional/matched_sz
        market_pq.append(avg_pq); market_edge.append(1-avg_pq)
        matched_total+=matched_sz

print("=== PAIR-MATCHED TARGET EDGE (FIFO complete-set cost) ===")
print(f"n markets with matched sets = {len(market_edge)}")
print(f"p+q (matched, per-market):  median={statistics.median(market_pq):.4f}  p25={pct(market_pq,0.25):.4f}  p75={pct(market_pq,0.75):.4f}")
print(f"   p90={pct(market_pq,0.90):.4f}  p95={pct(market_pq,0.95):.4f}")
print(f"edge=1-p-q (per-market):    median={statistics.median(market_edge):.4f}  p25={pct(market_edge,0.25):.4f}  p75={pct(market_edge,0.75):.4f}")
neg=sum(1 for e in market_edge if e<0)
print(f"negative-edge markets: {neg} ({neg/len(market_edge)*100:.1f}%)")

# size-weighted by matched shares
print("\n=== pooled across ALL matched complete-sets (chunk-level) ===")
print(f"n chunks={len(all_pair_pq)}")
print(f"pair p+q: median={statistics.median(all_pair_pq):.4f} p25={pct(all_pair_pq,0.25):.4f} p75={pct(all_pair_pq,0.75):.4f} p90={pct(all_pair_pq,0.90):.4f}")
pooled_edge=[1-x for x in all_pair_pq]
print(f"pair edge: median={statistics.median(pooled_edge):.4f} p25={pct(pooled_edge,0.25):.4f} p75={pct(pooled_edge,0.75):.4f}")

out={
 "pm_n":len(market_edge),
 "pm_pq_median":statistics.median(market_pq),"pm_pq_p25":pct(market_pq,0.25),"pm_pq_p75":pct(market_pq,0.75),
 "pm_pq_p90":pct(market_pq,0.90),"pm_pq_p95":pct(market_pq,0.95),
 "pm_edge_median":statistics.median(market_edge),"pm_edge_p25":pct(market_edge,0.25),"pm_edge_p75":pct(market_edge,0.75),
 "pm_neg_pct":neg/len(market_edge)*100,
}
with open(r"C:\Users\zexi\pmscan\audit\twosided_paired_results.json","w") as f:
    json.dump(out,f,indent=2)
print("\nsaved twosided_paired_results.json")
