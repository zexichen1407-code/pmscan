# -*- coding: utf-8 -*-
"""
Adversarial attack on the M4 FIFO residual/holding model.

BASELINE (orchestrator M4): each CONVERSION of s sets consumes up to s shares from
EVERY leg that has shares (FIFO by buy ts, capped at availability). This implicitly
assumes every conversion burns ALL legs => k_i = (#legs with stock) = maximal.

PHYSICS: a neg-risk partial conversion of subset S (|S|=k) burns s NO on exactly the
k outcomes in S, returns (k-1) USDC + YES on complement. So a conversion may NOT touch
thin legs. The data does NOT record which subset S (the conditionId on a CONVERSION row
is a single constant per event = the neg-risk marker, not the burned legs). So k_i is
UNOBSERVED and must be assumed.

We recompute residual% and capital-%-within-2min under ALTERNATIVE assumptions where
each conversion of s sets consumes s shares ONLY from the k THICKEST legs at that moment.
We test several k policies. k="cover" = the minimal set of currently-thickest legs whose
running available stock can supply this conversion (most defensible: a rational converter
burns the legs that actually have the inventory). Also k=2, k=ceil(N/2), k=N(=baseline).

FIFO within a leg is preserved (oldest buys consumed first). "Thickest at that moment"
is by REMAINING available shares in the leg at the conversion's timestamp.
"""
import sys, io, json, math
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
TWO_MIN = 120

# ---------------- PASS 1: collect per-event legs + conversions ----------------
ev = defaultdict(lambda: {"legs": defaultdict(list), "conv": []})
maxts = 0; n = 0
with open(RAW, 'rb') as fh:
    for r in ijson.items(fh, 'item'):
        n += 1
        t = r.get('type'); es = r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        try: ts = int(r.get('timestamp'))
        except: continue
        if ts > maxts: maxts = ts
        if t == "TRADE" and r.get('side') == "BUY" and r.get('outcome') == "No":
            cid = r.get('conditionId') or ""
            try: sz = float(r.get('size') or 0)
            except: sz = 0.0
            try: pr = float(r.get('price') or 0)
            except: pr = 0.0
            ev[es]["legs"][cid].append((ts, sz, pr))
        elif t == "CONVERSION":
            try: sz = float(r.get('size') or 0)
            except: sz = 0.0
            ev[es]["conv"].append((ts, sz))
        if n % 50000 == 0: print("...scan", n, file=sys.stderr)
DATA_END = maxts
print("rows scanned:", n, "data_end_ts:", maxts, file=sys.stderr)

negrisk = [(es, e) for es, e in ev.items()
           if e["conv"] and sum(1 for c, b in e["legs"].items() if b) >= 3]
print(f"neg-risk events (>=1 conv & >=3 NO legs): {len(negrisk)}", file=sys.stderr)


def run_model(k_policy):
    """k_policy: 'all' (baseline), 'cover', 'half', 'two', or an int.
    Returns aggregate dict.
    For each event, maintain per-leg FIFO deque of (ts, shares, price).
    Process conversions in ts order. For a conversion of s sets at cts:
      - candidate legs = legs with available shares with oldest buy ts <= cts
        (a buy after cts cannot be consumed by this conversion).
      - determine k legs to burn from, THICKEST first by current available (ts<=cts) shares.
      - 'cover': take thickest legs cumulatively until their combined available >= s
                 (minimal cover); each chosen leg supplies min(s, its_avail) actually we
                 burn exactly s from EACH chosen leg? NO. Physics: a conversion of s sets on
                 subset S burns s from EACH leg in S simultaneously. So to convert s sets you
                 need EVERY leg in S to have >= s available. Thinner legs cap the set size.
    """
    # holding-time accumulators (converted) weighted by usd(=shares*price)
    conv_usd = 0.0; conv_shares = 0.0
    resid_usd = 0.0; resid_shares = 0.0
    # for within-2min capital fraction and weighted-median holding
    hold_pairs = []   # (hold_seconds, usd_weight) for converted
    usd_within_2min = 0.0
    resid_hold_pairs = []  # (hold, usd) for residual (censored at DATA_END)

    for es, e in negrisk:
        convs = sorted(e["conv"])
        # per-leg FIFO deque of [ts, shares, price]; keep buys sorted by ts
        legs = {}
        for cid, b in e["legs"].items():
            if b:
                legs[cid] = deque(sorted((ts, sz, pr) for (ts, sz, pr) in b))
        N = len(legs)

        for (cts, s) in convs:
            need = s
            if need <= 1e-9:
                continue
            # available per leg = sum of shares with buy ts <= cts (FIFO-eligible)
            # We burn s from EACH chosen leg (neg-risk: simultaneous on subset S).
            # To honor "consume s shares" semantics from the spec, we treat one conversion
            # of s sets as: pick subset S; each leg in S loses s shares.
            # Choose S by policy among legs whose eligible available >= s (a leg with <s
            # eligible cannot participate in a full-s conversion; it could only join a
            # smaller sub-conversion -> handled by splitting, see below).

            # eligible available per leg
            avail = {}
            for cid, q in legs.items():
                a = 0.0
                for (ts, sh, pr) in q:
                    if ts <= cts: a += sh
                    else: break  # sorted by ts
                if a > 1e-12: avail[cid] = a
            if not avail:
                continue  # conversion drew from legs with no observed eligible buys

            order = sorted(avail, key=lambda c: -avail[c])  # thickest first

            # decide how many legs k to involve
            if k_policy == 'all':
                chosen = order  # every leg with eligible stock
            elif k_policy == 'two':
                chosen = order[:2]
            elif k_policy == 'half':
                chosen = order[:max(2, math.ceil(N/2))]
            elif isinstance(k_policy, int):
                chosen = order[:k_policy]
            else:
                chosen = order

            # Now distribute `need` sets of consumption across `chosen` legs.
            # Physics-faithful: each set converted burns 1 share from k legs simultaneously.
            # But with heterogeneous leg depth and a single observed `s`, we approximate by
            # consuming `need` shares total spread thickest-first, capped by each leg's avail,
            # FIFO within leg. This is the SAME total-shares-burned as baseline when k='all'
            # only if every leg has >= need; otherwise differs. To make k meaningfully reduce
            # burned shares, we cap TOTAL burned at need * (effective spread) is wrong.
            #
            # CLEAN MODEL: a conversion of s sets removes EXACTLY s shares from EACH chosen leg
            # (capped by that leg's eligible avail, FIFO). Total burned = sum over chosen of
            # min(s, avail_leg). Baseline ('all') = remove s from every leg. Alt (fewer legs)
            # = remove s from fewer legs => fewer shares burned => more residual.
            for cid in chosen:
                q = legs[cid]
                take_need = need  # s shares from this leg
                while take_need > 1e-9 and q and q[0][0] <= cts:
                    ts, sh, pr = q[0]
                    take = min(take_need, sh)
                    hold = cts - ts
                    w = take * pr
                    conv_usd += w; conv_shares += take
                    hold_pairs.append((hold, w))
                    if hold <= TWO_MIN: usd_within_2min += w
                    take_need -= take
                    if take >= sh - 1e-9:
                        q.popleft()
                    else:
                        q[0] = (ts, sh - take, pr)

        # leftover across all legs = residual (held to settlement / data end)
        for cid, q in legs.items():
            while q:
                ts, sh, pr = q.popleft()
                resid_shares += sh
                w = sh * pr
                resid_usd += w
                resid_hold_pairs.append((DATA_END - ts, w))

    tot_usd = conv_usd + resid_usd
    # weighted median + within-2min on converted usd
    hp = sorted(hold_pairs)
    twk = sum(w for h, w in hold_pairs if h <= TWO_MIN)
    cw = conv_usd

    def wmed(pairs):
        if not pairs: return None
        ps = sorted(pairs); tot = sum(w for _, w in ps); half = tot/2; c = 0
        for v, w in ps:
            c += w
            if c >= half: return v
        return ps[-1][0]

    return {
        "k_policy": str(k_policy),
        "conv_usd": conv_usd, "resid_usd": resid_usd, "tot_usd": tot_usd,
        "conv_shares": conv_shares, "resid_shares": resid_shares,
        "residual_pct_usd": 100*resid_usd/tot_usd if tot_usd > 0 else None,
        "converted_pct_usd": 100*conv_usd/tot_usd if tot_usd > 0 else None,
        "residual_pct_shares": 100*resid_shares/(conv_shares+resid_shares) if (conv_shares+resid_shares) > 0 else None,
        "within2min_pct_of_converted": 100*twk/cw if cw > 0 else None,
        "within2min_pct_of_total": 100*twk/tot_usd if tot_usd > 0 else None,
        "wmed_hold_converted_s": wmed(hold_pairs),
        "wmed_hold_residual_s": wmed(resid_hold_pairs),
    }


results = {}
for kp in ['all', 'half', 'two']:
    print("running k_policy =", kp, file=sys.stderr)
    results[str(kp)] = run_model(kp)

print(json.dumps(results, indent=2))

# pretty table
def f(x):
    if x is None: return "  -  "
    if x < 90: return f"{x:.0f}s"
    if x < 5400: return f"{x/60:.1f}m"
    if x < 172800: return f"{x/3600:.1f}h"
    return f"{x/86400:.1f}d"

print("\n========== SUMMARY ==========", file=sys.stderr)
hdr = f"{'policy':>8} {'resid%$':>8} {'resid%sh':>9} {'conv%$':>8} {'<=2min%conv':>11} {'<=2min%tot':>11} {'wMedHold':>9}"
print(hdr)
for kp in ['all', 'half', 'two']:
    if kp not in results: continue
    r = results[kp]
    print(f"{r['k_policy']:>8} {r['residual_pct_usd']:>8.2f} {r['residual_pct_shares']:>9.2f} "
          f"{r['converted_pct_usd']:>8.2f} {r['within2min_pct_of_converted']:>11.1f} "
          f"{r['within2min_pct_of_total']:>11.1f} {f(r['wmed_hold_converted_s']):>9}")
