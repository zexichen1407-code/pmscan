# Reproduce on-chain fee with the source formula for the SUBJECT's taker-order legs.
# These are the legs where taker field = 0xe2222 (exchange) => takerOrder.maker = SUBJECT (the aggressor).
# OrderFilled data words: [side, tokenId, makerAmountFilled, takerAmountFilled, fee, builder, metadata]
# He's BUYING NO. For a BUY order: makerAmount=USDC paid (in), takerAmount=tokens received (out)?
# From decode: word2=makerAmountFilled, word3=takerAmountFilled.
# price = makerAmount*1 / takerAmount (BUY).  outcomeTokens = the token amount.
ONE=1_000_000  # 1e6 fixed point (USDC 6 decimals)
BPS=10000
def calc_fee(feeRateBps, makerAmt, takerAmt):
    # BUY: price = makerAmt/takerAmt ; outcomeTokens = takerAmt (tokens received)
    price = makerAmt*ONE//takerAmt
    mn = min(price, ONE-price)
    fee = (feeRateBps * mn * takerAmt) // (price * BPS)
    return fee, price

# from on-chain decode (raw integer 1e6 units):
legs = [
 ("beijing-28c", 3923400, 5030000, 43150),
 ("beijing-29c", 2650000, 5000000, 62270),
 ("beijing-31c", 4899220, 5030000, 6360),
]
print(f"{'slug':14}{'mkrAmt':>10}{'tkrAmt':>10}{'price':>9}{'onchainFee':>12}{'calc@500':>10}{'match?':>8}")
for slug,mk,tk,onfee in legs:
    fee500,price=calc_fee(500,mk,tk)
    print(f"{slug:14}{mk:>10}{tk:>10}{price/ONE:>9.4f}{onfee:>12}{fee500:>10}{str(fee500==onfee):>8}")
    # also solve implied feeRateBps
    # onfee = bps*min*tk/(price*BPS) -> bps = onfee*price*BPS/(min*tk)
    mn=min(price,ONE-price)
    if mn*tk:
        impl = onfee*price*BPS/(mn*tk)
        print(f"   implied feeRateBps = {impl:.2f}")
