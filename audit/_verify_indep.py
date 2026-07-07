# -*- coding: utf-8 -*-
# Independent re-derivation. No import of user's logic.
import sys, io, json
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

def to_f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

def to_i(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None

# Per event accumulate:
#   no_buys[cid] = list of (ts, shares, price)   (NO BUY legs)
#   convs        = list of (ts, sets)
events = defaultdict(lambda: {"no": defaultdict(list), "conv": []})

n = 0
maxts = 0
type_counts = defaultdict(int)
with open(RAW, 'rb') as fh:
    for r in ijson.items(fh, 'item'):
        n += 1
        t = r.get('type')
        type_counts[t] += 1
        ts = to_i(r.get('timestamp'))
        if ts is None:
            continue
        if ts > maxts:
            maxts = ts
        es = r.get('eventSlug')
        if not es:
            # fall back so we don't merge unrelated rows under empty key
            es = r.get('slug') or r.get('conditionId') or "__noevent__"
        if t == "TRADE":
            if r.get('side') == "BUY" and r.get('outcome') == "No":
                cid = r.get('conditionId') or ""
                sh = to_f(r.get('size'))
                pr = to_f(r.get('price'))
                events[es]["no"][cid].append((ts, sh, pr))
        elif t == "CONVERSION":
            sets = to_f(r.get('size'))
            events[es]["conv"].append((ts, sets))
        if n % 50000 == 0:
            print(f"...scanned {n}", file=sys.stderr)

DATA_END = maxts
print(f"rows={n}  data_end_ts={maxts}", file=sys.stderr)
print("type_counts=", dict(type_counts), file=sys.stderr)

# neg-risk = eventSlug with >=1 CONVERSION and >=3 distinct NO-buy legs (conditionIds with >=1 buy)
negrisk = []
for es, e in events.items():
    nlegs = sum(1 for cid, lst in e["no"].items() if lst)
    if e["conv"] and nlegs >= 3:
        negrisk.append((es, e, nlegs))

print(f"neg-risk events (>=1 CONVERSION & >=3 NO legs): {len(negrisk)}")

def median(xs):
    if not xs:
        return None
    s = sorted(xs)
    m = len(s)
    if m % 2 == 1:
        return s[m // 2]
    return 0.5 * (s[m // 2 - 1] + s[m // 2])

# ---------------- (a) first NO BUY ts -> first CONVERSION ts ----------------
first_gaps = []
for es, e, nlegs in negrisk:
    t0 = min(min(x[0] for x in lst) for lst in e["no"].values() if lst)
    fc = min(c[0] for c in e["conv"])
    first_gaps.append(fc - t0)

a_median = median(first_gaps)
a_within120 = sum(1 for g in first_gaps if g <= 120) / len(first_gaps) if first_gaps else None
# also report negative-gap count (conversion before any NO buy = data oddity)
neg_count = sum(1 for g in first_gaps if g < 0)

print("\n(a) first NO-BUY -> first CONVERSION")
print(f"    n={len(first_gaps)}  median={a_median:.1f}s  pct<=120s={100*a_within120:.1f}%  (neg gaps={neg_count})")

# ---------------- (b) gap between consecutive CONVERSIONS within same event ----------------
inter_gaps = []
for es, e, nlegs in negrisk:
    cts = sorted(c[0] for c in e["conv"])
    for i in range(1, len(cts)):
        inter_gaps.append(cts[i] - cts[i - 1])
b_median = median(inter_gaps)
print("\n(b) inter-conversion gap (same event, consecutive)")
print(f"    n={len(inter_gaps)}  median={b_median:.1f}s")

# ---------------- (c) capital-weighted holding period via per-leg FIFO ----------------
# Each CONVERSION of s sets consumes up to s shares from each leg's oldest-first NO buys
# (only buys with ts <= conversion ts can be consumed).
# usd weight = consumed_shares * buy_price.
hold_secs = []      # holding seconds for consumed chunks
hold_usd  = []      # usd weight for consumed chunks
consumed_usd_total = 0.0
residual_usd_total = 0.0
consumed_sh_total = 0.0
residual_sh_total = 0.0

for es, e, nlegs in negrisk:
    convs = sorted(e["conv"])  # ascending ts
    for cid, lst in e["no"].items():
        if not lst:
            continue
        q = deque(sorted(lst))  # oldest first by ts; items (ts, shares, price)
        for (cts, s) in convs:
            need = s
            while need > 1e-9 and q and q[0][0] <= cts:
                bts, bsh, bpr = q[0]
                take = min(need, bsh)
                hold_secs.append(cts - bts)
                w = take * bpr
                hold_usd.append(w)
                consumed_usd_total += w
                consumed_sh_total += take
                need -= take
                if take >= bsh - 1e-9:
                    q.popleft()
                else:
                    q[0] = (bts, bsh - take, bpr)
        # leftover = naked residual
        while q:
            bts, bsh, bpr = q.popleft()
            residual_usd_total += bsh * bpr
            residual_sh_total += bsh

def wmedian(vals, weights, target=0.5):
    if not vals:
        return None
    pairs = sorted(zip(vals, weights))
    tot = sum(weights)
    if tot <= 0:
        return None
    cum = 0.0
    thr = target * tot
    for v, w in pairs:
        cum += w
        if cum >= thr:
            return v
    return pairs[-1][0]

c_wmedian = wmedian(hold_secs, hold_usd, 0.5)
# pct of NO-buy capital whose holding <=120s -- among CONSUMED capital? or all NO-buy capital?
# Report both interpretations.
consumed_le120_usd = sum(w for h, w in zip(hold_secs, hold_usd) if h <= 120)
total_nobuy_usd = consumed_usd_total + residual_usd_total

pct_consumed_le120_of_consumed = 100 * consumed_le120_usd / consumed_usd_total if consumed_usd_total > 0 else None
pct_consumed_le120_of_all = 100 * consumed_le120_usd / total_nobuy_usd if total_nobuy_usd > 0 else None
pct_capital_consumed = 100 * consumed_usd_total / total_nobuy_usd if total_nobuy_usd > 0 else None

print("\n(c) capital-weighted FIFO holding (NO-buy -> consuming conversion)")
print(f"    consumed: shares={consumed_sh_total:,.0f}  usd=${consumed_usd_total:,.0f}")
print(f"    residual: shares={residual_sh_total:,.0f}  usd=${residual_usd_total:,.0f}")
print(f"    total NO-buy usd=${total_nobuy_usd:,.0f}")
print(f"    pct of NO-buy capital CONSUMED by conversions = {pct_capital_consumed:.1f}%")
print(f"    capital-weighted median holding (consumed chunks) = {c_wmedian:.1f}s")
print(f"    pct of CONSUMED capital with holding<=120s = {pct_consumed_le120_of_consumed:.1f}%")
print(f"    pct of ALL NO-buy capital with holding<=120s = {pct_consumed_le120_of_all:.1f}%")

# negative holding sanity (buy ts == conv ts allowed; <0 impossible since we gate ts<=cts)
neg_hold = sum(1 for h in hold_secs if h < 0)
print(f"    (neg holdings={neg_hold}; should be 0 by construction)")

out = {
    "n_negrisk": len(negrisk),
    "a_first_conv_median_s": a_median,
    "a_pct_within_120s": 100 * a_within120,
    "a_neg_gaps": neg_count,
    "b_inter_conv_median_s": b_median,
    "c_capital_wmedian_hold_s": c_wmedian,
    "c_pct_capital_consumed": pct_capital_consumed,
    "c_pct_consumed_cap_le120": pct_consumed_le120_of_consumed,
    "c_pct_all_cap_le120": pct_consumed_le120_of_all,
    "consumed_usd": consumed_usd_total,
    "residual_usd": residual_usd_total,
    "total_nobuy_usd": total_nobuy_usd,
}
json.dump(out, open(r"C:\Users\zexi\pmscan\audit\_verify_indep_out.json", "w"), indent=2)
print("\nWROTE _verify_indep_out.json")
print(json.dumps(out, indent=2))
