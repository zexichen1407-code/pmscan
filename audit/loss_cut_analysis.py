import ijson
import statistics
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"

# First pass: collect all TRADE events (BUY/SELL) keyed by (conditionId, outcome)
# We need them chronological per market+outcome, so collect then sort.
# Each event: (timestamp, side, price, size, usdcSize)

events = defaultdict(list)  # key -> list of events
# Also track BUY size per (conditionId, outcome) to assess unmatched legs
buy_size_by_key = defaultdict(float)   # (cond, outcome) -> total buy size
sell_size_by_key = defaultdict(float)
# track all outcomes seen per condition for matched/unmatched analysis
outcomes_by_cond_buysize = defaultdict(lambda: defaultdict(float))  # cond -> outcome -> buy size

n_total = 0
n_trade = 0
n_buy = 0
n_sell = 0
type_counts = defaultdict(int)

with open(PATH, "rb") as f:
    for obj in ijson.items(f, "item"):
        n_total += 1
        t = obj.get("type")
        type_counts[t] += 1
        if t != "TRADE":
            continue
        n_trade += 1
        side = obj.get("side")
        cond = obj.get("conditionId")
        outcome = obj.get("outcome")
        price = float(obj.get("price") or 0)
        size = float(obj.get("size") or 0)
        usdc = float(obj.get("usdcSize") or 0)
        ts = int(obj.get("timestamp") or 0)
        key = (cond, outcome)
        events[key].append((ts, side, price, size, usdc))
        if side == "BUY":
            n_buy += 1
            buy_size_by_key[key] += size
            outcomes_by_cond_buysize[cond][outcome] += size
        elif side == "SELL":
            n_sell += 1
            sell_size_by_key[key] += size

print("TOTAL_RECORDS", n_total)
print("TYPE_COUNTS", dict(type_counts))
print("N_TRADE", n_trade, "N_BUY", n_buy, "N_SELL", n_sell)

# Now process chronologically per (conditionId, outcome) with running weighted avg buy cost.
total_sells = 0
total_sell_usd = 0.0
total_realized_pnl = 0.0

loss_count = 0
loss_usd = 0.0   # total $ lost (positive magnitude)
gain_count = 0
gain_usd = 0.0   # total $ gained

loss_pct_list = []  # (cost - sellprice)/cost for losing sells

# For unmatched-leg analysis: classify each SELL by whether its market leg was unmatched.
# unmatched = abs(buyA - buyB)/max(buyA,buyB) high. We'll measure sell USD in unmatched vs matched markets.
# For timing: for each market+outcome, find min/max sell timestamp position relative to all trade timestamps.

# Per-sell records for timing & unmatched
sell_records = []  # dict per sell

for key, evs in events.items():
    cond, outcome = key
    evs.sort(key=lambda x: x[0])  # chronological ascending
    run_size = 0.0   # current shares held (from buys, reduced by sells)
    run_cost_total = 0.0  # total cost basis value of held shares
    # avg cost = run_cost_total / run_size
    # timing: timestamps of trades in this key
    ts_list = [e[0] for e in evs]
    tmin, tmax = min(ts_list), max(ts_list)
    span = (tmax - tmin) if tmax > tmin else 0
    for (ts, side, price, size, usdc) in evs:
        if side == "BUY":
            run_size += size
            run_cost_total += price * size  # use price*size as cost (consistent)
        elif side == "SELL":
            # avg buy cost at this point
            if run_size > 1e-12:
                avg_cost = run_cost_total / run_size
            else:
                avg_cost = 0.0  # no prior buys (oversell / short); treat cost as 0 -> realized = sell proceeds
            realized = (price - avg_cost) * size
            total_sells += 1
            total_sell_usd += price * size
            total_realized_pnl += realized
            # reduce running position
            reduce = min(size, run_size)
            if run_size > 1e-12:
                run_cost_total -= avg_cost * reduce
                run_size -= reduce
            # classify
            if price < avg_cost - 1e-9:
                loss_count += 1
                loss_usd += (avg_cost - price) * size
                if avg_cost > 1e-9:
                    loss_pct_list.append((avg_cost - price) / avg_cost)
            elif price > avg_cost + 1e-9:
                gain_count += 1
                gain_usd += (price - avg_cost) * size
            # timing position within market
            pos = (ts - tmin) / span if span > 0 else 1.0
            sell_records.append({
                "cond": cond, "outcome": outcome, "ts": ts, "price": price,
                "size": size, "avg_cost": avg_cost, "realized": realized,
                "pos": pos, "is_loss": price < avg_cost - 1e-9,
            })

median_loss_pct = statistics.median(loss_pct_list) * 100 if loss_pct_list else 0.0

print("=== SELL ANALYSIS ===")
print("total_sells", total_sells)
print("total_sell_usd", round(total_sell_usd, 2))
print("total_realized_pnl", round(total_realized_pnl, 2))
print("loss_count", loss_count, "loss_usd", round(loss_usd, 2))
print("gain_count", gain_count, "gain_usd", round(gain_usd, 2))
print("median_loss_pct", round(median_loss_pct, 2))
print("avg_loss_pct", round(statistics.mean(loss_pct_list)*100,2) if loss_pct_list else 0)

# ---- Unmatched leg analysis ----
# For each condition, compute buy size per outcome. Unmatched degree = abs imbalance.
# A SELL is "in an unmatched market" if, for its condition, the two main outcomes' buy sizes are very imbalanced.
def imbalance(cond):
    sizes = list(outcomes_by_cond_buysize[cond].values())
    if not sizes:
        return None
    if len(sizes) == 1:
        return 1.0
    sizes_sorted = sorted(sizes, reverse=True)
    top = sizes_sorted[0]
    second = sizes_sorted[1] if len(sizes_sorted) > 1 else 0.0
    if top <= 1e-12:
        return None
    # imbalance: how much the largest leg exceeds the second largest, normalized
    return (top - second) / top  # 0 = perfectly matched, 1 = fully unmatched

sell_usd_unmatched = 0.0
sell_usd_matched = 0.0
sell_cnt_unmatched = 0
sell_cnt_matched = 0
loss_usd_unmatched = 0.0
loss_usd_matched = 0.0
IMB_THRESH = 0.5  # >50% of the leg is unmatched

for r in sell_records:
    imb = imbalance(r["cond"])
    usd = r["price"] * r["size"]
    lossmag = (r["avg_cost"] - r["price"]) * r["size"] if r["is_loss"] else 0.0
    if imb is not None and imb >= IMB_THRESH:
        sell_usd_unmatched += usd
        sell_cnt_unmatched += 1
        loss_usd_unmatched += lossmag
    else:
        sell_usd_matched += usd
        sell_cnt_matched += 1
        loss_usd_matched += lossmag

print("=== UNMATCHED LEG ===")
print("sell_usd_unmatched", round(sell_usd_unmatched,2), "cnt", sell_cnt_unmatched, "lossusd", round(loss_usd_unmatched,2))
print("sell_usd_matched", round(sell_usd_matched,2), "cnt", sell_cnt_matched, "lossusd", round(loss_usd_matched,2))
print("pct_sell_usd_in_unmatched", round(100*sell_usd_unmatched/(sell_usd_unmatched+sell_usd_matched),2) if (sell_usd_unmatched+sell_usd_matched)>0 else 0)

# ---- Timing ----
# Position of sells within the market's trade span (0=start,1=end)
positions = [r["pos"] for r in sell_records]
loss_positions = [r["pos"] for r in sell_records if r["is_loss"]]
print("=== TIMING ===")
print("median_pos_all_sells", round(statistics.median(positions),3) if positions else 0)
print("mean_pos_all_sells", round(statistics.mean(positions),3) if positions else 0)
print("median_pos_loss_sells", round(statistics.median(loss_positions),3) if loss_positions else 0)
print("mean_pos_loss_sells", round(statistics.mean(loss_positions),3) if loss_positions else 0)
# fraction of sells in last 20% of market activity
late = sum(1 for p in positions if p >= 0.8)
print("frac_sells_in_last_20pct", round(100*late/len(positions),2) if positions else 0)
late_loss = sum(1 for p in loss_positions if p >= 0.8)
print("frac_loss_sells_in_last_20pct", round(100*late_loss/len(loss_positions),2) if loss_positions else 0)

# how many distinct markets have sells
markets_with_sells = set((r["cond"]) for r in sell_records)
print("distinct_conditions_with_sells", len(markets_with_sells))
print("distinct_cond_outcome_with_sells", len(set((r["cond"],r["outcome"]) for r in sell_records)))
