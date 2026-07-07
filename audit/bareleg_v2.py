"""
Bare-leg exposure, v2 -- restricted to TWO-SIDED markets (he bought BOTH legs
on the open market), which is the population the FIFO口径 actually applies to.

For each such conditionId, FIFO-pair the two BUY legs chronologically. Each
paired unit's bare-leg duration = pairing_time - FIFO-front buy time of the
LEADING leg. Weight by share size (a paired unit = one share of each leg).

Never-paired = shares left unmatched on the leading leg at end of data, WITHIN
these two-sided markets.

Also gathers SPLIT info to characterize how he really obtains the complementary
leg (the reason most conds look one-sided), for the caveat.
"""
import ijson, json
from collections import defaultdict

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

buys = defaultdict(list)        # cond -> [(ts,oi,size)]
split_sz = defaultdict(float)   # cond -> total SPLIT size
merge_sz = defaultdict(float)
n = 0
with open(RAW, "rb") as f:
    for r in ijson.items(f, "item"):
        n += 1
        t = r.get("type"); c = r.get("conditionId")
        if not c: continue
        if t == "TRADE" and r.get("side") == "BUY":
            try: oi = int(r.get("outcomeIndex"))
            except: continue
            if oi in (0, 1):
                sz = float(r.get("size") or 0)
                if sz > 0:
                    buys[c].append((int(r["timestamp"]), oi, sz))
        elif t == "SPLIT":
            split_sz[c] += float(r.get("size") or 0)
        elif t == "MERGE":
            merge_sz[c] += float(r.get("size") or 0)

two_sided = {c: f for c, f in buys.items()
             if any(o == 0 for _, o, _ in f) and any(o == 1 for _, o, _ in f)}
print(f"records {n}; two-sided conds: {len(two_sided)}")

dur_w = []                      # (dur_sec, size)
paired_shares = 0.0
bought_shares = 0.0
never_shares = 0.0
for c, fills in two_sided.items():
    fs = sorted(fills, key=lambda x: x[0])
    q = {0: [], 1: []}
    for ts, oi, sz in fs:
        bought_shares += sz
        other = 1 - oi
        rem = sz
        while rem > 1e-9 and q[other]:
            lts, lsz = q[other][0]
            take = min(rem, lsz)
            dur_w.append((ts - lts, take))
            paired_shares += take
            rem -= take; lsz -= take
            if lsz <= 1e-9: q[other].pop(0)
            else: q[other][0][1] = lsz
        if rem > 1e-9:
            q[oi].append([ts, rem])
    never_shares += sum(s for leg in (0, 1) for _, s in q[leg])

def wq(pairs, q):
    sp = sorted(pairs); tot = sum(w for _, w in sp); tgt = q*tot; cum = 0
    for v, w in sp:
        cum += w
        if cum >= tgt: return v
    return sp[-1][0]
def wmean(p):
    tot = sum(w for _, w in p); return sum(v*w for v, w in p)/tot if tot else float('nan')
def wfrac(p, th):
    tot = sum(w for _, w in p); return sum(w for v, w in p if v < th)/tot if tot else float('nan')

ev = [(d, 1.0) for d, _ in dur_w]

res = {
 "two_sided_conds": len(two_sided),
 "pairing_events": len(dur_w),
 "bought_shares_in_twosided": bought_shares,
 "paired_shares_per_leg": paired_shares,
 "never_paired_shares_in_twosided": never_shares,
 "never_frac_in_twosided": never_shares/bought_shares,
 # size-weighted (per paired share)
 "sw_median": wq(dur_w,.5),"sw_p25": wq(dur_w,.25),"sw_p75": wq(dur_w,.75),
 "sw_p90": wq(dur_w,.90),"sw_mean": wmean(dur_w),
 # per-event (per fill chunk)
 "ev_median": wq(ev,.5),"ev_p25": wq(ev,.25),"ev_p75": wq(ev,.75),
 "ev_p90": wq(ev,.90),"ev_mean": wmean(ev),
 "sw_lt1": wfrac(dur_w,1),"sw_lt5": wfrac(dur_w,5),"sw_lt10": wfrac(dur_w,10),
 "sw_lt60": wfrac(dur_w,60),"sw_lt300": wfrac(dur_w,300),"sw_lt3600": wfrac(dur_w,3600),
 "ev_lt10": wfrac(ev,10),"ev_lt60": wfrac(ev,60),"ev_lt300": wfrac(ev,300),
}
for k, v in res.items():
    print(f"{k:32s}: {v:,.4f}" if isinstance(v, float) else f"{k:32s}: {v}")
json.dump(res, open(r"C:\Users\zexi\pmscan\audit\bareleg_v2_results.json","w"), indent=2)
print("wrote bareleg_v2_results.json")
