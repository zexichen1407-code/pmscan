# -*- coding: utf-8 -*-
"""
Refine pair-ready -> merge lag with proper FIFO cumulative matching.
A "mergeable pair" exists at time t iff min(cum_buy0(<=t), cum_buy1(<=t)) >= cum_merged(<=t)+1share.
For each MERGE event, the inventory it consumes became 'ready' at the timestamp where the
later leg's cumulative buys first covered that pair. We compute, per merge event, the lag =
merge_ts - ready_ts(of the marginal pair consumed). Size-weighted percentiles too.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson, json
from collections import defaultdict
import bisect

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

def pct(s, p):
    if not s: return None
    if len(s)==1: return s[0]
    k=(len(s)-1)*p; f=int(k); c=min(f+1,len(s)-1)
    if f==c: return s[f]
    return s[f]*(c-k)+s[c]*(k-f)

def stats(vals,label):
    if not vals:
        print(label,"NO DATA"); return {}
    s=sorted(vals)
    d=dict(n=len(s),median=pct(s,.5),p25=pct(s,.25),p75=pct(s,.75),p90=pct(s,.90),mean=sum(s)/len(s))
    print(f"{label}: n={d['n']} median={d['median']:.2f} p25={d['p25']:.2f} p75={d['p75']:.2f} p90={d['p90']:.2f} mean={d['mean']:.2f}")
    return d

buy_leg = defaultdict(lambda: {0: [], 1: []})
merge_by_cond = defaultdict(list)

with open(PATH,"r",encoding="utf-8") as f:
    for row in ijson.items(f,"item"):
        t=row.get("type","")
        if t not in ("TRADE","MERGE"): continue
        cid=row.get("conditionId","") or ""
        ts=row.get("timestamp",0) or 0
        try: ts=int(ts)
        except: ts=0
        sz=row.get("size",0) or 0
        try: sz=float(sz)
        except: sz=0.0
        if t=="MERGE":
            merge_by_cond[cid].append((ts,sz))
        else:
            if (row.get("side","") or "")!="BUY": continue
            oi=row.get("outcomeIndex",999)
            try: oi=int(oi)
            except: oi=999
            if oi in (0,1):
                buy_leg[cid][oi].append((ts,sz))

# Per-merge-event lag via FIFO ready-time
per_merge_lag = []          # one value per merge event (the marginal pair's wait)
per_merge_lag_w = []        # (lag, size) for size-weighting
cond_first_pair_lag = []    # per-cond: first merge vs first-ready (clean)
EPS=1e-9

n_conds_used=0
for cid, mlst in merge_by_cond.items():
    legs=buy_leg.get(cid)
    if not legs: continue
    b0=sorted(legs[0]); b1=sorted(legs[1])
    if not b0 or not b1: continue
    n_conds_used+=1
    # Build a "pair-ready schedule": as cumulative matched pairs grow over time, record
    # the timestamp at which each incremental matched-share threshold is first reached.
    # We merge the two leg buy streams; matched(t)=min(cum0(t),cum1(t)).
    # ready_curve: list of (cum_matched_after_event, ts) monotonic in cum_matched.
    events=sorted([(ts,0,sz) for ts,sz in b0]+[(ts,1,sz) for ts,sz in b1])
    cum=[0.0,0.0]; matched=0.0
    ready_pts=[]  # (cum_matched_level, ts) where level is reached
    for ts,leg,sz in events:
        cum[leg]+=sz
        nm=min(cum[0],cum[1])
        if nm>matched+EPS:
            ready_pts.append((nm,ts))
            matched=nm
    if not ready_pts:
        continue
    levels=[p[0] for p in ready_pts]
    times=[p[1] for p in ready_pts]
    # walk merges in time order, consuming matched inventory FIFO
    consumed=0.0
    mtss=sorted(mlst)
    first_lag=None
    for mts,msz in mtss:
        # the marginal pair consumed sits at cumulative-matched level = consumed + msz (its top)
        # ready time = time at which level (consumed+small) was reached
        lvl=consumed+min(msz, max(msz,0))  # top of this merge's consumed band
        # find smallest ready level >= (consumed+EPS): the ready time for the *start* of this band
        i=bisect.bisect_left(levels, consumed+EPS)
        if i>=len(levels):
            # merge exceeds known matched inventory (rebuys/rounding) -> use last ready time
            rt=times[-1]
        else:
            rt=times[i]
        lag=mts-rt
        per_merge_lag.append(lag)
        per_merge_lag_w.append((lag,msz))
        if first_lag is None:
            first_lag=lag
        consumed+=msz
    if first_lag is not None:
        cond_first_pair_lag.append(first_lag)

print("conds used (both legs + merge):", n_conds_used)
print()
print("=== per-MERGE-event FIFO lag: merge_ts - marginal_pair_ready_ts (秒) ===")
d1=stats(per_merge_lag, "per_merge_lag_sec")
# size-weighted median
sw=sorted(per_merge_lag_w, key=lambda x:x[0])
tot=sum(w for _,w in sw); acc=0; swmed=None
for lag,w in sw:
    acc+=w
    if acc>=tot/2:
        swmed=lag; break
print(f"  size-weighted median lag = {swmed}")
neg=sum(1 for x in per_merge_lag if x<0)
print(f"  negative per-merge lags: {neg}/{len(per_merge_lag)} = {100*neg/len(per_merge_lag):.1f}%")
pos=[x for x in per_merge_lag if x>=0]
stats(pos,"per_merge_lag_sec (>=0 only)")
print()
print("=== per-COND first-merge FIFO lag (秒) ===")
stats(cond_first_pair_lag,"cond_first_merge_lag_sec")
posc=[x for x in cond_first_pair_lag if x>=0]
stats(posc,"cond_first_merge_lag_sec (>=0 only)")

json.dump({
  "per_merge_lag_sec": d1,
  "per_merge_lag_sw_median": swmed,
  "per_merge_neg_pct": 100*neg/len(per_merge_lag),
  "n_conds": n_conds_used,
}, open(r"C:\Users\zexi\pmscan\audit\merge_lag_refine_out.json","w"), indent=2)
print("\nWROTE merge_lag_refine_out.json")
