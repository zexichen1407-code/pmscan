import json, statistics
from collections import defaultdict

d = json.load(open(r"C:/Users/zexi/pmscan/audit/_aligned.json"))

# All rows are his BUY fills (align.py filtered side==BUY).
# Proxy for "maker buy": passive bid resting BELOW mid, got hit -> fill_price < mid.
# distance from mid (cents) = (mid - fill_price)*100 = -offset_interp*100.
# We use offset_interp (interpolated mid at fill time) as primary; report offset_prev as robustness.

def cents(x):  # distance in cents using interpolated mid
    return -x['offset_interp'] * 100.0

# Identify the two legs by their mid level.
# ZVEREV = expensive leg (mid ~0.78), COBOLLI = cheap leg (mid ~0.17).
legs = {'ZVEREV': [], 'COBOLLI': []}
for x in d:
    legs[x['token']].append(x)

# mid level per leg (time-weighted-ish: just mean of mkt_interp over his fills, plus from ph series)
ph = json.load(open(r"C:/Users/zexi/pmscan/audit/_ph_all.json"))
mid_series = {k: statistics.mean(p['p'] for p in v) for k, v in ph.items()}
mid_at_fills = {k: statistics.mean(x['mkt_interp'] for x in legs[k]) for k in legs}

print("=== Leg mid levels ===")
for k in legs:
    print(f"{k}: mean mid (full series) = {mid_series[k]:.4f}, mean mid (at his fills) = {mid_at_fills[k]:.4f}, n_fills={len(legs[k])}")

# MAKER BUY proxy: keep only fills where fill_price strictly below mid (offset_interp < 0)
# Use a tiny tolerance to avoid floating dust counting an at-mid fill.
TOL = 0.0005  # half a tenth of a cent
def maker_buys(rows):
    out = []
    for x in rows:
        off = x['offset_interp']
        if off < -TOL:  # filled below mid => passive bid hit
            out.append(x)
    return out

mb = {k: maker_buys(v) for k, v in legs.items()}

print("\n=== Maker-buy proxy counts (fill below mid) ===")
for k in mb:
    n_below = len(mb[k]); n_total = len(legs[k])
    n_above = sum(1 for x in legs[k] if x['offset_interp'] > TOL)
    n_at = n_total - n_below - n_above
    print(f"{k}: below_mid(maker)={n_below}  at_mid={n_at}  above_mid(taker?)={n_above}  total={n_total}")

def summ(rows):
    ds = sorted(cents(x) for x in rows)
    n = len(ds)
    def pct(p):
        if n == 0: return None
        i = min(n-1, max(0, int(round(p*(n-1)))))
        return ds[i]
    return {
        'n': n,
        'median': round(statistics.median(ds), 3) if ds else None,
        'mean': round(statistics.mean(ds), 3) if ds else None,
        'p25': round(pct(0.25), 3) if ds else None,
        'p75': round(pct(0.75), 3) if ds else None,
        'min': round(ds[0], 3) if ds else None,
        'max': round(ds[-1], 3) if ds else None,
    }

print("\n=== Distance from mid (cents) for maker buys ===")
res = {}
for k in mb:
    res[k] = summ(mb[k])
    print(f"{k}: {res[k]}")

# Map to expensive/cheap
exp_token = max(mid_at_fills, key=mid_at_fills.get)   # ZVEREV
cheap_token = min(mid_at_fills, key=mid_at_fills.get) # COBOLLI
d_exp = res[exp_token]
d_cheap = res[cheap_token]
mid_exp = mid_at_fills[exp_token]
mid_cheap = mid_at_fills[cheap_token]

print(f"\nEXPENSIVE leg = {exp_token} (mid~{mid_exp:.3f}); CHEAP leg = {cheap_token} (mid~{mid_cheap:.3f})")

# --- KEY TEST ---
# H_equal: d_exp - d_cheap ~ 0
# H_prop:  d_exp / d_cheap ~ mid_exp / mid_cheap
diff_med = d_exp['median'] - d_cheap['median']
ratio_dist = d_exp['median'] / d_cheap['median'] if d_cheap['median'] else None
ratio_mid = mid_exp / mid_cheap

print("\n=== KEY TEST ===")
print(f"median d_exp - d_cheap = {diff_med:.3f} cents  (==0 => equal_absolute)")
print(f"ratio d_exp/d_cheap    = {ratio_dist:.3f}")
print(f"ratio mid_exp/mid_cheap = {ratio_mid:.3f}  (proportional => these two ratios match)")

# Paired-by-minute robustness: align fills into time buckets where BOTH legs quoted,
# compute per-bucket (d_exp - d_cheap). Bucket = 60s.
def bucketize(rows, w=60):
    b = defaultdict(list)
    for x in rows:
        b[x['t'] // w].append(cents(x))
    return {k: statistics.median(v) for k, v in b.items()}

be = bucketize(mb[exp_token]); bc = bucketize(mb[cheap_token])
common = sorted(set(be) & set(bc))
paired_diffs = [be[k] - bc[k] for k in common]
paired_ratios = [be[k] / bc[k] for k in common if bc[k] > 0]
print(f"\nPaired buckets (60s, both legs maker-buy present): n={len(common)}")
if paired_diffs:
    print(f"  median paired (d_exp - d_cheap) = {statistics.median(paired_diffs):.3f} cents")
    print(f"  mean   paired (d_exp - d_cheap) = {statistics.mean(paired_diffs):.3f} cents")
if paired_ratios:
    print(f"  median paired (d_exp / d_cheap) = {statistics.median(paired_ratios):.3f}  vs mid ratio {ratio_mid:.3f}")

# How concentrated is the distance? Check what fraction sit at exactly 0.5c (half-cent peg).
for k in mb:
    ds = [round(cents(x), 4) for x in mb[k]]
    half = sum(1 for v in ds if abs(v - 0.5) < 0.05)
    print(f"\n{k}: fraction of maker buys at ~0.5c from mid: {half}/{len(ds)} = {half/len(ds):.2f}" if ds else f"{k}: none")
    # distribution histogram by 0.5c bins
    from collections import Counter
    binned = Counter(round(v*2)/2 for v in ds)  # 0.5c bins
    print(f"  {k} dist histogram (cents:count): " + ", ".join(f"{kk}:{vv}" for kk, vv in sorted(binned.items())[:12]))

out = {
    'exp_token': exp_token, 'cheap_token': cheap_token,
    'mid_exp': round(mid_exp,4), 'mid_cheap': round(mid_cheap,4),
    'mid_ratio': round(ratio_mid,3),
    'd_exp': d_exp, 'd_cheap': d_cheap,
    'median_diff_cents': round(diff_med,3),
    'dist_ratio': round(ratio_dist,3) if ratio_dist else None,
    'paired_n': len(common),
    'paired_median_diff': round(statistics.median(paired_diffs),3) if paired_diffs else None,
    'paired_median_ratio': round(statistics.median(paired_ratios),3) if paired_ratios else None,
}
json.dump(out, open(r"C:/Users/zexi/pmscan/audit/leg_distance_results.json","w"), indent=2)
print("\nwrote leg_distance_results.json")
