import json
from taskD_probe import get_receipt, decode_orderfilled_logs
from taskD_main import trades, NEGRISK_EX

# Compute fee rate against the correct base. Polymarket fee formula (binary symmetric):
#   fee = baseRate * min(price, 1-price) * shares    (charged in the token you END UP with;
#   for SELL fee is charged in USDC proceeds; for BUY in shares).
# We reverse: rate = fee / (min(p,1-p) * shares_cash_equiv). Just report multiple bases so the
# reader can see which clean rate (0.03/0.05/0.07) it hits.

def words_for(name,cid,side,n=2):
    tr=trades(cid,limit=200)
    picks=[t for t in tr if t.get('side')==side][:n]
    print('====',name,side)
    for t in picks:
        txh=t['transactionHash']; tw=(t.get('proxyWallet') or '').lower()
        price=float(t.get('price') or 0); size=float(t.get('size') or 0); outcome=t.get('outcome')
        r=get_receipt(txh); ofs=decode_orderfilled_logs(r, want_ex=NEGRISK_EX)
        # total fee on legs where REAL taker == trade wallet (not agg)
        taker_fee=sum(o['fee'] for o in ofs if o['taker'].lower()==tw)
        agg_fee=sum(o['fee'] for o in ofs if o['taker'].lower()==NEGRISK_EX.lower())
        # proceeds estimate: shares * price (USDC). min(p,1-p)
        mp=min(price,1-price) if price else 0
        proceeds=size*price
        notional_minp=size*mp
        print(' tx',txh[:14],'p=%.4f sz=%.1f out=%s'%(price,size,outcome))
        print('   taker_fee=$%.6f  agg_fee=$%.6f'%(taker_fee/1e6,agg_fee/1e6))
        if proceeds: print('   taker_fee/proceeds=%.4f'%(taker_fee/1e6/proceeds))
        if notional_minp: print('   taker_fee/(size*min(p,1-p))=%.4f'%(taker_fee/1e6/notional_minp))
        if agg_fee and notional_minp: print('   agg_fee/(size*min(p,1-p))=%.4f'%(agg_fee/1e6/notional_minp))

words_for('ethiopia-pm','0xf426c69674c6637f2b458c76d9920faa814d4daa8c308e0b8ba554dda509cb8b','SELL')
words_for('wimbledon','0x0571d51f994d898d0eff4f38175924e93038427e697a7dff6d33b0470afe4310','SELL')
words_for('mlb-henderson','0x3b90e3c4776e73f25d33f745a0324e7d27e5c0efc111c1284a0faf4db664c1d9','SELL',n=4)
# also a clean p=0.5-ish neg-risk SELL if any: check fifa canada SELL (those had agg fees only)
words_for('fifa-canada','0x67443cb1ffb2bf180f7df5b6ca7adff63f7e8e933c7e41405ba118f3e9f8befb','SELL')
