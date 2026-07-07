import ijson, statistics, json
from decimal import Decimal

PATH = 'raw_activity_full.json'
CID = '0x221b05db581e0beb3bd140683b89b06f6bb565f67674fb7243c6d3789ee18b96'

# Collect all BUY TRADE fills for the two outcomes in this binary market.
# outcome 'Alexander Zverev' = favorite (we call YES), 'Flavio Cobolli' = underdog (NO).
fills = []  # (ts, outcome, price, size)
f = open(PATH, 'rb')
for obj in ijson.items(f, 'item'):
    if obj.get('conditionId') != CID:
        continue
    if obj.get('type') != 'TRADE':
        continue
    if obj.get('side') != 'BUY':
        continue
    ts = int(obj['timestamp'])
    price = float(obj['price'])
    size = float(obj['size'])
    outcome = obj.get('outcome')
    fills.append((ts, outcome, price, size))
f.close()

# chronological order (oldest first)
fills.sort(key=lambda r: r[0])

YES = 'Alexander Zverev'
NO  = 'Flavio Cobolli'

n_buy = len(fills)
yes_fills = [r for r in fills if r[1] == YES]
no_fills  = [r for r in fills if r[1] == NO]

print('total BUY fills:', n_buy)
print('YES (Zverev) fills:', len(yes_fills), ' NO (Cobolli) fills:', len(no_fills))

# --- 1 & 2: PAIR-COST INVARIANT + SPLIT FLOATS ---
# rolling current yes price and no price (last fill on each side).
# Only start tracking p+q once BOTH sides have at least one fill.
cur_yes = None
cur_no = None
pq_series = []  # (ts, p, q, p+q)
yes_price_series = []
no_price_series = []
for ts, outcome, price, size in fills:
    if outcome == YES:
        cur_yes = price
    elif outcome == NO:
        cur_no = price
    if cur_yes is not None and cur_no is not None:
        pq_series.append((ts, cur_yes, cur_no, cur_yes + cur_no))

sums = [x[3] for x in pq_series]
yps = [x[1] for x in pq_series]
nps = [x[2] for x in pq_series]

def stats(xs):
    return {
        'mean': round(statistics.mean(xs), 5),
        'std': round(statistics.pstdev(xs), 5),
        'min': round(min(xs), 5),
        'max': round(max(xs), 5),
        'n': len(xs),
    }

sum_stats = stats(sums)
yes_stats_roll = stats(yps)
no_stats_roll = stats(nps)

# raw fill price ranges (all fills, not just paired)
yes_prices_all = [r[2] for r in yes_fills]
no_prices_all = [r[2] for r in no_fills]

print('\n=== PAIR COST INVARIANT (rolling p+q over paired series) ===')
print('p+q:', sum_stats)
print('rolling YES price:', yes_stats_roll)
print('rolling NO  price:', no_stats_roll)
print('\nYES fill price range (all):', round(min(yes_prices_all),4), '-', round(max(yes_prices_all),4))
print('NO  fill price range (all):', round(min(no_prices_all),4), '-', round(max(no_prices_all),4))
print('sum_std =', sum_stats['std'], '  yes_side_std =', yes_stats_roll['std'], '  no_side_std =', no_stats_roll['std'])

# trimmed view excluding the final-minute collapse (prices going to ~0 / ~1 at settle)
# Use only fills before the match outcome became near-certain: keep where both 0.05<p<0.97 region
core = [(ts,p,q,s) for (ts,p,q,s) in pq_series if 0.03 < p < 0.98 and 0.03 < q < 0.98]
core_sums = [x[3] for x in core]
if core_sums:
    print('\n=== CORE (excluding settle collapse, 0.03<each<0.98) ===')
    print('p+q core:', stats(core_sums))
    print('YES core:', stats([x[1] for x in core]))
    print('NO  core:', stats([x[2] for x in core]))

# --- 3: INVENTORY SKEW ---
# running cumulative shares; track how deficient side bid moves.
cum_yes = 0.0
cum_no = 0.0
# We'll look at: for each fill, the imbalance BEFORE the fill, and whether the side filled
# is the deficient side, and the price relative to recent same-side price.
last_yes_price = None
last_no_price = None
skew_events = []  # records when imbalance is large and the deficient side's price moves up
# correlation: when YES inventory >> NO (need NO), does NO bid rise vs prior NO bid?
no_bid_when_need_no = []
no_bid_when_excess_no = []
yes_bid_when_need_yes = []
yes_bid_when_excess_yes = []

for ts, outcome, price, size in fills:
    imbalance = cum_yes - cum_no  # >0 means over-filled YES (need NO)
    if outcome == NO:
        if last_no_price is not None:
            delta = price - last_no_price
            if imbalance > 50:   # over-filled YES => deficient in NO => needs NO
                no_bid_when_need_no.append(delta)
            elif imbalance < -50:  # over-filled NO already
                no_bid_when_excess_no.append(delta)
        last_no_price = price
        cum_no += size
    else:  # YES
        if last_yes_price is not None:
            delta = price - last_yes_price
            if imbalance < -50:   # over-filled NO => deficient YES => needs YES
                yes_bid_when_need_yes.append(delta)
            elif imbalance > 50:
                yes_bid_when_excess_yes.append(delta)
        last_yes_price = price
        cum_yes += size

def avg(xs):
    return round(statistics.mean(xs),5) if xs else None

print('\n=== INVENTORY SKEW ===')
print('final cum YES shares:', round(cum_yes,2), ' final cum NO shares:', round(cum_no,2))
print('final imbalance (YES-NO):', round(cum_yes-cum_no,2))
print('NO bid delta when NEED NO (YES over-filled):  mean', avg(no_bid_when_need_no), ' n', len(no_bid_when_need_no))
print('NO bid delta when EXCESS NO:                  mean', avg(no_bid_when_excess_no), ' n', len(no_bid_when_excess_no))
print('YES bid delta when NEED YES (NO over-filled): mean', avg(yes_bid_when_need_yes), ' n', len(yes_bid_when_need_yes))
print('YES bid delta when EXCESS YES:                mean', avg(yes_bid_when_excess_yes), ' n', len(yes_bid_when_excess_yes))

# --- 4: RE-QUOTE CADENCE ---
ts_all = sorted(r[0] for r in fills)
gaps = [ts_all[i+1]-ts_all[i] for i in range(len(ts_all)-1)]
gaps_pos = [g for g in gaps if g >= 0]
print('\n=== RE-QUOTE CADENCE ===')
print('median inter-fill sec:', statistics.median(gaps_pos))
print('mean inter-fill sec:', round(statistics.mean(gaps_pos),2))
print('p90 gap:', sorted(gaps_pos)[int(0.9*len(gaps_pos))])
print('max gap:', max(gaps_pos))
# fast-move behaviour: look at periods where price moved a lot between consecutive paired points
big_moves = 0
for i in range(1,len(pq_series)):
    dp = abs(pq_series[i][1]-pq_series[i-1][1]) + abs(pq_series[i][2]-pq_series[i-1][2])
    dt = pq_series[i][0]-pq_series[i-1][0]
    if dp > 0.03:
        big_moves += 1
print('paired steps with >0.03 combined price move:', big_moves, 'of', len(pq_series)-1)

# --- 5: SIZE ---
yes_sizes = [r[3] for r in yes_fills]
no_sizes = [r[3] for r in no_fills]
print('\n=== SIZE ===')
print('YES (Zverev) median size:', round(statistics.median(yes_sizes),2), ' mean:', round(statistics.mean(yes_sizes),2), ' total:', round(sum(yes_sizes),2))
print('NO  (Cobolli) median size:', round(statistics.median(no_sizes),2), ' mean:', round(statistics.mean(no_sizes),2), ' total:', round(sum(no_sizes),2))

# Does he post more size on the side he needs (deficient)?
# Compare size when filling deficient side vs excess side
size_deficient = []
size_excess = []
cum_yes2=0.0; cum_no2=0.0
for ts, outcome, price, size in fills:
    imb = cum_yes2 - cum_no2
    if outcome==NO:
        if imb>50: size_deficient.append(size)   # needed NO
        elif imb<-50: size_excess.append(size)
        cum_no2+=size
    else:
        if imb<-50: size_deficient.append(size)   # needed YES
        elif imb>50: size_excess.append(size)
        cum_yes2+=size
print('median size on DEFICIENT side:', round(statistics.median(size_deficient),2) if size_deficient else None, 'n', len(size_deficient))
print('median size on EXCESS side:', round(statistics.median(size_excess),2) if size_excess else None, 'n', len(size_excess))

# time-bucketed look at how split tracks game: print every ~20th paired point
print('\n=== SAMPLE TRAJECTORY (yes, no, sum) every Nth paired point ===')
step = max(1, len(pq_series)//20)
for i in range(0, len(pq_series), step):
    ts,p,q,s = pq_series[i]
    print(ts, 'YES=%.3f'%p, 'NO=%.3f'%q, 'SUM=%.3f'%s)

# dump machine-readable
out = {
  'n_buy': n_buy,
  'n_yes': len(yes_fills),
  'n_no': len(no_fills),
  'sum_stats': sum_stats,
  'yes_roll': yes_stats_roll,
  'no_roll': no_stats_roll,
  'core_sum': stats(core_sums) if core_sums else None,
  'core_yes': stats([x[1] for x in core]) if core else None,
  'core_no': stats([x[2] for x in core]) if core else None,
  'yes_price_range': [round(min(yes_prices_all),4), round(max(yes_prices_all),4)],
  'no_price_range': [round(min(no_prices_all),4), round(max(no_prices_all),4)],
  'median_gap': statistics.median(gaps_pos),
  'yes_median_size': round(statistics.median(yes_sizes),2),
  'no_median_size': round(statistics.median(no_sizes),2),
  'final_imbalance': round(cum_yes-cum_no,2),
  'no_delta_need_no': avg(no_bid_when_need_no),
  'no_delta_excess_no': avg(no_bid_when_excess_no),
  'yes_delta_need_yes': avg(yes_bid_when_need_yes),
  'yes_delta_excess_yes': avg(yes_bid_when_excess_yes),
  'size_deficient_med': round(statistics.median(size_deficient),2) if size_deficient else None,
  'size_excess_med': round(statistics.median(size_excess),2) if size_excess else None,
}
json.dump(out, open('maker_quote_out.json','w'), indent=2)
print('\nwrote maker_quote_out.json')
