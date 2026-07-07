# Cross-check the nonzero-fee legs: is SUBJECT the taker or the maker on them?
# And compute fee / notional (fee bps).
# Polymarket convention: for a BUY (taker buys YES/NO token with USDC),
#   makerAmountFilled / takerAmountFilled tells the price; fee charged in collateral.

legs = [
  # (slug, p, leg, maker_is_subj, taker_is_subj, makerAmt, takerAmt, fee)
  ("beijing-28c", 0.78, "A user-order", False, True,  1.106600, 5.030000, 0.0),
  ("beijing-28c", 0.78, "B exch-leg",   True,  False, 3.923400, 5.030000, 0.043150),
  ("beijing-29c", 0.53, "A user-order", False, True,  2.350000, 5.000000, 0.0),
  ("beijing-29c", 0.53, "B exch-leg",   True,  False, 2.650000, 5.000000, 0.062270),
  ("beijing-31c", 0.974,"A user-order", False, True,  0.130780, 5.030000, 0.0),
  ("beijing-31c", 0.974,"B exch-leg",   True,  False, 4.899220, 5.030000, 0.006360),
]
print(f"{'slug':14}{'p':6}{'leg':14}{'subjRole':10}{'mkrAmt':>10}{'tkrAmt':>10}{'fee':>10}{'notional':>10}{'feeBps':>9}{'fee/p(1-p)Bps':>14}")
for slug,p,leg,mksubj,tksubj,mk,tk,fee in legs:
    role = "TAKER" if tksubj else ("MAKER" if mksubj else "?")
    # token shares = the side that's NOT usdc. For NO buy, shares = takerAmt(5.03) in token, makerAmt=usdc paid OR vice versa.
    # The collateral notional ~ the smaller-priced usd value. shares=5.03. notional = shares * p.
    shares = max(mk,tk)
    notional = shares * p
    feebps = (fee/notional*10000) if notional else 0
    # fee as % of the 'fair' fee base shares*p*(1-p)
    base = shares*p*(1-p)
    fee_over_base = (fee/base*10000) if base else 0
    print(f"{slug:14}{p:<6}{leg:14}{role:10}{mk:>10.4f}{tk:>10.4f}{fee:>10.5f}{notional:>10.4f}{feebps:>9.2f}{fee_over_base:>14.4f}")

print()
print("Interpretation:")
print(" - The leg where SUBJECT=TAKER has fee=0.")
print(" - The leg where SUBJECT=MAKER (taker=NegRiskExchange itself) carries the nonzero fee.")
