ONE=1_000_000; BPS=10000
# side enum: 0=BUY,1=SELL (Polymarket Side). word[0] was 0 on ALL legs we saw -> all logged as BUY? 
# Actually each OrderFilled logs the *order's own* side. Let's just brute the formula variants.
def price_buy(mk,tk): return mk*ONE//tk
def price_sell(mk,tk): return tk*ONE//mk

legs = [
 ("28c",3923400,5030000,43150),
 ("29c",2650000,5000000,62270),
 ("31c",4899220,5030000,6360),
]
def fee_buy(bps,mk,tk):
    p=price_buy(mk,tk); mn=min(p,ONE-p); return (bps*mn*tk)//(p*BPS), p
def fee_sell(bps,mk,tk):
    p=price_sell(mk,tk); mn=min(p,ONE-p); return (bps*mn*mk)//(BPS*ONE), p
def fee_sell_tk(bps,mk,tk):
    p=price_sell(mk,tk); mn=min(p,ONE-p); return (bps*mn*tk)//(BPS*ONE), p

for slug,mk,tk,onfee in legs:
    print("==",slug,"onchainFee",onfee)
    for name,fn in [("BUY(out=tk)",fee_buy),("SELL(out=mk)",fee_sell),("SELL(out=tk)",fee_sell_tk)]:
        for bps in (500,):
            f,p=fn(bps,mk,tk)
            # solve implied bps
            impl = onfee/f*bps if f else 0
            print(f"   {name:14} p={p/ONE:.4f} calc@{bps}={f}  impliedBps={impl:.1f}")
