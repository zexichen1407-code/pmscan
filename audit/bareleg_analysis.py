"""
0xp3nny bare-leg exposure analysis.

For each market where he bought BOTH outcome legs (two-sided), reconstruct his
BUY fills chronologically. Maintain cumulative bought YES (oi=0) and NO (oi=1).
At any moment, paired units = min(cumYES, cumNO). When a new fill completes a
pairing, that unit's "bare-leg duration" = pairing_time - buy_time of the FIFO
front unit of the LEADING leg (the leg that was already ahead).

We weight every metric by SHARE SIZE (each share = one paired unit), since fills
have very different sizes. We also report unit-count (per-fill-event) views.

Never-paired = shares bought on the leg that stays ahead and are never matched by
the lagging leg through end of data (held bare to settlement).
"""
import ijson
import pickle
import math
from collections import defaultdict
from decimal import Decimal

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# Per conditionId: list of (ts, oi, size) for TRADE BUY; flags for exit events.
buys = defaultdict(list)          # cond -> [(ts, oi, size)]
has_merge = defaultdict(bool)
has_redeem = defaultdict(bool)
has_conv = defaultdict(bool)
title_of = {}

n = 0
with open(RAW, "rb") as f:
    for rec in ijson.items(f, "item"):
        n += 1
        t = rec.get("type")
        cond = rec.get("conditionId")
        if not cond:
            continue
        if t == "TRADE":
            if rec.get("side") == "BUY":
                oi = rec.get("outcomeIndex")
                try:
                    oi = int(oi)
                except Exception:
                    continue
                if oi not in (0, 1):
                    continue
                ts = int(rec["timestamp"])
                sz = float(rec.get("size") or 0)
                if sz <= 0:
                    continue
                buys[cond].append((ts, oi, sz))
                if cond not in title_of:
                    title_of[cond] = rec.get("title", "")
        elif t == "MERGE":
            has_merge[cond] = True
        elif t == "REDEEM":
            has_redeem[cond] = True
        elif t == "CONVERSION":
            has_conv[cond] = True

print(f"scanned {n} records; conds with buys: {len(buys)}")

# --- FIFO pairing per two-sided cond ---
# durations as (duration_seconds, size) weighted units. Also count of fill-events.
dur_weighted = []   # (dur_sec, size)  one entry per pairing-event-chunk
total_paired_shares = 0.0
total_bought_shares = 0.0
never_paired_shares = 0.0      # shares that stay unmatched on the leading leg at end
two_sided_conds = 0
one_sided_conds = 0
conds_with_any_pair = 0

# exit context for never-paired shares
never_paired_in_exited_cond = 0.0   # cond had merge/redeem/conv somewhere
never_paired_in_clean_cond = 0.0

for cond, fills in buys.items():
    ois = set(o for (_, o, _) in fills)
    if len(ois) < 2:
        one_sided_conds += 1
        # entirely one-sided market: every share is bare to settlement
        s = sum(sz for (_, _, sz) in fills)
        total_bought_shares += s
        never_paired_shares += s
        if has_merge[cond] or has_redeem[cond] or has_conv[cond]:
            never_paired_in_exited_cond += s
        else:
            never_paired_in_clean_cond += s
        continue
    two_sided_conds += 1
    fills_sorted = sorted(fills, key=lambda x: x[0])
    # FIFO queues of unmatched buys per leg: list of [ts, remaining_size]
    q = {0: [], 1: []}
    paired_here = False
    for (ts, oi, sz) in fills_sorted:
        total_bought_shares += sz
        other = 1 - oi
        remaining = sz
        # match against opposite leg's outstanding queue (those are the leading
        # leg's earlier unmatched shares; their bare time ends now)
        while remaining > 1e-9 and q[other]:
            lead_ts, lead_sz = q[other][0]
            take = min(remaining, lead_sz)
            dur = ts - lead_ts          # leading leg waited from lead_ts to now
            dur_weighted.append((dur, take))
            total_paired_shares += take   # 'take' shares of each leg get paired; count pairs
            remaining -= take
            lead_sz -= take
            paired_here = True
            if lead_sz <= 1e-9:
                q[other].pop(0)
            else:
                q[other][0][1] = lead_sz
        # leftover of this fill becomes outstanding on its own leg
        if remaining > 1e-9:
            q[oi].append([ts, remaining])
    if paired_here:
        conds_with_any_pair += 1
    # leftover in queues = never paired (bare to settlement)
    leftover = sum(s for leg in (0, 1) for (_, s) in q[leg])
    never_paired_shares += leftover
    if leftover > 0:
        if has_merge[cond] or has_redeem[cond] or has_conv[cond]:
            never_paired_in_exited_cond += leftover
        else:
            never_paired_in_clean_cond += leftover

# --- distribution helpers (size-weighted over paired units) ---
def weighted_pct(pairs, q):
    """pairs: list of (value, weight). q in [0,1]. returns weighted quantile."""
    if not pairs:
        return float("nan")
    sp = sorted(pairs, key=lambda x: x[0])
    tot = sum(w for _, w in sp)
    target = q * tot
    cum = 0.0
    for v, w in sp:
        cum += w
        if cum >= target:
            return v
    return sp[-1][0]

def weighted_mean(pairs):
    tot = sum(w for _, w in pairs)
    if tot == 0:
        return float("nan")
    return sum(v * w for v, w in pairs) / tot

def weighted_frac_below(pairs, thresh):
    tot = sum(w for _, w in pairs)
    if tot == 0:
        return float("nan")
    return sum(w for v, w in pairs if v < thresh) / tot

# size-weighted (per share) distribution
med = weighted_pct(dur_weighted, 0.5)
p25 = weighted_pct(dur_weighted, 0.25)
p75 = weighted_pct(dur_weighted, 0.75)
p90 = weighted_pct(dur_weighted, 0.90)
mean = weighted_mean(dur_weighted)

# unit (per-fill-event, unweighted) distribution
ev = [(d, 1.0) for (d, _) in dur_weighted]
med_e = weighted_pct(ev, 0.5)
p25_e = weighted_pct(ev, 0.25)
p75_e = weighted_pct(ev, 0.75)
p90_e = weighted_pct(ev, 0.90)
mean_e = weighted_mean(ev)

frac_lt10 = weighted_frac_below(dur_weighted, 10)
frac_lt60 = weighted_frac_below(dur_weighted, 60)
frac_lt5  = weighted_frac_below(dur_weighted, 5)
frac_lt1  = weighted_frac_below(dur_weighted, 1)   # same block / instant
frac_lt300 = weighted_frac_below(dur_weighted, 300)
frac_lt3600 = weighted_frac_below(dur_weighted, 3600)

ev_frac_lt10 = weighted_frac_below(ev, 10)
ev_frac_lt60 = weighted_frac_below(ev, 60)

never_frac = never_paired_shares / total_bought_shares if total_bought_shares else float("nan")

print("\n========== RESULTS ==========")
print(f"records scanned: {n}")
print(f"conds with >=1 BUY: {len(buys)}")
print(f"  two-sided (bought both legs): {two_sided_conds}")
print(f"  one-sided (only one leg ever): {one_sided_conds}")
print(f"  two-sided conds with >=1 pairing event: {conds_with_any_pair}")
print(f"pairing events (FIFO chunks): {len(dur_weighted)}")
print(f"total bought shares: {total_bought_shares:,.0f}")
print(f"total paired shares (per leg): {total_paired_shares:,.0f}")
print(f"never-paired shares (bare to settlement): {never_paired_shares:,.0f}")
print()
print("--- BARE-LEG DURATION, SIZE-WEIGHTED (seconds) ---")
print(f"  median: {med:.1f}   p25: {p25:.1f}   p75: {p75:.1f}   p90: {p90:.1f}   mean: {mean:.1f}")
print(f"  median(min): {med/60:.2f}  p90(min): {p90/60:.2f}  mean(min): {mean/60:.2f}")
print("--- BARE-LEG DURATION, PER-EVENT (unweighted, seconds) ---")
print(f"  median: {med_e:.1f}   p25: {p25_e:.1f}   p75: {p75_e:.1f}   p90: {p90_e:.1f}   mean: {mean_e:.1f}")
print()
print("--- APPROX-SIMULTANEOUS FILL FRACTIONS (size-weighted) ---")
print(f"  <1s:  {frac_lt1*100:.1f}%")
print(f"  <5s:  {frac_lt5*100:.1f}%")
print(f"  <10s: {frac_lt10*100:.1f}%")
print(f"  <60s: {frac_lt60*100:.1f}%")
print(f"  <5min:{frac_lt300*100:.1f}%")
print(f"  <1h:  {frac_lt3600*100:.1f}%")
print("--- APPROX-SIMULTANEOUS (per-event) ---")
print(f"  <10s: {ev_frac_lt10*100:.1f}%   <60s: {ev_frac_lt60*100:.1f}%")
print()
print("--- NEVER-PAIRED (held bare to settlement) ---")
print(f"  never-paired share fraction: {never_frac*100:.2f}% of all bought shares")
print(f"    of which in conds that DID merge/redeem/convert elsewhere: {never_paired_in_exited_cond:,.0f}")
print(f"    of which in clean conds (no exit event at all): {never_paired_in_clean_cond:,.0f}")

import json
out = {
    "records": n,
    "conds_with_buys": len(buys),
    "two_sided_conds": two_sided_conds,
    "one_sided_conds": one_sided_conds,
    "pairing_events": len(dur_weighted),
    "total_bought_shares": total_bought_shares,
    "total_paired_shares": total_paired_shares,
    "never_paired_shares": never_paired_shares,
    "never_paired_frac": never_frac,
    "sw_median_s": med, "sw_p25_s": p25, "sw_p75_s": p75, "sw_p90_s": p90, "sw_mean_s": mean,
    "ev_median_s": med_e, "ev_p25_s": p25_e, "ev_p75_s": p75_e, "ev_p90_s": p90_e, "ev_mean_s": mean_e,
    "frac_lt1": frac_lt1, "frac_lt5": frac_lt5, "frac_lt10": frac_lt10,
    "frac_lt60": frac_lt60, "frac_lt300": frac_lt300, "frac_lt3600": frac_lt3600,
    "ev_frac_lt10": ev_frac_lt10, "ev_frac_lt60": ev_frac_lt60,
    "never_paired_in_exited_cond": never_paired_in_exited_cond,
    "never_paired_in_clean_cond": never_paired_in_clean_cond,
}
json.dump(out, open(r"C:\Users\zexi\pmscan\audit\bareleg_results.json", "w"), indent=2)
print("\nwrote bareleg_results.json")
