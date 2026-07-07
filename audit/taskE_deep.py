import json
SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'.lower()
PUSD='0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb'.lower()
USDC='0x2791bca1f2de4661ed88a30c99a7a9449aa84174'.lower()
R=json.load(open('taskE_out.json'))

for r in R:
    m=r['meta']
    print('\n==== TX',r['tx'][:18],'reported price',m['price'],'usdcSize',m['usdcSize'])
    no_asset=int(m['asset'])
    taker_fills=[e for e in r['of_events'] if e['taker']==SUB]
    maker_fills=[e for e in r['of_events'] if e['maker']==SUB]
    print('  taker fills (SUBJECT pays, receives takerAsset):')
    for e in taker_fills:
        is_no = e['takerAssetId']==no_asset
        kind = 'DIRECT-NO-buy' if is_no else 'buy-OTHER-NO(complement)'
        ta = 'NO' if is_no else (str(e['takerAssetId'])[:10]+'..')
        px = e['makerAmt']/e['takerAmt'] if e['takerAmt'] else 0
        ma = 'collat' if e['makerAssetId']==0 else str(e['makerAssetId'])[:8]
        print('    makerAsset={:>6} takerAsset={:>12} makerAmt={:.6f} takerAmt={:.6f} px={:.5f} FEE={}  [{}]'.format(
            ma, ta, e['makerAmt']/1e6, e['takerAmt']/1e6, px, e['fee'], kind))
    print('  maker fills (SUBJECT is maker, gives makerAsset):')
    for e in maker_fills:
        ma = 'collat' if e['makerAssetId']==0 else ('NO' if e['makerAssetId']==no_asset else str(e['makerAssetId'])[:8])
        ta = 'collat' if e['takerAssetId']==0 else ('NO' if e['takerAssetId']==no_asset else str(e['takerAssetId'])[:8])
        print('    GIVES makerAsset={:>6} takerAsset={:>6} makerAmt={:.6f} takerAmt={:.6f} FEE={} (={:.6f})'.format(
            ma, ta, e['makerAmt']/1e6, e['takerAmt']/1e6, e['fee'], e['fee']/1e6))
    allfee=sum(e['fee'] for e in r['of_events'])
    net=sum((info['out']-info['in']) for tok,info in r['erc20'].items() if tok in (USDC,PUSD))/1e6
    taker_coll = sum(e['makerAmt'] for e in taker_fills if e['makerAssetId']==0)/1e6
    print('  TOTAL fee(all fills)={} ({:.6f})  | SUBJECT net collateral out={:.6f} | sum taker-collateral-out legs={:.6f}'.format(
        allfee, allfee/1e6, net, taker_coll))
