import ijson
import statistics
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

events = defaultdict(list)
with open(PATH, "rb") as f:
    for obj in ijson.items(f, "item"):
        if obj.get("type") != "TRADE":
            continue
        side = obj.get("side")
        if side not in ("BUY", "SELL"):
            continue
        key = (obj.get("conditionId"), obj.get("outcome"))
        events[key].append((int(obj.get("timestamp") or 0), side,
                            float(obj.get("price") or 0), float(obj.get("size") or 0)))

# Diagnostics:
# 1. How many sells have NO prior buys at sell time (oversell / cost basis = 0)?
# 2. Distribution of realized pnl on gain sells - any huge outliers?
oversell_count = 0
oversell_proceeds = 0.0
gain_pnls = []
loss_pnls = []
sells_with_cost = 0
# also: top gain sells
top_gains = []

for key, evs in events.items():
    evs.sort(key=lambda x: x[0])
    run_size = 0.0
    run_cost = 0.0
    for (ts, side, price, size) in evs:
        if side == "BUY":
            run_size += size
            run_cost += price * size
        else:  # SELL
            if run_size > 1e-12:
                avg = run_cost / run_size
                sells_with_cost += 1
            else:
                avg = 0.0
                oversell_count += 1
                oversell_proceeds += price * size
            realized = (price - avg) * size
            reduce = min(size, run_size)
            if run_size > 1e-12:
                run_cost -= avg * reduce
                run_size -= reduce
            if price > avg + 1e-9:
                gain_pnls.append(realized)
                top_gains.append((realized, key, price, avg, size))
            elif price < avg - 1e-9:
                loss_pnls.append(-realized)

print("sells_with_prior_buys", sells_with_cost)
print("oversell_count(no prior buys)", oversell_count, "proceeds", round(oversell_proceeds,2))
print("gain pnl sum", round(sum(gain_pnls),2), "n", len(gain_pnls))
print("loss pnl sum", round(sum(loss_pnls),2), "n", len(loss_pnls))
if gain_pnls:
    print("gain pnl: max", round(max(gain_pnls),2), "median", round(statistics.median(gain_pnls),4), "mean", round(statistics.mean(gain_pnls),4))
top_gains.sort(reverse=True)
print("TOP 10 GAIN SELLS (realized, price, avgcost, size):")
for g in top_gains[:10]:
    print("  ", round(g[0],2), "px=", g[2], "cost=", round(g[3],4), "size=", round(g[4],2), g[1][1])

# Sell USD vs Buy USD overall to gauge how small selling is
total_buy_usd = 0.0
total_sell_usd = 0.0
for key, evs in events.items():
    for (ts, side, price, size) in evs:
        if side == "BUY":
            total_buy_usd += price*size
        else:
            total_sell_usd += price*size
print("total_buy_usd", round(total_buy_usd,2), "total_sell_usd", round(total_sell_usd,2))
print("sell_usd as pct of buy_usd", round(100*total_sell_usd/total_buy_usd,3))
