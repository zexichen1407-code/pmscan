"""
Final bare-leg analysis. Three nested populations, FIFO pairing of the two BUY
legs within each conditionId, per-paired-share (size-weighted) bare-leg duration:

  A) ALL two-sided conds (754)
  B) BALANCED two-sided conds, min/max leg-share >= 0.25 (the genuine two-sided
     market-making subset; matches scout's legskew<0.25 cleanliness filter)
  C) BALANCED >= 0.5

Bare-leg duration of a paired unit = pairing_ts - FIFO-front buy_ts of LEADING leg.
Never-paired = unmatched leading-leg shares at end of data.
"""
import ijson, json
from collections import defaultdict

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
buys = defaultdict(list)
with open(RAW, "rb") as f:
    for r in ijson.items(f, "item"):
        if r.get("type") == "TRADE" and r.get("side") == "BUY":
            c = r.get("conditionId")
            try: oi = int(r.get("outcomeIndex"))
            except: continue
            if oi in (0, 1) and c:
                sz = float(r.get("size") or 0)
                if sz > 0:
                    buys[c].append((int(r["timestamp"]), oi, sz))

two_sided = {c: f for c, f in buys.items()
             if any(o == 0 for _, o, _ in f) and any(o == 1 for _, o, _ in f)}

def balance(f):
    s0 = sum(s for _, o, s in f if o == 0); s1 = sum(s for _, o, s in f if o == 1)
    return min(s0, s1) / max(s0, s1) if max(s0, s1) > 0 else 0

def run(conds):
    dur_w = []; bought = 0.0; paired = 0.0; never = 0.0
    for c in conds:
        fs = sorted(two_sided[c], key=lambda x: x[0])
        q = {0: [], 1: []}
        for ts, oi, sz in fs:
            bought += sz; other = 1 - oi; rem = sz
            while rem > 1e-9 and q[other]:
                lts, lsz = q[other][0]; take = min(rem, lsz)
                dur_w.append((ts - lts, take)); paired += take
                rem -= take; lsz -= take
                if lsz <= 1e-9: q[other].pop(0)
                else: q[other][0][1] = lsz
            if rem > 1e-9: q[oi].append([ts, rem])
        never += sum(s for leg in (0, 1) for _, s in q[leg])
    return dur_w, bought, paired, never

def wq(p, q):
    sp = sorted(p); tot = sum(w for _, w in sp); tgt = q*tot; cum = 0
    for v, w in sp:
        cum += w
        if cum >= tgt: return v
    return sp[-1][0] if sp else float('nan')
def wmean(p):
    tot = sum(w for _, w in p); return sum(v*w for v, w in p)/tot if tot else float('nan')
def wfrac(p, th):
    tot = sum(w for _, w in p); return sum(w for v, w in p if v < th)/tot if tot else float('nan')

pops = {
    "A_all_754": list(two_sided.keys()),
    "B_bal_ge0.25": [c for c in two_sided if balance(two_sided[c]) >= 0.25],
    "C_bal_ge0.5": [c for c in two_sided if balance(two_sided[c]) >= 0.5],
}
allres = {}
for name, conds in pops.items():
    dur_w, bought, paired, never = run(conds)
    ev = [(d, 1.0) for d, _ in dur_w]
    r = {
        "n_conds": len(conds), "pairing_events": len(dur_w),
        "bought_shares": bought, "paired_shares_per_leg": paired,
        "never_paired_shares": never, "never_frac": never/bought if bought else float('nan'),
        # size-weighted per paired share
        "sw_median": wq(dur_w,.5), "sw_p25": wq(dur_w,.25), "sw_p75": wq(dur_w,.75),
        "sw_p90": wq(dur_w,.90), "sw_mean": wmean(dur_w),
        # per pairing event (unweighted)
        "ev_median": wq(ev,.5), "ev_p25": wq(ev,.25), "ev_p75": wq(ev,.75),
        "ev_p90": wq(ev,.90), "ev_mean": wmean(ev),
        "sw_lt5": wfrac(dur_w,5), "sw_lt10": wfrac(dur_w,10), "sw_lt60": wfrac(dur_w,60),
        "sw_lt300": wfrac(dur_w,300),
        "ev_lt5": wfrac(ev,5), "ev_lt10": wfrac(ev,10), "ev_lt60": wfrac(ev,60),
        "ev_lt300": wfrac(ev,300),
    }
    allres[name] = r
    print(f"\n===== {name}  ({len(conds)} conds, {len(dur_w)} pairing events) =====")
    print(f"  bought {bought:,.0f}  paired/leg {paired:,.0f}  never {never:,.0f} ({r['never_frac']*100:.1f}%)")
    print(f"  SIZE-WEIGHTED (s): med {r['sw_median']:.0f}  p25 {r['sw_p25']:.0f}  p75 {r['sw_p75']:.0f}  p90 {r['sw_p90']:.0f}  mean {r['sw_mean']:.0f}")
    print(f"     -> median {r['sw_median']/60:.1f} min, p90 {r['sw_p90']/3600:.1f} h")
    print(f"  PER-EVENT     (s): med {r['ev_median']:.0f}  p25 {r['ev_p25']:.0f}  p75 {r['ev_p75']:.0f}  p90 {r['ev_p90']:.0f}  mean {r['ev_mean']:.0f}")
    print(f"  sim frac SW: <5s {r['sw_lt5']*100:.1f}%  <10s {r['sw_lt10']*100:.1f}%  <60s {r['sw_lt60']*100:.1f}%  <5m {r['sw_lt300']*100:.1f}%")
    print(f"  sim frac EV: <5s {r['ev_lt5']*100:.1f}%  <10s {r['ev_lt10']*100:.1f}%  <60s {r['ev_lt60']*100:.1f}%  <5m {r['ev_lt300']*100:.1f}%")

json.dump(allres, open(r"C:\Users\zexi\pmscan\audit\bareleg_final_results.json","w"), indent=2)
print("\nwrote bareleg_final_results.json")
