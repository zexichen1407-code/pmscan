import ijson
import json
from collections import defaultdict
import statistics

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# Counters
type_counts = defaultdict(int)
type_usdc = defaultdict(float)
side_counts = defaultdict(int)

# Per-condition aggregation for round-trip + timing
# For each conditionId: collect BUY timestamps, SELL timestamps, MERGE/CONVERSION ts, REDEEM ts
cond = defaultdict(lambda: {
    "buy_ts": [],          # list of (ts, usdc)
    "sell_ts": [],
    "merge_ts": [],        # MERGE timestamps
    "conv_ts": [],         # CONVERSION timestamps
    "redeem_ts": [],
    "buy_usdc": 0.0,
    "title": "",
    "eventSlug": "",
})

n = 0
with open(PATH, "r", encoding="utf-8") as f:
    for row in ijson.items(f, "item"):
        n += 1
        t = row.get("type", "")
        usdc = row.get("usdcSize", 0) or 0
        try:
            usdc = float(usdc)
        except Exception:
            usdc = 0.0
        ts = row.get("timestamp", 0) or 0
        try:
            ts = int(ts)
        except Exception:
            ts = 0
        cid = row.get("conditionId", "") or ""
        side = row.get("side", "") or ""

        type_counts[t] += 1
        type_usdc[t] += usdc

        if t == "TRADE":
            side_counts[side] += 1

        c = cond[cid]
        if not c["title"] and row.get("title"):
            c["title"] = row.get("title", "")
        if not c["eventSlug"] and row.get("eventSlug"):
            c["eventSlug"] = row.get("eventSlug", "")

        if t == "TRADE":
            if side == "BUY":
                c["buy_ts"].append((ts, usdc))
                c["buy_usdc"] += usdc
            elif side == "SELL":
                c["sell_ts"].append((ts, usdc))
        elif t == "MERGE":
            c["merge_ts"].append(ts)
        elif t == "CONVERSION":
            c["conv_ts"].append(ts)
        elif t == "REDEEM":
            c["redeem_ts"].append(ts)

print("TOTAL ROWS:", n)
print()
print("=== TYPE COUNTS ===")
for t in sorted(type_counts, key=lambda k: -type_counts[k]):
    print(f"{t:18s} count={type_counts[t]:8d}  usdc={type_usdc[t]:,.2f}")
print()
print("=== SIDE COUNTS (TRADE) ===")
for s in sorted(side_counts, key=lambda k: -side_counts[k]):
    print(f"{s:8s} {side_counts[s]}")

# Save cond dict to a pickle-ish JSON for second-stage analysis
import pickle
with open(r"C:\Users\zexi\pmscan\audit\cond_agg.pkl", "wb") as out:
    pickle.dump(dict(cond), out)
print()
print("Saved cond_agg.pkl with", len(cond), "conditions")
