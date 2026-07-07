import json
from taskD_probe import get_receipt, decode_orderfilled_logs, words, h2i
from taskD_main import trades, NEGRISK_EX

# Deep-verify the REFUTING candidates: Ethiopia PM (SELL), Wimbledon (SELL), MLB Henderson (SELL).
# For each: full leg dump + match against data-api trade record (taker wallet, price, size, side)
# + reverse fee rate. fee is in USDC 6-decimals (collateral). takAmt/makAmt in 6-dec too.

CASES = {
 'ethiopia-pm-shimelis':('0xf426c69674c6637f2b458c76d9920faa814d4daa8c308e0b8ba554dda509cb8b','SELL'),
 'wimbledon-siegemund':('0x0571d51f994d898d0eff4f38175924e93038427e697a7dff6d33b0470afe4310','SELL'),
 'mlb-henderson-runs':('0x3b90e3c4776e73f25d33f745a0324e7d27e5c0efc111c1284a0faf4db664c1d9','SELL'),
}

for name,(cid,side) in CASES.items():
    print('================',name,side,'================')
    tr=trades(cid,limit=200)
    picks=[t for t in tr if t.get('side')==side][:3]
    for t in picks:
        txh=t['transactionHash']
        tw=(t.get('proxyWallet') or '').lower()
        print('-- tx',txh)
        print('   data-api: taker_wallet=%s side=%s price=%s size=%s outcome=%s' % (
            tw, t.get('side'), t.get('price'), t.get('size'), t.get('outcome')))
        r=get_receipt(txh)
        ofs=decode_orderfilled_logs(r, want_ex=NEGRISK_EX)
        for o in ofs:
            mk=o['maker'].lower(); tk=o['taker'].lower()
            who_t='AGG(exchange)' if tk==NEGRISK_EX.lower() else ('TAKER==tradewallet' if tk==tw else 'taker=other')
            who_m='maker==tradewallet' if mk==tw else 'maker=other'
            mak=o['makerAmountFilled']; tak=o['takerAmountFilled']; fee=o['fee']
            # fee rate vs the cash leg. For a SELL, taker gives tokens, gets USDC.
            # Identify cash side: makerAssetId==0 means maker gives USDC.
            line='   leg maker=%s[%s] taker=%s[%s] makAmt=%d takAmt=%d FEE=%d' % (
                o['maker'][:10], who_m, o['taker'][:10], who_t, mak, tak, fee)
            if fee>0:
                # reverse rate against both possible notionals
                rr_tak = fee/tak if tak else 0
                rr_mak = fee/mak if mak else 0
                line += '  | fee/takAmt=%.4f fee/makAmt=%.4f' % (rr_tak, rr_mak)
            print(line)
        print()
