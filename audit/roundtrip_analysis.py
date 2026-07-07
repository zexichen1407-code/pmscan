import pickle
import statistics

with open(r"C:\Users\zexi\pmscan\audit\cond_agg.pkl", "rb") as f:
    cond = pickle.load(f)

# ===== Q1: ACTIVE vs HOLD-TO-EXPIRY =====
total_merge = sum(len(c["merge_ts"]) for c in cond.values())
total_conv = sum(len(c["conv_ts"]) for c in cond.values())
total_redeem = sum(len(c["redeem_ts"]) for c in cond.values())
active = total_merge + total_conv
active_pct = active / (active + total_redeem) * 100
print("=== Q1 ACTIVE vs REDEEM ===")
print(f"MERGE ops      : {total_merge}")
print(f"CONVERSION ops : {total_conv}")
print(f"active total   : {active}")
print(f"REDEEM ops     : {total_redeem}")
print(f"active_exit_pct: {active_pct:.3f}%")
print()

# ===== Q2: ROUND-TRIP SPEED =====
# Method: per conditionId, the trader BUYs to build a position, then MERGE/CONVERSION
# closes it. Round-trip = time between the BUY activity and the close.
# We use, per condition, the FIRST close event (min of merge+conv ts) and the
# bulk-of-buys timestamp = MEDIAN of buy timestamps before that close.
# Caveat: a condition can have many cycles; this captures the first cycle's duration.
# We also compute an alternative: median(close) - median(buy) over the whole condition.

rt_first = []   # first-close minus median-buy-before-first-close
rt_median = []  # median(all closes) - median(all buys)
rt_lastbuy = [] # first close - last buy before that close (tightest)

for cid, c in cond.items():
    buys = sorted(t for t, u in c["buy_ts"])
    closes = sorted(c["merge_ts"] + c["conv_ts"])
    if not buys or not closes:
        continue
    first_close = closes[0]
    buys_before = [b for b in buys if b <= first_close]
    if buys_before:
        med_buy = statistics.median(buys_before)
        rt_first.append((first_close - med_buy) / 60.0)
        rt_lastbuy.append((first_close - buys_before[-1]) / 60.0)
    # whole-condition median approach
    med_buy_all = statistics.median(buys)
    med_close_all = statistics.median(closes)
    rt_median.append((med_close_all - med_buy_all) / 60.0)

def pct(data, p):
    data = sorted(data)
    if not data:
        return 0
    k = (len(data) - 1) * p
    f = int(k)
    cdiff = k - f
    if f + 1 < len(data):
        return data[f] + (data[f+1] - data[f]) * cdiff
    return data[f]

print("=== Q2 ROUND-TRIP (minutes) ===")
for label, data in [("first_close - median_buy", rt_first),
                    ("first_close - last_buy_before", rt_lastbuy),
                    ("median_close - median_buy", rt_median)]:
    if data:
        # clip negatives to understand
        neg = sum(1 for x in data if x < 0)
        print(f"\n[{label}] n={len(data)} (neg={neg})")
        print(f"  median = {statistics.median(data):.2f} min")
        print(f"  p25    = {pct(data,0.25):.2f} min")
        print(f"  p75    = {pct(data,0.75):.2f} min")
        print(f"  p10    = {pct(data,0.10):.2f} min")
        print(f"  p90    = {pct(data,0.90):.2f} min")
        # also in hours/days for context
        m = statistics.median(data)
        print(f"  median = {m/60:.2f} hours = {m/1440:.3f} days")

# Distribution buckets for the primary metric (first_close - median_buy)
print("\n=== Q2 distribution of round-trip (first_close - median_buy) ===")
buckets = [("<1 min",0,1),("1-5 min",1,5),("5-30 min",5,30),("30-60 min",30,60),
           ("1-6 hr",60,360),("6-24 hr",360,1440),("1-3 day",1440,4320),(">3 day",4320,1e18)]
n_pos = [x for x in rt_first if x >= 0]
for label, lo, hi in buckets:
    cnt = sum(1 for x in n_pos if lo <= x < hi)
    print(f"  {label:10s}: {cnt:6d} ({cnt/len(n_pos)*100:5.1f}%)")

# ===== Q3: maker/taker already from pass 1 (printed there) =====

# ===== Q4: TIMING WITHIN MARKET LIFE - sample big multi-day events =====
# Big = many trades + spread over multiple days (max ts - min ts large)
print("\n=== Q4 BIG MULTI-DAY CONDITIONS (sample) ===")
rows = []
for cid, c in cond.items():
    allts = [t for t,u in c["buy_ts"]] + [t for t,u in c["sell_ts"]] + c["merge_ts"] + c["conv_ts"] + c["redeem_ts"]
    if not allts:
        continue
    span = max(allts) - min(allts)
    n_events = len(allts)
    closes = c["merge_ts"] + c["conv_ts"]
    rows.append((span, n_events, cid, c["title"], c["eventSlug"], c, closes))

# sort by span desc, take ones with decent event count
rows.sort(key=lambda r: -r[0])
shown = 0
for span, n_events, cid, title, eslug, c, closes in rows:
    if n_events < 50:
        continue
    buys = sorted(t for t,u in c["buy_ts"])
    if not buys or not closes:
        continue
    life_start = min(buys + closes)
    life_end = max(buys + closes)
    life = life_end - life_start
    if life <= 0:
        continue
    # where in the life do closes fall? fraction 0=start 1=end
    close_fracs = [ (t - life_start)/life for t in sorted(closes) ]
    med_frac = statistics.median(close_fracs)
    print(f"\nTITLE: {title[:60]}")
    print(f"  event={eslug[:50]}")
    print(f"  span_days={span/86400:.1f}  buys={len(buys)} closes={len(closes)}")
    print(f"  close-position-in-life: p25={pct(close_fracs,0.25):.2f} median={med_frac:.2f} p75={pct(close_fracs,0.75):.2f}  (0=open,1=last activity)")
    shown += 1
    if shown >= 8:
        break
