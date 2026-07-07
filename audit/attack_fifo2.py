# -*- coding: utf-8 -*-
"""
Defensible alternative model for the FIFO attack.

PRIMARY ALT ('keeppace'): a leg participates in a conversion only if it has been bought
up enough to KEEP PACE with cumulative conversion volume. Concretely, at a conversion of
s sets at time cts with running cumulative converted-sets C (before this conversion):
  - a leg is "eligible" if its cumulative NO buys with ts<=cts  >=  C + s
    (i.e. it has enough inventory to have supplied every conversion so far + this one).
  - the conversion burns s shares (FIFO) from EACH eligible leg.
  - legs that have fallen behind (thin tail) are SKIPPED -> their shares accrue as residual.

This excludes the thin tail legs (C3's "one illiquid tail leg") from conversion WITHOUT
nuking balanced baskets, because in a balanced event ALL legs keep pace and the result
collapses to the baseline. It is strictly more conservative than 'all' (never burns a leg
'all' wouldn't, and skips legs that 'all' would partially drain).

Also report:
  - 'keeppace_soft': eligible if cumulative buys >= C + s but allow PARTIAL participation:
        burn min(s, avail) from eligible legs (closer to baseline; upper-ish bound of alt).
  - residual decomposed: how much residual is the thin-tail (C3) vs structural.

We reuse the same streaming pass.
"""
import sys, io, json, math, bisect
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
TWO_MIN = 120

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
negrisk = [(es, e) for es, e in ev.items()
           if e["conv"] and sum(1 for c, b in e["legs"].items() if b) >= 3]
print(f"neg-risk events: {len(negrisk)}", file=sys.stderr)


def run(mode):
    conv_usd = 0.0; resid_usd = 0.0; conv_shares = 0.0; resid_shares = 0.0
    hold_pairs = []; usd_within_2min = 0.0; resid_hold_pairs = []
    # residual decomposition: tail-leg residual (leg that NEVER kept pace at all) vs other
    tail_resid_usd = 0.0
    for es, e in negrisk:
        convs = sorted(e["conv"])
        legs = {}
        cum_buy_sorted = {}  # cid -> list of (ts, cum_shares) for prefix lookup
        for cid, b in e["legs"].items():
            if not b: continue
            sb = sorted((ts, sz, pr) for (ts, sz, pr) in b)
            legs[cid] = deque(sb)
            # prefix cumulative shares by ts: store parallel arrays (ts_list, cum_list)
            ts_list = []; cum_list = []; c = 0.0
            for (ts, sz, pr) in sb:
                c += sz; ts_list.append(ts); cum_list.append(c)
            cum_buy_sorted[cid] = (ts_list, cum_list)
        if not legs: continue

        def cum_buys_upto(cid, cts):
            ts_list, cum_list = cum_buy_sorted[cid]
            idx = bisect.bisect_right(ts_list, cts) - 1
            return cum_list[idx] if idx >= 0 else 0.0

        C = 0.0  # cumulative converted sets so far (event-level)
        for (cts, s) in convs:
            if s <= 1e-9: continue
            # eligibility
            elig = []
            for cid in legs:
                cb = cum_buys_upto(cid, cts)
                if mode in ('keeppace', 'keeppace_soft'):
                    if cb >= C + s - 1e-9:
                        elig.append(cid)
            for cid in elig:
                q = legs[cid]
                if mode == 'keeppace':
                    # burn exactly s (FIFO); eligibility guarantees enough eligible avail
                    need = s
                else:  # soft: burn up to s
                    need = s
                while need > 1e-9 and q and q[0][0] <= cts:
                    ts, sh, pr = q[0]
                    take = min(need, sh)
                    hold = cts - ts; w = take * pr
                    conv_usd += w; conv_shares += take
                    hold_pairs.append((hold, w))
                    if hold <= TWO_MIN: usd_within_2min += w
                    need -= take
                    if take >= sh - 1e-9: q.popleft()
                    else: q[0] = (ts, sh - take, pr)
            C += s
        # residual
        total_conv_event = C
        for cid, q in legs.items():
            cl = cum_buy_sorted[cid][1]
            leg_total = cl[-1] if cl else 0.0
            never_kept_pace = leg_total < total_conv_event  # thin tail leg
            while q:
                ts, sh, pr = q.popleft()
                resid_shares += sh; w = sh * pr; resid_usd += w
                resid_hold_pairs.append((DATA_END - ts, w))
                if never_kept_pace: tail_resid_usd += w

    tot = conv_usd + resid_usd
    def wmed(pairs):
        if not pairs: return None
        ps = sorted(pairs); T = sum(w for _, w in ps); half = T/2; c = 0
        for v, w in ps:
            c += w
            if c >= half: return v
        return ps[-1][0]
    return {
        "mode": mode,
        "residual_pct_usd": 100*resid_usd/tot if tot > 0 else None,
        "converted_pct_usd": 100*conv_usd/tot if tot > 0 else None,
        "residual_pct_shares": 100*resid_shares/(conv_shares+resid_shares) if (conv_shares+resid_shares) else None,
        "within2min_pct_of_converted": 100*usd_within_2min/conv_usd if conv_usd > 0 else None,
        "within2min_pct_of_total": 100*usd_within_2min/tot if tot > 0 else None,
        "wmed_hold_converted_s": wmed(hold_pairs),
        "resid_usd": resid_usd, "conv_usd": conv_usd, "tot_usd": tot,
        "tail_resid_usd": tail_resid_usd,
        "tail_resid_pct_of_total": 100*tail_resid_usd/tot if tot > 0 else None,
    }


res = {}
for m in ['keeppace', 'keeppace_soft']:
    print("running", m, file=sys.stderr)
    res[m] = run(m)
print(json.dumps(res, indent=2))

def f(x):
    if x is None: return "-"
    if x < 90: return f"{x:.0f}s"
    if x < 5400: return f"{x/60:.1f}m"
    if x < 172800: return f"{x/3600:.1f}h"
    return f"{x/86400:.1f}d"
print("\npolicy           resid%$  conv%$  <=2min%conv  <=2min%tot  wMedHold  tail-resid%$")
for m in ['keeppace', 'keeppace_soft']:
    r = res[m]
    print(f"{m:16} {r['residual_pct_usd']:6.2f}  {r['converted_pct_usd']:6.2f}  "
          f"{r['within2min_pct_of_converted']:10.1f}  {r['within2min_pct_of_total']:9.1f}  "
          f"{f(r['wmed_hold_converted_s']):>8}  {r['tail_resid_pct_of_total']:6.2f}")
