import ijson
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

events = defaultdict(list)
outcomes_by_cond_buysize = defaultdict(lambda: defaultdict(float))
with open(PATH, "rb") as f:
    for obj in ijson.items(f, "item"):
        if obj.get("type") != "TRADE":
            continue
        side = obj.get("side")
        if side not in ("BUY", "SELL"):
            continue
        cond = obj.get("conditionId"); outcome = obj.get("outcome")
        key = (cond, outcome)
        price = float(obj.get("price") or 0); size = float(obj.get("size") or 0)
        ts = int(obj.get("timestamp") or 0)
        events[key].append((ts, side, price, size))
        if side == "BUY":
            outcomes_by_cond_buysize[cond][outcome] += size

# Two accountings:
# A) ALL sells, oversell cost basis = 0 (the naive reading of task) -> phantom gains
# B) ONLY sells matched against a prior running BUY basis (proper realized P&L);
#    sells beyond held size are split: matched portion uses avg cost, excess flagged.

# We'll report B as the headline (true cost-basis P&L) but track both.
res = {}
for label in ("matched_only",):
    pass

total_sells = 0
total_sell_usd = 0.0

# matched accounting
m_realized = 0.0
m_loss_cnt = 0; m_loss_usd = 0.0; m_gain_cnt = 0; m_gain_usd = 0.0
m_loss_pct = []
matched_sell_count = 0  # sells fully or partially against real basis
m_matched_proceeds = 0.0

# excess (no basis) tracking
excess_proceeds = 0.0
excess_size_sells = 0  # sells with ANY excess (no/partial basis)
pure_excess_sells = 0  # sells with ZERO basis at all

sell_records = []

for key, evs in events.items():
    cond, outcome = key
    evs.sort(key=lambda x: x[0])
    run_size = 0.0; run_cost = 0.0
    ts_list = [e[0] for e in evs]
    tmin, tmax = min(ts_list), max(ts_list); span = tmax - tmin
    for (ts, side, price, size) in evs:
        if side == "BUY":
            run_size += size; run_cost += price * size
        else:  # SELL
            total_sells += 1
            total_sell_usd += price * size
            matched_size = min(size, run_size)
            excess = size - matched_size
            if run_size > 1e-12:
                avg = run_cost / run_size
            else:
                avg = None
            # matched portion realized pnl
            if matched_size > 1e-12 and avg is not None:
                realized = (price - avg) * matched_size
                m_realized += realized
                m_matched_proceeds += price * matched_size
                matched_sell_count += 1
                if price < avg - 1e-9:
                    m_loss_cnt += 1
                    m_loss_usd += (avg - price) * matched_size
                    if avg > 1e-9:
                        m_loss_pct.append((avg - price) / avg)
                elif price > avg + 1e-9:
                    m_gain_cnt += 1
                    m_gain_usd += (price - avg) * matched_size
                # reduce
                run_cost -= avg * matched_size
                run_size -= matched_size
            if excess > 1e-9:
                excess_proceeds += price * excess
                excess_size_sells += 1
                if matched_size <= 1e-12:
                    pure_excess_sells += 1
            pos = (ts - tmin)/span if span > 0 else 1.0
            sell_records.append({"cond":cond,"outcome":outcome,"ts":ts,"price":price,
                "size":size,"avg":avg,"matched":matched_size,"excess":excess,
                "is_loss": (avg is not None and matched_size>1e-12 and price < avg-1e-9),
                "pos":pos})

print("=== HEADLINE: ALL SELLS ===")
print("total_sells", total_sells)
print("total_sell_usd", round(total_sell_usd,2))
print()
print("=== MATCHED-BASIS REALIZED P&L (sells against real running BUY cost) ===")
print("sells_with_real_basis(>=partial)", matched_sell_count)
print("matched_proceeds_usd", round(m_matched_proceeds,2))
print("matched_realized_pnl", round(m_realized,2))
print("loss_cnt", m_loss_cnt, "loss_usd", round(m_loss_usd,2))
print("gain_cnt", m_gain_cnt, "gain_usd", round(m_gain_usd,2))
print("median_loss_pct", round(statistics.median(m_loss_pct)*100,2) if m_loss_pct else 0)
print("mean_loss_pct", round(statistics.mean(m_loss_pct)*100,2) if m_loss_pct else 0)
print()
print("=== EXCESS / NO-BASIS SELLS (cost basis unknown; likely from MERGE/SPLIT/CONVERSION-acquired shares) ===")
print("sells_with_any_excess", excess_size_sells)
print("pure_no_basis_sells", pure_excess_sells)
print("excess_proceeds_usd", round(excess_proceeds,2))

# unmatched leg using buy-size imbalance
def imbalance(cond):
    sizes = sorted(outcomes_by_cond_buysize[cond].values(), reverse=True)
    if not sizes or sizes[0] <= 1e-12: return None
    top = sizes[0]; second = sizes[1] if len(sizes)>1 else 0.0
    return (top - second)/top

su_un=su_ma=0.0; cu=cm=0
for r in sell_records:
    imb = imbalance(r["cond"]); usd = r["price"]*r["size"]
    if imb is not None and imb >= 0.5:
        su_un += usd; cu+=1
    else:
        su_ma += usd; cm+=1
print()
print("=== UNMATCHED LEG (buy-size imbalance >=50% between legs) ===")
print("sell_usd_in_unmatched", round(su_un,2), "cnt", cu)
print("sell_usd_in_matched", round(su_ma,2), "cnt", cm)
print("pct_sell_usd_in_unmatched", round(100*su_un/(su_un+su_ma),2))

# timing
pos_all = [r["pos"] for r in sell_records]
pos_loss = [r["pos"] for r in sell_records if r["is_loss"]]
print()
print("=== TIMING (0=first trade in market, 1=last) ===")
print("median_pos_all", round(statistics.median(pos_all),3))
print("mean_pos_all", round(statistics.mean(pos_all),3))
print("frac_all_in_last20pct", round(100*sum(1 for p in pos_all if p>=0.8)/len(pos_all),2))
print("median_pos_loss", round(statistics.median(pos_loss),3) if pos_loss else 0)
print("frac_loss_in_last20pct", round(100*sum(1 for p in pos_loss if p>=0.8)/len(pos_loss),2) if pos_loss else 0)
