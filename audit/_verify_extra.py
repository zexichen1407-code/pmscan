# -*- coding: utf-8 -*-
# Extra checks: C1 "first conv before all legs touched", C2 residual holding-to-settlement,
# and robustness of (a)/(c) under alternative neg-risk definition + matching choices.
import sys, io, json
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

def to_f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def to_i(x):
    try: return int(x)
    except (TypeError, ValueError): return None

events = defaultdict(lambda: {"no": defaultdict(list), "conv": []})
maxts = 0; n = 0
with open(RAW, 'rb') as fh:
    for r in ijson.items(fh, 'item'):
        n += 1
        t = r.get('type'); ts = to_i(r.get('timestamp'))
        if ts is None: continue
        if ts > maxts: maxts = ts
        es = r.get('eventSlug') or r.get('slug') or r.get('conditionId') or "__noevent__"
        if t == "TRADE" and r.get('side') == "BUY" and r.get('outcome') == "No":
            events[es]["no"][r.get('conditionId') or ""].append((ts, to_f(r.get('size')), to_f(r.get('price'))))
        elif t == "CONVERSION":
            events[es]["conv"].append((ts, to_f(r.get('size'))))
DATA_END = maxts

negrisk = [(es, e, sum(1 for c, l in e["no"].items() if l)) for es, e in events.items()
           if e["conv"] and sum(1 for c, l in e["no"].items() if l) >= 3]

def median(xs):
    if not xs: return None
    s = sorted(xs); m = len(s)
    return s[m//2] if m % 2 else 0.5*(s[m//2-1]+s[m//2])

# C1c: first conversion BEFORE all N legs touched once
before = 0
for es, e, nlegs in negrisk:
    leg_first = {cid: min(x[0] for x in l) for cid, l in e["no"].items() if l}
    t_alln = max(leg_first.values())
    fc = min(c[0] for c in e["conv"])
    if fc < t_alln:
        before += 1
print(f"C1c: first CONVERSION before all {nlegs}... legs first-touched: {before}/{len(negrisk)} = {100*before/len(negrisk):.1f}%")

# C2: residual holding to settlement (data end as censor proxy)
res_hold_days = []; res_usd = []
for es, e, nlegs in negrisk:
    convs = sorted(e["conv"])
    for cid, l in e["no"].items():
        if not l: continue
        q = deque(sorted(l))
        for (cts, s) in convs:
            need = s
            while need > 1e-9 and q and q[0][0] <= cts:
                bts, bsh, bpr = q[0]; take = min(need, bsh); need -= take
                if take >= bsh - 1e-9: q.popleft()
                else: q[0] = (bts, bsh - take, bpr)
        while q:
            bts, bsh, bpr = q.popleft()
            res_hold_days.append((DATA_END - bts) / 86400.0)
            res_usd.append(bsh * bpr)
def wmed(vals, weights, tgt=0.5):
    if not vals: return None
    pairs = sorted(zip(vals, weights)); tot = sum(weights)
    if tot <= 0: return None
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= tgt*tot: return v
    return pairs[-1][0]
print(f"C2 residual: usd-weighted median holding-to-dataEnd = {wmed(res_hold_days, res_usd):.2f} days  (plain median = {median(res_hold_days):.2f} days, n={len(res_hold_days)})")

# Robustness: alt neg-risk def = just >=1 conversion (no >=3 leg gate) -- does (a) move?
alt = [(es, e) for es, e in events.items() if e["conv"] and any(l for l in e["no"].values())]
fg = []
for es, e in alt:
    nob = [x[0] for l in e["no"].values() for x in l]
    if not nob: continue
    fg.append(min(c[0] for c in e["conv"]) - min(nob))
print(f"\nRobustness alt-def (>=1 conv, >=1 NO leg, no >=3 gate): n={len(fg)} median first-gap={median(fg):.1f}s  pct<=120s={100*sum(1 for g in fg if g<=120)/len(fg):.1f}%")
