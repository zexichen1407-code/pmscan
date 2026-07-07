import ijson, json, statistics, random, math
import numpy as np

PATH = r'C:\Users\zexi\pmscan\audit\raw_activity_full.json'
CID  = '0x221b05db581e0beb3bd140683b89b06f6bb565f67674fb7243c6d3789ee18b96'
WALLET = '0x4f1d5ae26fc31472966e951af3183308736d8de2'

# ---------------------------------------------------------------------------
# Single pass over the 252MB file:
#  - collect tennis-market BUY trades (for claim a)
#  - collect ALL trades for this wallet's conditionId-level aggregates (claim b sample)
# Confirm rows belong to the target wallet (proxyWallet).
# ---------------------------------------------------------------------------
tennis = []          # (ts, outcomeIndex, price, size)
tennis_wallets = set()
tennis_sides = {}

# For inventory-skew (claim b) we need per-market chronological buys across many markets.
# We sample a manageable set: collect per-conditionId buy events (ts, outcomeIndex, price, size)
# but only keep markets where the wallet traded BOTH outcomes (two-sided) to mirror the claim.
from collections import defaultdict
mkt_buys = defaultdict(list)   # cid -> list of (ts, oi, price, size)
mkt_types = defaultdict(set)

f = open(PATH, 'rb')
for obj in ijson.items(f, 'item'):
    t = obj.get('type')
    cid = obj.get('conditionId')
    if obj.get('proxyWallet'):
        pass
    if cid == CID and t == 'TRADE':
        tennis_wallets.add(obj.get('proxyWallet'))
        if obj.get('side') == 'BUY':
            tennis.append((int(obj['timestamp']), int(obj['outcomeIndex']),
                           float(obj['price']), float(obj['size'])))
    if t == 'TRADE' and obj.get('side') == 'BUY' and cid:
        oi = obj.get('outcomeIndex')
        if oi is None: continue
        mkt_buys[cid].append((int(obj['timestamp']), int(oi),
                              float(obj['price']), float(obj['size'])))
    if t in ('MERGE','SPLIT'):
        mkt_types[cid].add(t)
f.close()

print("Tennis wallets present in TRADE rows:", tennis_wallets)
print("Total tennis BUY fills:", len(tennis))

tennis.sort(key=lambda r: r[0])
YES_OI, NO_OI = 1, 0   # YES=Zverev=oi1, NO=Cobolli=oi0
yes = [r for r in tennis if r[1]==YES_OI]
no  = [r for r in tennis if r[1]==NO_OI]
print(f"YES(Zverev,oi1) fills: {len(yes)}  NO(Cobolli,oi0) fills: {len(no)}")

# ===========================================================================
# CLAIM (a): PAIR-COST INVARIANT.  Is sum_std << side_std *beyond* what the
# anti-correlation of a binary market mechanically forces?
# ===========================================================================
# Reproduce the rolling last-fill estimator.
cur_y=cur_n=None
roll=[]
for ts,oi,p,s in tennis:
    if oi==YES_OI: cur_y=p
    else: cur_n=p
    if cur_y is not None and cur_n is not None:
        roll.append((ts,cur_y,cur_n,cur_y+cur_n))
ys=np.array([r[1] for r in roll]); ns=np.array([r[2] for r in roll]); su=ys+ns
print("\n=== CLAIM (a) reproduce rolling estimator ===")
print(f"n={len(roll)}  sum mean={su.mean():.5f} std={su.std():.5f}")
print(f"yes std={ys.std():.5f}  no std={ns.std():.5f}")
print(f"sum_std/avg_side_std = {su.std()/((ys.std()+ns.std())/2):.3f}")
print(f"corr(yes,no) = {np.corrcoef(ys,ns)[0,1]:.4f}")

# Decompose: var(sum) = var(y)+var(n)+2cov(y,n).
vy,vn=ys.var(),ns.var(); cov=np.cov(ys,ns,bias=True)[0,1]
print(f"var(y)={vy:.5f} var(n)={vn:.5f} 2cov={2*cov:.5f}  -> var(sum)={vy+vn+2*cov:.5f} (actual {su.var():.5f})")

# NULL MODEL 1: shuffle which NO price pairs with which YES price (break the
# *temporal* targeting but keep each leg's marginal distribution). If the sum
# is still tight, the tightness is just from marginals; if it loosens a lot,
# temporal co-movement (his quoting) is what tightens it.
rng=np.random.default_rng(0)
null_sum_std=[]
for _ in range(500):
    perm=rng.permutation(ns)
    null_sum_std.append((ys+perm).std())
null_sum_std=np.array(null_sum_std)
print(f"\nNULL(shuffle pairing): sum_std mean={null_sum_std.mean():.5f} "
      f"[{np.percentile(null_sum_std,2.5):.5f},{np.percentile(null_sum_std,97.5):.5f}]")
print(f"actual sum_std={su.std():.5f}  -> actual is "
      f"{'TIGHTER' if su.std()<null_sum_std.mean() else 'NOT tighter'} than shuffled null")

# NULL MODEL 2: the *fair* binary benchmark. In any binary market the two
# outcome prices SHOULD sum ~1 (no-arb). Compare his sum to 1.0 and ask whether
# 0.95 is a deliberate discount vs just "prices sum to ~1 minus noise".
print(f"\nDistance of his sum from 1.0: mean={1-su.mean():.5f} (the claimed ~5c edge)")
print(f"Fraction of paired obs with sum<1.0: {(su<1.0).mean():.3f}")
print(f"Fraction with sum<0.97: {(su<0.97).mean():.3f}")

# Robust recompute excluding settle collapse, using a CONTEMPORANEOUS pairing:
# only pair when both legs filled within 60s of each other (true simultaneity),
# instead of stale last-fill which can be minutes old.
def contemporaneous_pairs(maxlag):
    pairs=[]
    last_y=last_n=None  # (ts,price)
    for ts,oi,p,s in tennis:
        if oi==YES_OI: last_y=(ts,p)
        else: last_n=(ts,p)
        if last_y and last_n and abs(last_y[0]-last_n[0])<=maxlag:
            pairs.append(last_y[1]+last_n[1])
    return np.array(pairs)
for lag in (30,60,120):
    cp=contemporaneous_pairs(lag)
    if len(cp):
        print(f"contemporaneous (<= {lag}s) pairs n={len(cp)} sum mean={cp.mean():.5f} std={cp.std():.5f}")

# ===========================================================================
# CLAIM (b): INVENTORY-SKEW correlation across markets.
# Recompute Pearson/Spearman between normalized (Yes-No) inventory imbalance
# BEFORE a buy and the direction (+1 Yes / -1 No) of that buy.
# Restrict to two-sided + MERGE markets to mirror the claim's n=669.
# ===========================================================================
# two-sided = traded both oi0 and oi1; merge market = had MERGE
twosided=[]
for cid,evs in mkt_buys.items():
    ois={e[1] for e in evs}
    if 0 in ois and 1 in ois and 'MERGE' in mkt_types.get(cid,set()):
        twosided.append(cid)
print(f"\n=== CLAIM (b) ===")
print(f"two-sided + MERGE markets: {len(twosided)}")

imb_norm=[]; nxt_dir=[]; nxt_signed_price=[]
cond_long_yes_nextyes=0; cond_long_yes_n=0
cond_long_no_nextyes=0; cond_long_no_n=0
for cid in twosided:
    evs=sorted(mkt_buys[cid])
    total_size=sum(e[3] for e in evs) or 1.0
    cy=cn=0.0
    for ts,oi,p,s in evs:
        imb=(cy-cn)/total_size    # normalized before this buy
        d=1 if oi==1 else -1       # buy direction (oi1=Yes)
        # need a prior nonzero imbalance to be meaningful
        imb_norm.append(imb); nxt_dir.append(d)
        nxt_signed_price.append(d*p)
        if imb>0:
            cond_long_yes_n+=1
            if oi==1: cond_long_yes_nextyes+=1
        elif imb<0:
            cond_long_no_n+=1
            if oi==1: cond_long_no_nextyes+=1
        if oi==1: cy+=s
        else: cn+=s

imb_norm=np.array(imb_norm); nxt_dir=np.array(nxt_dir); nxt_sp=np.array(nxt_signed_price)
n=len(imb_norm)
def pearson(a,b):
    if a.std()==0 or b.std()==0: return float('nan')
    return np.corrcoef(a,b)[0,1]
def spearman(a,b):
    ra=np.argsort(np.argsort(a)); rb=np.argsort(np.argsort(b))
    return pearson(ra.astype(float),rb.astype(float))
print(f"n observations={n}")
print(f"Pearson(imbalance, next-dir)   = {pearson(imb_norm,nxt_dir):.4f}")
print(f"Spearman(imbalance, next-dir)  = {spearman(imb_norm,nxt_dir):.4f}")
print(f"Pearson(imbalance, signed-price)= {pearson(imb_norm,nxt_sp):.4f}")
print(f"P(next=Yes | currently long Yes) = {cond_long_yes_nextyes/cond_long_yes_n:.3f} (n={cond_long_yes_n})")
print(f"P(next=Yes | currently long No)  = {cond_long_no_nextyes/cond_long_no_n:.3f} (n={cond_long_no_n})")

# Bootstrap the correlation by RESAMPLING MARKETS (cluster bootstrap) so the
# CI reflects market-level independence, not 30k correlated within-market obs.
mkt_index=defaultdict(list)
# rebuild with market id tagging
idx=0; tags=[]
imb2=[]; dir2=[]
for cid in twosided:
    evs=sorted(mkt_buys[cid]); total=sum(e[3] for e in evs) or 1.0
    cy=cn=0.0
    for ts,oi,p,s in evs:
        imb2.append((cy-cn)/total); dir2.append(1 if oi==1 else -1); tags.append(cid)
        if oi==1: cy+=s
        else: cn+=s
imb2=np.array(imb2); dir2=np.array(dir2); tags=np.array(tags)
by_mkt={}
for cid in twosided:
    m=tags==cid
    by_mkt[cid]=(imb2[m],dir2[m])
cids=list(twosided)
boot=[]
for _ in range(300):
    samp=rng.choice(len(cids),len(cids),replace=True)
    A=[];B=[]
    for j in samp:
        a,b=by_mkt[cids[j]]; A.append(a); B.append(b)
    A=np.concatenate(A); B=np.concatenate(B)
    boot.append(pearson(A,B))
boot=np.array(boot)
print(f"cluster-bootstrap Pearson 95% CI: [{np.percentile(boot,2.5):.4f}, {np.percentile(boot,97.5):.4f}]")

# ===========================================================================
# Target-edge sanity check: per two-sided+MERGE market, size-weighted avg buy
# price of cheapest matched shares on each leg, capped at merge volume? We don't
# have per-market merge volume here cheaply, so do the simpler robust proxy used
# in the claim's "naive" warning AND the cheapest-matched estimator.
# ===========================================================================
edges_simple=[]   # 1-(swavg p_oi0 + swavg p_oi1) over all buys
edges_minleg=[]   # 1-(vwap of cheapest matched min(qty) shares each side)
for cid in twosided:
    evs=mkt_buys[cid]
    b0=[(p,s) for ts,oi,p,s in evs if oi==0]
    b1=[(p,s) for ts,oi,p,s in evs if oi==1]
    if not b0 or not b1: continue
    sw=lambda lst: sum(p*s for p,s in lst)/sum(s for s in (x[1] for x in lst))
    p0=sw(b0); p1=sw(b1)
    edges_simple.append(1-(p0+p1))
    # cheapest matched: sort each leg ascending price, match min total qty
    q=min(sum(s for _,s in b0), sum(s for _,s in b1))
    def vwap_cheapest(lst,qty):
        lst=sorted(lst); got=0.0; cost=0.0
        for p,s in lst:
            take=min(s,qty-got); cost+=take*p; got+=take
            if got>=qty: break
        return cost/got if got else float('nan')
    edges_minleg.append(1-(vwap_cheapest(b0,q)+vwap_cheapest(b1,q)))
edges_simple=np.array(edges_simple); edges_minleg=np.array(edges_minleg)
def desc(a):
    a=a[~np.isnan(a)]
    return (f"n={len(a)} median={np.median(a):.4f} mean={a.mean():.4f} "
            f"p25={np.percentile(a,25):.4f} p75={np.percentile(a,75):.4f} "
            f"p90={np.percentile(a,90):.4f} p95={np.percentile(a,95):.4f} "
            f"frac_neg={(a<0).mean():.3f}")
print(f"\n=== TARGET-EDGE sanity ===")
print("SIMPLE all-buys estimator (1-(p0+q0)):", desc(edges_simple))
print("CHEAPEST-MATCHED min-leg estimator:   ", desc(edges_minleg))
print("ceiling p+q (1-edge): p90 sum =", 1-np.percentile(edges_minleg[~np.isnan(edges_minleg)],10),
      " p95 sum =", 1-np.percentile(edges_minleg[~np.isnan(edges_minleg)],5))
