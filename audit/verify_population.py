# -*- coding: utf-8 -*-
"""
Independent verification (my own code, not re-running the user's scripts).

Goal A: single streaming pass over raw_activity_full.json, build per-eventSlug:
  - distinct NO-BUY conditionIds + first NO-BUY ts per leg
  - all NO-BUY usdc, total trade legs
  - conversion rows (ts, size, usdc)
  - outcome-label multiset across this event's TRADE rows (to judge bucket/winner vs 2-outcome)
  - per-leg outcome label + NO-buy usdc (to describe sample events)

Goal B: for 7 known episode slugs, measure:
    first NO BUY ts  ->  ts when ALL N distinct NO-BUY legs have each been touched once
  (N = number of distinct NO-BUY conditionIds observed in raw for that event)

Goal C: classify genuine neg-risk multi-outcome events for sampling.
"""
import sys, io, json, random
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

EP_SLUGS = {
    "when-will-gpt-5pt6-be-released": ("ep_4", "GPT", 0),
    "fed-decision-in-july-181": ("ep_6", "Fed", 0),
    "colombia-presidential-election": ("ep_8", "Colombia", 0),
    "spacex-closing-market-cap-end-of-ipo-month-20260606222757973": ("ep_9", "SpaceX", 9.2*3600),
    "2026-nba-champion": ("ep_10", "NBA", 0),
    "elon-musk-of-tweets-june-22-june-24": ("ep_13", "Elon6/22", 31.6*3600),
    "elon-musk-of-tweets-june-1-june-3": ("ep_14", "Elon6/1", 40),
}

# per-event accumulator
ev = defaultdict(lambda: {
    "no_first_ts": {},      # cid -> first NO-BUY ts
    "no_buy_usdc": 0.0,
    "no_buy_cnt": 0,
    "conv_ts": [],          # conversion timestamps
    "conv_usdc": 0.0,
    "conv_size": 0.0,
    "all_outcomes": defaultdict(int),   # outcome label -> trade count (any side)
    "no_leg_label": {},     # cid -> outcome label (should be "No")
    "leg_title_usdc": defaultdict(float),  # cid -> NO-buy usdc (for describing legs)
    "n_trade_rows": 0,
    "yes_buy_usdc": 0.0,
})

maxts = 0
n = 0
with open(RAW, 'rb') as fh:
    for r in ijson.items(fh, 'item'):
        n += 1
        t = r.get('type')
        es = r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        ts = r.get('timestamp')
        try:
            ts = int(ts)
        except Exception:
            continue
        if ts > maxts:
            maxts = ts
        e = ev[es]
        if t == "TRADE":
            side = r.get('side'); out = r.get('outcome')
            e["n_trade_rows"] += 1
            if out is not None:
                e["all_outcomes"][out] += 1
            try: us = float(r.get('usdcSize') or 0)
            except: us = 0.0
            if side == "BUY" and out == "No":
                cid = r.get('conditionId') or ""
                if cid not in e["no_first_ts"] or ts < e["no_first_ts"][cid]:
                    e["no_first_ts"][cid] = ts
                e["no_buy_usdc"] += us
                e["no_buy_cnt"] += 1
                e["leg_title_usdc"][cid] += us
            elif side == "BUY" and out == "Yes":
                e["yes_buy_usdc"] += us
        elif t == "CONVERSION":
            try: sz = float(r.get('size') or 0)
            except: sz = 0.0
            try: us = float(r.get('usdcSize') or 0)
            except: us = 0.0
            e["conv_ts"].append(ts)
            e["conv_usdc"] += us
            e["conv_size"] += sz
        if n % 100000 == 0:
            print("...scan", n, file=sys.stderr)

print("rows scanned:", n, "data_end_ts:", maxts, file=sys.stderr)

# ---------- Goal B: 7 episodes ----------
print("\n================ GOAL B: 7 doc episodes — first NO-buy -> all-N-legs touched ================")
def fmt(x):
    if x < 90: return f"{x:.0f}s"
    if x < 5400: return f"{x/60:.1f}m"
    if x < 172800: return f"{x/3600:.2f}h"
    return f"{x/86400:.2f}d"

ep_results = {}
for slug, (epname, label, docval) in EP_SLUGS.items():
    e = ev.get(slug)
    if not e or not e["no_first_ts"]:
        print(f"  {epname:6} {label:9} slug={slug}  NOT FOUND in raw")
        ep_results[epname] = {"found": False}
        continue
    firsts = e["no_first_ts"]
    n_legs = len(firsts)
    t0 = min(firsts.values())
    t_all = max(firsts.values())
    measured = t_all - t0
    docfmt = fmt(docval) if docval > 0 else "0s"
    print(f"  {epname:6} {label:9} N={n_legs:2}  first_NO_buy->all_legs_touched = {fmt(measured):>8} "
          f"({measured:.0f}s)   DOC={docfmt:>8} ({docval:.0f}s)")
    ep_results[epname] = {
        "found": True, "slug": slug, "label": label, "n_legs": n_legs,
        "measured_s": measured, "measured_fmt": fmt(measured),
        "doc_s": docval, "doc_fmt": docfmt,
        "first_ts": t0, "all_ts": t_all,
        "n_conv": len(e["conv_ts"]), "no_buy_usdc": e["no_buy_usdc"],
    }

# ---------- Goal C: classify neg-risk multi-outcome events ----------
negrisk = []
for es, e in ev.items():
    nlegs = len(e["no_first_ts"])
    if e["conv_ts"] and nlegs >= 3:
        negrisk.append((es, e, nlegs))
print(f"\n================ GOAL C: neg-risk events (>=1 CONVERSION & >=3 NO-BUY legs) = {len(negrisk)} ================")
tot_no = sum(e["no_buy_usdc"] for _, e, _ in negrisk)
tot_conv = sum(e["conv_usdc"] for _, e, _ in negrisk)
tot_yes = sum(e["yes_buy_usdc"] for _, e, _ in negrisk)
print(f"  total NO-buy=${tot_no:,.0f}  total CONVERSION=${tot_conv:,.0f}  total YES-buy=${tot_yes:,.0f}")

# ---------- Goal A: sample ~8 events, describe ----------
# Deterministic sample: pick by spread of capital + the known episodes excluded.
random.seed(42)
# stratify: pick across capital deciles to avoid only-whales
negrisk_sorted = sorted(negrisk, key=lambda x: -x[1]["no_buy_usdc"])
ncand = len(negrisk_sorted)
# pick 8 spread across the size distribution, NOT including the 7 doc episodes
ep_slug_set = set(EP_SLUGS.keys())
pool = [x for x in negrisk_sorted if x[0] not in ep_slug_set]
# indices at ~ 0,5%,15%,30%,45%,60%,75%,90% of size-ranked list
fracs = [0.0, 0.04, 0.12, 0.25, 0.40, 0.55, 0.72, 0.90]
picks = []
seen = set()
for fr in fracs:
    idx = min(int(fr*len(pool)), len(pool)-1)
    while idx in seen and idx < len(pool)-1:
        idx += 1
    seen.add(idx)
    picks.append(pool[idx])

print(f"\n================ GOAL A: 8 sampled neg-risk events (size-stratified, excl. doc 7) ================")
def share_to_str(d, topk=12):
    items = sorted(d.items(), key=lambda kv: -kv[1])
    return ", ".join(f"{k}:{v}" for k, v in items[:topk])

sample_out = []
for (es, e, nlegs) in picks:
    outc = dict(e["all_outcomes"])
    # how many distinct outcome labels (Yes/No vs candidate names)
    labels = set(outc.keys())
    # NO-buy spread across legs
    leg_usds = sorted(e["leg_title_usdc"].values(), reverse=True)
    top_leg_share = (leg_usds[0]/sum(leg_usds)*100) if leg_usds and sum(leg_usds)>0 else 0
    print(f"\n  SLUG: {es}")
    print(f"    N NO-legs={nlegs}  NO-buy=${e['no_buy_usdc']:,.0f}  conv_rows={len(e['conv_ts'])} "
          f"conv$=${e['conv_usdc']:,.0f}  yes-buy=${e['yes_buy_usdc']:,.0f}")
    print(f"    distinct outcome labels among TRADE rows: {sorted(labels)}")
    print(f"    outcome trade-count breakdown: {share_to_str(outc)}")
    print(f"    NO-buy$ spread across legs (desc, top10): {[round(x) for x in leg_usds[:10]]}  "
          f"top-leg={top_leg_share:.0f}% of NO-buy")
    sample_out.append({
        "slug": es, "n_legs": nlegs, "no_buy_usdc": e["no_buy_usdc"],
        "n_conv": len(e["conv_ts"]), "conv_usdc": e["conv_usdc"],
        "yes_buy_usdc": e["yes_buy_usdc"],
        "outcome_labels": sorted(labels),
        "top_leg_pct": top_leg_share,
    })

json.dump({"episodes": ep_results, "sample": sample_out,
           "n_negrisk": len(negrisk), "tot_no": tot_no, "tot_conv": tot_conv,
           "data_end_ts": maxts},
          open(r"C:\Users\zexi\pmscan\audit\verify_out.json", "w"), indent=2)
print("\nWROTE verify_out.json")
