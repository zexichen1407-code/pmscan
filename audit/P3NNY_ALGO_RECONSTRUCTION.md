# 0xp3nny / e46m3 algorithm reconstruction

Wallet: `0x4f1d5ae26fc31472966e951af3183308736d8de2`

This is a reconstruction, not a claim that we know his private code. I label
each rule with confidence:

- **High**: official docs or direct chain/activity evidence.
- **Medium**: strongly implied by several measurements, but not directly visible
  because we cannot see unfilled orders, cancellations, or private risk limits.
- **Low**: plausible implementation detail; use only as a working hypothesis.

## Executive model

He appears to run two related machines:

1. **Neg-risk NO basket machine**: buy NO legs in multi-outcome neg-risk events,
   then use `convertPositions` / merges to recover collateral quickly.
2. **Binary YES/NO merge machine**: buy both sides of a two-outcome market when
   the pair can be merged back to $1 for a small spread.

The robust part of the strategy is not "predicting winners". It is fast,
small-edge inventory conversion: buy positions whose combined all-in cost is
below the contractually recoverable value, then convert/merge quickly and reuse
capital. The fragile part is residual inventory from missing legs or failed
pairs; that part is directional risk, not pure arbitrage.

## Hard facts that anchor the model

| Claim | Confidence | Evidence |
|---|---|---|
| Taker fees exist and must be deducted. | High | Official fee formula: `fee = shares * feeRate * p * (1-p)`. Local receipt reconstruction shows pUSD paid exceeds `shares * quote` even when `OrderFilled.fee == 0`; see `FEE_VERIFICATION.md` and `howiknow_efffee.py`. |
| A NO in a neg-risk market can be converted into YES for all other outcomes. | High | Official neg-risk docs and `NegRiskAdapter.sol`. The adapter comment says a set of NO positions converts to complementary YES positions plus collateral proportional to `#NO positions - 1`. |
| He is mostly, but not always, taker. | High | Four chain-decoded samples: 669 taker / 130 maker = 83.73% taker. Files: `sample3mo_101/202/303/404.json`. |
| Multi-leg fills are concurrent independent txs, not one atomic basket tx. | High | `_burst_probe.py`: 2,654 `(event, second)` buckets touched >=2 distinct NO legs; 0 shared a single tx; 0/15,011 txs contained >1 distinct leg. |
| He cannot guarantee the whole basket fills atomically onchain. | High | Same chain evidence plus official order docs: FOK/FAK apply to individual orders; batch orders process multiple orders, but no documented cross-market basket atomicity. |
| Neg-risk conversion is very fast after fills. | High | `_verify_indep.py`: 2,028 neg-risk events; first NO buy -> first conversion median 30s; 89.4% within 120s; inter-conversion median 9s; capital-weighted consumed holding median 26s. |
| He frequently uses partial/subset conversion, not just full N-leg conversion. | High | `conv_mechanic_probe.py`: 1,156/2,028 events have conversions exceeding the thinnest leg; by conversion volume at least 61% is definitely subset conversion. |
| Single fills are small. | High | `negrisk_trades.pkl`: 15,123 BUY-NO fills; median $11.83, p75 $37.18, p95 $199.84, max $1,406.38. |
| Selling is not the main exit. | High | Raw activity counts: 51,406 conversions + 46,104 merges vs 2,963 sells. |
| Exact stop-loss thresholds are not observable. | High | We see fills and settlement actions, not unfilled orders, cancels, private order-book snapshots, or account-level risk knobs. |

## Fee-aware edge formula

For a full N-leg neg-risk basket, the taker all-in edge per one share of each
NO leg is:

```text
gross_edge = (N - 1) - sum(no_price_i)
fee_cost   = sum(feeRate_i * no_price_i * (1 - no_price_i))
net_edge   = gross_edge - fee_cost
```

At p=0.50, the effective fee as percent of dollars paid is `feeRate * 0.5`:
Sports 1.5%, Politics/Finance/Tech 2.0%, Weather/Culture/Other 2.5%,
Crypto 3.5%, Geopolitics 0%.

Implication: the bot can only taker-sweep a clean basket when:

```text
sum(no_price_i + feeRate_i * no_price_i * (1 - no_price_i))
  < (N - 1) - target_profit_buffer - slippage_buffer
```

Local snapshot evidence from `edge_vs_fee2.py` is sobering: among 20 bought-full
baskets in the local June 29 snapshot, median gross edge was 0.69c per set,
median official fee was 3.15c per set, and only 9/20 remained positive after
official taker fees. That script's "chain actual fee %" column is not used here,
because it reads the `OrderFilled.fee` field and therefore undercounts BUY fees.

## Reconstructed neg-risk algorithm

### 1. Market scan

Evidence level: **High for the opportunity formula, Medium for exact thresholds.**

For every open `negRisk=true` event:

1. Pull all child markets and identify the NO token for each outcome.
2. Pull order books / midpoints / recent fills.
3. For each NO leg, compute the all-in taker cost curve:

```text
all_in_price(q) = vwap_no_ask(q) + feeRate * vwap_no_ask(q) * (1 - vwap_no_ask(q))
```

4. Compute the maximum fillable common set size:

```text
q_full = min(cumulative_available_shares_i at acceptable price)
```

5. Trigger only if:

```text
net_edge(q_full) > min_edge_abs
net_edge(q_full) / capital_used > min_edge_pct
q_full >= min_clip_set
```

Observed first-entry gross thresholds in older reverse-engineered samples were
very thin: min +0.0019/set, mean roughly +0.022/set. After current taker fees,
such a thin threshold only works if fees are zero, category is fee-free, the
trade is maker/rebated, or it is inventory repair rather than a fresh taker arb.

### 2. Order dispatch

Evidence level: **High for concurrency / non-atomic; Medium for FOK/FAK.**

The live implementation is likely:

```text
for each selected NO leg:
    create a marketable BUY limit order at max acceptable price
submit orders concurrently or with postMultipleOrders
use FOK if full per-leg size is mandatory
use FAK if partial immediate fill is acceptable
listen to user websocket for actual fills
cancel or ignore unfilled remainder
```

Why this shape:

- Chain shows independent concurrent txs, not basket multicall.
- Official docs say FOK fills entirely or cancels; FAK fills immediately and
  cancels the rest; batch order posting processes multiple orders in parallel.
- We cannot prove his private order type from chain. The only proven fact is
  independent concurrent execution.

### 3. Sizing

Evidence level: **High for small clips; Medium for target sizing rule.**

He sizes in shares / sets, not equal dollars. Cheap NO legs need more shares to
build the same number of convertible sets. Practical sizing:

```text
target_sets = min(
    per_leg_available_size_at_edge,
    capital_limit / all_in_cost_per_set,
    event_risk_limit,
)

per_leg_order_size_i = target_sets - current_no_balance_i
```

Observed fill size distribution supports a small-clip machine:

```text
median $11.83, p75 $37.18, p95 $199.84, only ~0.06% >= $1000
```

For a simulator, use small repeated orders rather than one large sweep. That
matches the observed fill tape and reduces visible footprint.

### 4. Convert / merge loop

Evidence level: **High for rapid conversion and subset conversion; Medium for
subset selection objective.**

After every fill batch:

```text
balances = current NO balances by outcome

q_full = min(balances over all N outcomes)
if q_full >= conv_batch:
    convertPositions(marketId, indexSet=all_outcomes, amount=q_full)
    update balances

for subsets S with at least 2 NO legs:
    q = min(balances over S)
    cash = (len(S) - 1) * q
    minted_yes = q on every outcome not in S
    immediate_value = cash + mergeable_value(minted_yes, existing_NO_on_complements)
    cost_basis = FIFO_cost_of_NO_in_S(q)
    if immediate_value - cost_basis > subset_edge_floor:
        convertPositions(marketId, indexSet=S, amount=q)
        then merge any newly paired YES/NO
```

The exact subset selector is not visible, but subset conversion itself is
proven. In the full dataset, conversions often exceed the thinnest NO leg,
which is mathematically impossible for pure full-set conversion.

### 5. Re-entry

Evidence level: **High.**

The bot does not do one pass per event. It re-enters when the spread reappears:

```text
while event open:
    recompute all-in edge after each book/fill update
    if edge is positive and risk budget remains:
        run another dispatch batch
    after fills:
        convert/merge immediately
```

Evidence: in the 7-event neg-risk sample, Fed had 89 sessions, Colombia 99,
NBA 76. Conversion timing across 2,028 events is seconds-to-minutes, not days.

## Reconstructed binary YES/NO merge algorithm

Evidence level: **Medium to High.**

For ordinary two-sided markets:

```text
if yes_ask + no_ask + fees < 1 - min_edge:
    buy YES and NO in small clips
    when min(yes_balance, no_balance) >= merge_batch:
        merge positions to $1
```

Measured from `twosided_paired.py` / `twosided_metrics.py`:

- 669 markets with matched sets.
- Per-market matched p+q median roughly 0.998; edge median roughly 0.2-0.3c.
- 42-43% of markets had negative matched edge, meaning many merges are inventory
  repair or failed quote captures, not clean profit.
- Fill cadence median across markets about 44s; same-block simultaneous fills
  exist but are not the dominant proof.

This suggests a market-making style: quote both sides around a target pair cost,
merge pairs when possible, and accept some negative-edge pairs to reduce
inventory.

## Stop-loss / failure handling

Evidence level: **Medium for behavior ranking; Low for thresholds.**

Observed exit ranking:

1. Convert/merge quickly when possible.
2. If a pair/basket is stuck, sometimes buy the missing expensive leg and merge
   at a loss. This is real but concentrated in a small number of markets.
3. Sell residuals mainly as tail cleanup, not as the primary stop.
4. Some residual inventory is carried to resolution; true net of that bucket is
   hard to infer because losing tokens disappear economically.

So the most faithful stop-loss model is not a clean "stop at -x%". It is:

```text
if missing_leg_can_complete_at_loss <= loss_budget:
    buy missing leg and merge/convert
elif residual_bid_is_good_enough:
    sell residual tail
elif expected_settlement_value > liquidation_value:
    hold residual to resolution
else:
    reduce new quoting and wait for re-pair opportunity
```

Concrete thresholds are **low confidence**. We do not see unfilled orders,
cancels, private PnL limits, or real-time book states at decision time.

## Why he can appear to coordinate many takers

Strictly, he does not guarantee a whole cross-leg basket fills atomically. The
working mechanism is:

```text
parallel independent marketable orders
small clip size
order types that immediately fill-or-cancel per leg
websocket-driven fill accounting
immediate convert/merge of whatever became safe
residual inventory management for whatever failed
```

This gives the appearance of simultaneous taker execution. Chain evidence proves
parallelism; it disproves a single atomic multi-leg tx.

## Dry-run script shape

A faithful simulator should run in this order:

```text
1. load open neg-risk events from Gamma
2. load CLOB order books for all NO tokens
3. compute all-in fee-adjusted full-basket edge curves
4. choose target set size from min leg depth and risk budget
5. submit concurrent marketable BUY orders in dry-run mode
6. update simulated fills from actual available book depth
7. run full-set and subset convert selection
8. merge newly paired YES/NO
9. classify leftovers: retry, repair-buy, sell tail, or hold
10. log every decision with gross edge, fee, net edge, confidence, and evidence
```

Do not simulate profit from incomplete baskets as if it were locked. A partial
conversion without valuing the complementary YES / residual inventory is not a
closed trade.

## Bottom line

The high-confidence algorithm is:

```text
scan tiny all-in spreads
send many small independent taker/maker orders concurrently
convert/merge within seconds
reuse capital
repair or carry residual inventory when a leg fails
```

What is not proven:

- his exact private thresholds;
- whether a specific live order was FOK or FAK;
- exact PnL of partial conversions without full multi-wallet inventory tracing;
- exact stop-loss rules.

The economic edge is thin. After taker fees, many apparently positive gross
edges disappear. The durable advantage is execution speed, inventory accounting,
possible maker/rebate economics, and the ability to recycle capital through
conversion/merge faster than manual traders.

## Sources and local evidence

Official:

- Fees: https://docs.polymarket.com/trading/fees.md
- Negative risk: https://docs.polymarket.com/advanced/neg-risk.md
- Order types: https://docs.polymarket.com/trading/orders/overview.md
- Create orders / FOK / FAK / batch: https://docs.polymarket.com/trading/orders/create.md
- Data API trades: https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets.md
- Adapter source: https://github.com/Polymarket/neg-risk-ctf-adapter

Local scripts:

- `C:\Users\zexi\pmscan\audit\howiknow_efffee.py`
- `C:\Users\zexi\pmscan\audit\_burst_probe.py`
- `C:\Users\zexi\pmscan\audit\_burst_probe2.py`
- `C:\Users\zexi\pmscan\audit\conv_mechanic_probe.py`
- `C:\Users\zexi\pmscan\audit\_verify_indep.py`
- `C:\Users\zexi\pmscan\audit\edge_vs_fee2.py`
- `C:\Users\zexi\pmscan\audit\twosided_paired.py`
- `C:\Users\zexi\pmscan\audit\twosided_metrics.py`
