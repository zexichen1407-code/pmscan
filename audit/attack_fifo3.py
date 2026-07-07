# -*- coding: utf-8 -*-
"""
FINAL adversarial model. The subset S burned by each conversion is UNOBSERVED (the
CONVERSION row's conditionId is a constant per event = neg-risk marker; usdcSize==size
gives no k info). So converted/residual depends on an unidentified parameter. We bound it.

RIGOROUS BOUNDS that do NOT need k:
  * UPPER bound on converted (= baseline 'all'): every conversion burns min(s, avail) from
    every leg. This is the MAX shares any assignment could burn, because total burned by a
    conversion of s sets across subset S is sum_{i in S} (shares burned on i) and each leg
    can give at most min(s, avail_i). Summing over ALL legs is the max. => orchestrator's 2%.
  * LOWER bound on converted: each conversion of s sets must burn at least... s shares total?
    NO. Physically a conversion of s sets on |S|=k burns exactly s on each of k>=2 legs, so
    >= 2s shares total (k>=2 for any profitable neg-risk conversion). So total burned >=
    2 * sum(s) = 2 * total_conv_sets. That's a floor on converted shares, not tied to legs.

ALT MODELS (assign the floor-respecting burn to the THICKEST legs, leaving thin tail naked):
  'thick_k=2'  : each conversion burns s from the 2 thickest legs (min profitable subset).
  'thick_k=N/2': from ceil(N/2) thickest legs.
  'thick_full' : from every leg that can supply >= s (skip legs with avail<s -> they reside).
A leg is eligible only if its CURRENT remaining avail (buys ts<=cts) >= s (must give full s,
since a conversion of s sets needs s on each chosen leg). Thickest = most remaining avail.
"""
import sys, io, json, math
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


def remaining_avail(q, cts):
    a = 0.0
    for (ts, sh, pr) in q:
        if ts <= cts: a += sh
        else: break
    return a


def run(mode):
    conv_usd = 0.0; resid_usd = 0.0; conv_sh = 0.0; resid_sh = 0.0
    hold_pairs = []; w2 = 0.0; resid_hold = []
    for es, e in negrisk:
        convs = sorted(e["conv"])
        legs = {cid: deque(sorted((ts, sz, pr) for (ts, sz, pr) in b))
                for cid, b in e["legs"].items() if b}
        N = len(legs)
        if mode == 'thick_k2': K = 2
        elif mode == 'thick_half': K = max(2, math.ceil(N/2))
        else: K = N  # thick_full / all-eligible
        for (cts, s) in convs:
            if s <= 1e-9: continue
            # candidate legs with any eligible stock (ts<=cts), thickest first
            allcand = sorted(((remaining_avail(q, cts), cid) for cid, q in legs.items()),
                             reverse=True)
            allcand = [(a, cid) for a, cid in allcand if a > 1e-9]
            if not allcand:
                continue
            # prefer legs that can supply the FULL s; if at least 2 such exist, restrict to them
            full = [(a, cid) for a, cid in allcand if a >= s - 1e-9]
            pool = full if len(full) >= 2 else allcand
            chosen = pool[:K]
            for a, cid in chosen:
                q = legs[cid]; need = s
                while need > 1e-9 and q and q[0][0] <= cts:
                    ts, sh, pr = q[0]
                    take = min(need, sh); hold = cts - ts; w = take * pr
                    conv_usd += w; conv_sh += take; hold_pairs.append((hold, w))
                    if hold <= TWO_MIN: w2 += w
                    need -= take
                    if take >= sh - 1e-9: q.popleft()
                    else: q[0] = (ts, sh - take, pr)
        for cid, q in legs.items():
            while q:
                ts, sh, pr = q.popleft()
                resid_sh += sh; w = sh*pr; resid_usd += w; resid_hold.append((DATA_END-ts, w))
    tot = conv_usd + resid_usd
    def wmed(p):
        if not p: return None
        ps = sorted(p); T = sum(w for _, w in ps); h = T/2; c = 0
        for v, w in ps:
            c += w
            if c >= h: return v
        return ps[-1][0]
    return {
        "mode": mode,
        "residual_pct_usd": 100*resid_usd/tot if tot else None,
        "converted_pct_usd": 100*conv_usd/tot if tot else None,
        "within2min_pct_converted": 100*w2/conv_usd if conv_usd else None,
        "within2min_pct_total": 100*w2/tot if tot else None,
        "wmed_hold_s": wmed(hold_pairs),
        "resid_usd": resid_usd, "conv_usd": conv_usd, "tot_usd": tot,
    }


res = {}
for m in ['thick_full', 'thick_half', 'thick_k2']:
    print("running", m, file=sys.stderr)
    res[m] = run(m)
print(json.dumps(res, indent=2))

def f(x):
    if x is None: return "-"
    if x < 90: return f"{x:.0f}s"
    if x < 5400: return f"{x/60:.1f}m"
    if x < 172800: return f"{x/3600:.1f}h"
    return f"{x/86400:.1f}d"
print("\npolicy        resid%$  conv%$  <=2min%conv  <=2min%tot  wMedHold")
for m in ['thick_full', 'thick_half', 'thick_k2']:
    r = res[m]
    print(f"{m:12} {r['residual_pct_usd']:7.2f}  {r['converted_pct_usd']:6.2f}  "
          f"{r['within2min_pct_converted']:10.1f}  {r['within2min_pct_total']:9.1f}  {f(r['wmed_hold_s']):>8}")
