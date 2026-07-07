import ijson
from collections import defaultdict
import statistics
import json

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# Per conditionId, collect:
#  - buys per side (outcomeIndex 0 / 1): list of (price, size, timestamp)
#  - merge total size
#  - whether outcomes are binary (Yes/No or 2 distinct outcome indices among buys)
buys = defaultdict(lambda: {0: [], 1: []})   # condId -> {idx: [(price,size,ts),...]}
merge_size = defaultdict(float)
outcome_labels = defaultdict(set)   # condId -> set of (outcomeIndex, outcome)
event_slug = {}

n_records = 0
with open(PATH, "rb") as f:
    for rec in ijson.items(f, "item"):
        n_records += 1
        t = rec.get("type")
        cid = rec.get("conditionId")
        if not cid:
            continue
        if t == "MERGE":
            merge_size[cid] += float(rec.get("size") or 0)
            event_slug.setdefault(cid, rec.get("eventSlug"))
        elif t == "TRADE" and rec.get("side") == "BUY":
            oi = rec.get("outcomeIndex")
            price = float(rec.get("price") or 0)
            size = float(rec.get("size") or 0)
            ts = rec.get("timestamp")
            oc = rec.get("outcome")
            outcome_labels[cid].add((oi, oc))
            event_slug.setdefault(cid, rec.get("eventSlug"))
            if oi in (0, 1):
                buys[cid][oi].append((price, size, ts))

print(f"Total records streamed: {n_records}")

def wavg(rows):
    # size-weighted avg price
    tot_sz = sum(s for _, s, _ in rows)
    if tot_sz == 0:
        return None, 0.0
    p = sum(pr * s for pr, s, _ in rows) / tot_sz
    return p, tot_sz

results = []          # per both-side+merge market
both_side_merge = 0

for cid, sides in buys.items():
    side0 = sides[0]
    side1 = sides[1]
    if not side0 or not side1:
        continue            # didn't buy BOTH sides
    if merge_size.get(cid, 0) <= 0:
        continue            # no merge
    # binary check: only outcome indices 0 and 1 present
    idxs = {oi for (oi, _) in outcome_labels[cid]}
    if not idxs.issubset({0, 1}):
        continue
    both_side_merge += 1

    p0, sz0 = wavg(side0)
    p1, sz1 = wavg(side1)
    # cheap = p, expensive = q
    if p0 <= p1:
        cheap = {"idx": 0, "price": p0, "size": sz0, "rows": side0}
        exp   = {"idx": 1, "price": p1, "size": sz1, "rows": side1}
    else:
        cheap = {"idx": 1, "price": p1, "size": sz1, "rows": side1}
        exp   = {"idx": 0, "price": p0, "size": sz0, "rows": side0}

    p = cheap["price"]; q = exp["price"]
    pair_cost = p + q
    msz = merge_size[cid]
    matched_qty = min(cheap["size"], exp["size"], msz)

    results.append({
        "cid": cid,
        "p": p, "q": q, "pair_cost": pair_cost,
        "matched_qty": matched_qty,
        "merge_size": msz,
        "cheap_rows": cheap["rows"],
        "exp_rows": exp["rows"],
        "cheap_size": cheap["size"], "exp_size": exp["size"],
        "event": event_slug.get(cid),
    })

neg = [r for r in results if r["pair_cost"] > 1.0]

total_loss = sum((r["pair_cost"] - 1.0) * r["matched_qty"] for r in neg)
neg_pct = (len(neg) / both_side_merge * 100.0) if both_side_merge else 0.0
avg_overpay = statistics.mean([r["pair_cost"] for r in neg]) if neg else 0.0
max_overpay = max([r["pair_cost"] for r in neg]) if neg else 0.0

# Stop-loss hypothesis test:
# For each neg market, median timestamp of expensive-leg buys vs cheap-leg buys.
exp_later_count = 0
delta_medians = []   # exp_median_ts - cheap_median_ts (positive => expensive bought later)
rising_exp_count = 0 # expensive leg: is last (latest) buy price >= first (earliest) buy price?
exp_pushes_over = 0  # markets where without the latest expensive buys, pair would be <=1

for r in neg:
    cheap_ts = [ts for (_, _, ts) in r["cheap_rows"] if ts is not None]
    exp_ts   = [ts for (_, _, ts) in r["exp_rows"] if ts is not None]
    if cheap_ts and exp_ts:
        cm = statistics.median(cheap_ts)
        em = statistics.median(exp_ts)
        delta_medians.append(em - cm)
        if em > cm:
            exp_later_count += 1
    # rising expensive price over time
    exp_sorted = sorted([(ts, pr) for (pr, _, ts) in r["exp_rows"] if ts is not None])
    if len(exp_sorted) >= 2:
        first_pr = exp_sorted[0][1]
        last_pr = exp_sorted[-1][1]
        if last_pr > first_pr:
            rising_exp_count += 1

median_delta = statistics.median(delta_medians) if delta_medians else 0.0

# Aggregate medians across all neg markets for the headline comparison
all_cheap_medians = []
all_exp_medians = []
for r in neg:
    cheap_ts = [ts for (_, _, ts) in r["cheap_rows"] if ts is not None]
    exp_ts   = [ts for (_, _, ts) in r["exp_rows"] if ts is not None]
    if cheap_ts:
        all_cheap_medians.append(statistics.median(cheap_ts))
    if exp_ts:
        all_exp_medians.append(statistics.median(exp_ts))

overall_cheap_median = statistics.median(all_cheap_medians) if all_cheap_medians else None
overall_exp_median = statistics.median(all_exp_medians) if all_exp_medians else None

summary = {
    "n_records": n_records,
    "n_both_side_merge_binary": both_side_merge,
    "n_negative_edge": len(neg),
    "negative_edge_pct": round(neg_pct, 2),
    "total_loss_usd": round(total_loss, 4),
    "avg_overpay_pair_cost": round(avg_overpay, 5),
    "max_overpay_pair_cost": round(max_overpay, 5),
    "exp_later_count": exp_later_count,
    "exp_later_pct_of_neg": round(exp_later_count / len(neg) * 100, 2) if neg else 0,
    "median_delta_exp_minus_cheap_sec": median_delta,
    "rising_exp_count": rising_exp_count,
    "rising_exp_pct_of_neg": round(rising_exp_count / len(neg) * 100, 2) if neg else 0,
    "overall_cheap_median_ts": overall_cheap_median,
    "overall_exp_median_ts": overall_exp_median,
}
print(json.dumps(summary, indent=2))

# show a few worst markets
neg_sorted = sorted(neg, key=lambda r: (r["pair_cost"]-1.0)*r["matched_qty"], reverse=True)
print("\nTop 10 loss markets (loss_usd, pair_cost, p, q, matched_qty, event):")
for r in neg_sorted[:10]:
    loss = (r["pair_cost"]-1.0)*r["matched_qty"]
    print(f"  {loss:8.2f}  pc={r['pair_cost']:.4f} p={r['p']:.4f} q={r['q']:.4f} mq={r['matched_qty']:.2f}  {r['event']}")
