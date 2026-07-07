# Polymarket fee verification, corrected

This file supersedes the earlier local note that inferred "neg-risk taker fee
is approximately zero" from `OrderFilled.fee == 0`. That inference was wrong.

## Correct facts

- Polymarket's current documented trading fee formula is:

```text
fee = shares * feeRate * price * (1 - price)
```

- Makers are not charged. Takers pay the category fee when the market has fees
  enabled. Geopolitics / world events are documented as fee-free.
- For a BUY taker leg, the local `OrderFilled.fee` field can still be zero while
  the taker pays more pUSD than `shares * quoted_price`. The fee is visible by
  reconstructing pUSD paid versus shares received from the receipt transfers.

## Chain evidence

Script:

```powershell
$env:PYTHONIOENCODING='utf-8'
python C:\Users\zexi\pmscan\audit\howiknow_efffee.py
```

Representative output from 2026-06-30:

```text
tx 0xad1543692d95d6e5... quote 0.530
paid pUSD = 2.712, shares received = 5.000
effective price = 0.5425, effective/quote = 1.0235x
OrderFilled.fee field = 0
```

For a 5-share weather leg at p=0.53, the documented fee is:

```text
5 * 0.05 * 0.53 * 0.47 = 0.062275
```

That matches the excess pUSD paid:

```text
2.712275 - 5 * 0.53 = 0.062275
```

Therefore the correct operational rule is:

```text
net_edge_per_set = (N - 1) - sum(price_i + feeRate_i * price_i * (1 - price_i))
```

Use this all-in cost for taker simulations. Do not use `OrderFilled.fee == 0`
as proof of zero taker fee.

## Useful effective fee at p = 0.50

The fee as a percent of notional paid (`shares * price`) is:

```text
effective_fee_pct_of_paid = feeRate * (1 - price)
```

At p=0.50:

| Category | feeRate | fee as % of paid |
|---|---:|---:|
| Geopolitics / world events | 0.00 | 0.0% |
| Sports | 0.03 | 1.5% |
| Politics / Finance / Tech / Mentions | 0.04 | 2.0% |
| Economics / Weather / Culture / Other | 0.05 | 2.5% |
| Crypto | 0.07 | 3.5% |

Sources:

- Official fees: https://docs.polymarket.com/trading/fees.md
- Local chain reconstruction: `C:\Users\zexi\pmscan\audit\howiknow_efffee.py`
