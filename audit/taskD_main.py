import urllib.request, json, ssl, time, sys
from taskD_probe import jget, get_receipt, decode_orderfilled_logs

ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
UA = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
NEGRISK_EX='0xe2222d279d744050d28e00520010520000310f59'

def trades(cid, limit=200, takerOnly=False, side=None):
    url=f'https://data-api.polymarket.com/trades?market={cid}&limit={limit}'
    if takerOnly: url+='&takerOnly=true'
    try:
        return jget(url)
    except Exception as e:
        return []

# Neg-risk markets across categories (from gamma volume24hr sort)
NEG_MARKETS = {
 'fifa-canada-champ':'0x67443cb1ffb2bf180f7df5b6ca7adff63f7e8e933c7e41405ba118f3e9f8befb',
 'ethiopia-pm-shimelis':'0xf426c69674c6637f2b458c76d9920faa814d4daa8c308e0b8ba554dda509cb8b',
 'fifwc-bra-jpn-bra':'0xf95968b1334c35a5c17867680d783d1d592bb8648b014f7365d03e40e37c99fb',
 'fifwc-ger-par-ger':'0xdac6e67bfc09e630711ceb1be0be8f19c472344ca4353b608cc3b9282e61ec95',
 'wimbledon-siegemund':'0x0571d51f994d898d0eff4f38175924e93038427e697a7dff6d33b0470afe4310',
 'mlb-henderson-runs':'0x3b90e3c4776e73f25d33f745a0324e7d27e5c0efc111c1284a0faf4db664c1d9',
 'f1-albon-champ':'0x596b82d8371efcfcc2fd5312d5dde04f5e9aa1f0c61fd3f93be2a8be09e0da5f',
 'dem-2028-stewart':'0xfee07be730188c94cd3644ed6f107fa3ea2dfab9989ce8d39aeeae064766abe3',
}

def analyze(cid, name, max_tx=8, want_side=None):
    res=[]
    tr = trades(cid, limit=200)
    if not isinstance(tr,list) or not tr:
        return res, 'no trades'
    # filter side if requested
    seen=set()
    cnt=0
    for t in tr:
        side=t.get('side')
        if want_side and side!=want_side: continue
        txh=t.get('transactionHash')
        if not txh or txh in seen: continue
        seen.add(txh)
        taker_wallet=(t.get('proxyWallet') or t.get('taker') or '').lower()
        r=get_receipt(txh)
        if not r: continue
        ofs=decode_orderfilled_logs(r, want_ex=NEGRISK_EX)
        if not ofs:
            # maybe it routed std exchange (shouldn't for neg-risk) -> note
            allofs=decode_orderfilled_logs(r)
            res.append({'tx':txh,'side':side,'note':'no NegRiskEx OrderFilled','n_any':len(allofs)})
            cnt+=1
            if cnt>=max_tx: break
            continue
        # classify each leg
        legs=[]
        for o in ofs:
            tk=o['taker'].lower()
            is_agg = (tk==NEGRISK_EX.lower())
            legs.append({'maker':o['maker'],'taker':o['taker'],'is_agg':is_agg,
                         'fee':o['fee'],'makAmt':o['makerAmountFilled'],'takAmt':o['takerAmountFilled']})
        res.append({'tx':txh,'side':side,'taker_wallet':taker_wallet,'legs':legs})
        cnt+=1
        if cnt>=max_tx: break
    return res, 'ok'

if __name__=='__main__':
    out={}
    for name,cid in NEG_MARKETS.items():
        r,status=analyze(cid,name,max_tx=6)
        out[name]={'cid':cid,'status':status,'rows':r}
        # print compact
        print(f'### {name} [{status}] rows={len(r)}')
        for row in r:
            if 'note' in row:
                print('   tx',row['tx'][:16],row['side'],row['note'],'n_any=',row.get('n_any'))
                continue
            fees=[lg['fee'] for lg in row['legs']]
            realtaker_fee=[lg['fee'] for lg in row['legs'] if not lg['is_agg'] and lg['taker'].lower()==row['taker_wallet']]
            anyrealtaker_fee=[lg['fee'] for lg in row['legs'] if not lg['is_agg']]
            print('   tx',row['tx'][:16],'side',row['side'],'legfees',fees,
                  'realtaker_fee',anyrealtaker_fee)
        print()
    json.dump(out,open('taskD_main_out.json','w'),indent=1)
    print('saved taskD_main_out.json')
