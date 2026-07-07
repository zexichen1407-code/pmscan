import json

SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'.lower()
NEGRISK='0xe2222d279d744050d28e00520010520000310f59'.lower()
CTFV2='0xe111180000d2663c0091e4f400237545b87b996b'.lower()
CTF='0x4d97dcd97ec945f40cf65f87097ace5ea0476045'.lower()
USDC='0x2791bca1f2de4661ed88a30c99a7a9449aa84174'.lower()
PUSD='0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb'.lower()
TOK={USDC:'USDC.e',PUSD:'pUSD'}

R=json.load(open('taskE_out.json'))
print('='*120)
for r in R:
    m=r['meta']
    print(f"\nTX {r['tx']}")
    print(f"  slug={m['slug'][:34]} reported: price={m['price']:.4f} size={m['size']:.2f} usdcSize={m['usdcSize']:.2f} assetIdx(outcomeIndex)={m['outcomeIndex']}")
    print(f"  receipt to={r['to']}  ({'NEG-RISK' if r['to']==NEGRISK else 'CTF-V2' if r['to']==CTFV2 else r['to']})")
    # ERC20 net per stablecoin
    net_usd_out=0.0
    for tok,info in r['erc20'].items():
        if tok in (USDC,PUSD):
            o=info['out']/1e6; i=info['in']/1e6
            net=o-i
            net_usd_out+=net
            if o or i:
                print(f"  {TOK[tok]}: SUBJECT out={o:.6f} in={i:.6f} net_out={net:.6f}")
                # who did he pay / receive from
                for to,v in info['out_to'].items():
                    print(f"      out -> {to}: {v/1e6:.6f}")
                for frm,v in info['in_from'].items():
                    print(f"      in  <- {frm}: {v/1e6:.6f}")
    # ERC1155 NO shares (6 decimals on CTF outcome tokens? Polymarket CTF shares are 1e6)
    in_by_asset={}
    for aid,val,frm in r['erc1155_in']:
        in_by_asset[aid]=in_by_asset.get(aid,0)+val
    out_by_asset={}
    for aid,val,to in r['erc1155_out']:
        out_by_asset[aid]=out_by_asset.get(aid,0)+val
    print(f"  ERC1155 IN  (outcome tokens received) assets={len(in_by_asset)}:")
    tot_in=0
    for aid,val in in_by_asset.items():
        print(f"      asset {aid}: {val/1e6:.6f}")
        tot_in+=val
    if out_by_asset:
        print(f"  ERC1155 OUT (outcome tokens SENT by subject!) assets={len(out_by_asset)}:")
        for aid,val in out_by_asset.items():
            # is recipient a counterparty/exchange?
            recips=[to for a2,v2,to in r['erc1155_out'] if a2==aid]
            print(f"      asset {aid}: {val/1e6:.6f}  to={set(recips)}")
    else:
        print(f"  ERC1155 OUT: none (no share skimmed from subject)")
    # the NO asset he bought: match reported asset id
    reported_asset=int(m['asset']) if m.get('asset') else None
    net_no = in_by_asset.get(reported_asset,0)
    # if reported asset not directly matched, take the asset with largest net in
    net_per_asset={aid:in_by_asset.get(aid,0)-out_by_asset.get(aid,0) for aid in set(list(in_by_asset)+list(out_by_asset))}
    if reported_asset in net_per_asset:
        net_no_shares=net_per_asset[reported_asset]/1e6
        matched='reported asset'
    else:
        # pick max net
        best=max(net_per_asset.items(), key=lambda kv:kv[1]) if net_per_asset else (None,0)
        net_no_shares=best[1]/1e6
        matched=f'maxnet asset {best[0]}'
    eff_price = net_usd_out/net_no_shares if net_no_shares else float('nan')
    print(f"  >> NET stable paid = {net_usd_out:.6f} | NET NO shares ({matched}) = {net_no_shares:.6f}")
    print(f"  >> EFFECTIVE PRICE = {eff_price:.6f}  vs reported {m['price']:.6f}  diff={eff_price-m['price']:+.6f} ({(eff_price-m['price'])/m['price']*100:+.4f}%)")
    # OrderFilled fee fields where taker==SUBJECT
    sub_fills=[e for e in r['of_events'] if e['taker']==SUB]
    other_fills=[e for e in r['of_events'] if e['taker']!=SUB]
    feesum=sum(e['fee'] for e in sub_fills)
    print(f"  OrderFilled: total={len(r['of_events'])}, taker==SUBJECT fills={len(sub_fills)} sum(fee field)={feesum}")
    if sub_fills:
        for e in sub_fills:
            print(f"      [taker=SUB] makerAssetId={e['makerAssetId']} takerAssetId={e['takerAssetId']} makerAmt={e['makerAmt']} takerAmt={e['takerAmt']} FEE={e['fee']}")
    # also show maker side fills fee just in case he was maker
    sub_maker=[e for e in r['of_events'] if e['maker']==SUB]
    if sub_maker:
        print(f"      maker==SUBJECT fills={len(sub_maker)} sum(fee)={sum(e['fee'] for e in sub_maker)}")
print('\n'+'='*120)
print('SUMMARY TABLE')
print(f"{'tx':14} {'rep_price':>9} {'eff_price':>9} {'diff%':>8} {'net_paid':>11} {'net_NO':>10} {'fee_field':>9} {'share_out':>9}")
for r in R:
    m=r['meta']
    net_usd_out=sum((info['out']-info['in'])/1e6 for tok,info in r['erc20'].items() if tok in (USDC,PUSD))
    in_by_asset={}
    for aid,val,frm in r['erc1155_in']: in_by_asset[aid]=in_by_asset.get(aid,0)+val
    out_by_asset={}
    for aid,val,to in r['erc1155_out']: out_by_asset[aid]=out_by_asset.get(aid,0)+val
    net_per_asset={aid:in_by_asset.get(aid,0)-out_by_asset.get(aid,0) for aid in set(list(in_by_asset)+list(out_by_asset))}
    reported_asset=int(m['asset']) if m.get('asset') else None
    if reported_asset in net_per_asset: net_no=net_per_asset[reported_asset]/1e6
    else:
        best=max(net_per_asset.items(), key=lambda kv:kv[1]) if net_per_asset else (None,0); net_no=best[1]/1e6
    eff=net_usd_out/net_no if net_no else float('nan')
    feesum=sum(e['fee'] for e in r['of_events'] if e['taker']==SUB)
    shareout=sum(out_by_asset.values())/1e6
    print(f"{r['tx'][:14]} {m['price']:9.4f} {eff:9.4f} {(eff-m['price'])/m['price']*100:+7.3f}% {net_usd_out:11.6f} {net_no:10.4f} {feesum:9d} {shareout:9.4f}")
