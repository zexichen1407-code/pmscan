import pickle, statistics, math
from collections import Counter

with open(r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl","rb") as f:
    sel = pickle.load(f)

def pct(data,p):
    d=sorted(data)
    if not d: return float('nan')
    k=(len(d)-1)*p; lo=int(math.floor(k)); hi=int(math.ceil(k))
    if lo==hi: return d[lo]
    return d[lo]+(d[hi]-d[lo])*(k-lo)

# Diagnostic: what fraction of buy size on each side is matched (merge needs equal sets)?
# Also recompute edge but only over the MATCHED quantity using a paired/episodic view.
# Episode-free check: how big is leg imbalance? merge size vs paired buy size.

# Recompute p,q and inspect worst negative-edge markets
rows=[]
for cid,c in sel.items():
    p=c["b0n"]/c["b0s"]; q=c["b1n"]/c["b1s"]
    pq=p+q
    # matched shares = min of the two leg buy sizes (the part that can form complete sets)
    matched=min(c["b0s"],c["b1s"])
    legskew=abs(c["b0s"]-c["b1s"])/max(c["b0s"],c["b1s"])
    nfills=len(c["fills"])
    rows.append((1-pq,pq,p,q,c["b0s"],c["b1s"],matched,legskew,nfills,c["title"]))

rows.sort()
print("=== 10 most NEGATIVE edge markets (paid most over 1.0) ===")
for r in rows[:10]:
    print(f"edge={r[0]:+.3f} pq={r[1]:.3f} p={r[2]:.3f} q={r[3]:.3f} b0s={r[4]:.0f} b1s={r[5]:.0f} legskew={r[7]:.2f} nf={r[8]} | {r[9][:45].encode('ascii','replace').decode()}")

print("\n=== leg-skew distribution (how balanced are the two legs in size) ===")
ls=[r[7] for r in rows]
print(f"median legskew={statistics.median(ls):.3f} p25={pct(ls,0.25):.3f} p75={pct(ls,0.75):.3f} p90={pct(ls,0.90):.3f}")

# Volume-weighted edge: weight each market's edge by matched size (capital deployed)
tot_matched=sum(r[6] for r in rows)
vw_edge=sum(r[0]*r[6] for r in rows)/tot_matched
print(f"\nsize(matched)-weighted mean edge across markets = {vw_edge:.4f}")
print(f"simple mean edge = {statistics.mean([r[0] for r in rows]):.4f}")
print(f"median edge = {statistics.median([r[0] for r in rows]):.4f}")

# Restrict to BALANCED two-sided markets (legskew < 0.25) -> the clean merge-arb signature
clean=[r for r in rows if r[7]<0.25]
print(f"\n=== BALANCED legs (legskew<0.25): n={len(clean)} ===")
ce=[r[0] for r in clean]
print(f"edge median={statistics.median(ce):.4f} p25={pct(ce,0.25):.4f} p75={pct(ce,0.75):.4f}")
cpq=[r[1] for r in clean]
print(f"pq median={statistics.median(cpq):.4f} p90={pct(cpq,0.90):.4f} p95={pct(cpq,0.95):.4f}")
negc=sum(1 for e in ce if e<0)
print(f"negative-edge among balanced: {negc} ({negc/len(clean)*100:.1f}%)")

# How many fills per market (is this real MM with many fills, or just 2 buys?)
nf=[r[8] for r in rows]
print(f"\nfills per market: median={statistics.median(nf):.0f} p25={pct(nf,0.25):.0f} p75={pct(nf,0.75):.0f} max={max(nf)}")
twofill=sum(1 for x in nf if x<=2)
print(f"markets with <=2 fills (not really MM): {twofill} ({twofill/len(nf)*100:.1f}%)")
