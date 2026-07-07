import json
SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'.lower()
PUSD='0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb'.lower()
USDC='0x2791bca1f2de4661ed88a30c99a7a9449aa84174'.lower()
NEGRISK='0xe2222d279d744050d28e00520010520000310f59'.lower()
R=json.load(open('taskE_out.json'))

# The neg-risk NO-buy is recorded as a SELF-MATCH:
#  - SUBJECT as MAKER posts a "buy NO" order: gives collateral (makerAmt), wants NO (takerAmt=50.03). fee=F.
#  - The exchange/operator fills it by sourcing the NO: either matching real maker NO-sell orders
#    (taker fills where takerAsset is OTHER outcomes' NO that get converted), or directly.
#  - Net effect on SUBJECT wallet: collateral OUT, NO IN.
#
# CRITICAL RECONCILIATION (fee-field-independent):
#   wallet_collateral_out  vs  (maker_fill makerAmt) and vs (maker_fill makerAmt + fee)
#   If wallet_out == makerAmt (fee NOT added on top)  -> fee field is informational, NOT extra debit.
#   If wallet_out == makerAmt + fee                   -> fee WAS charged on top.
#   Also: NO_received vs maker_fill takerAmt; if equal & no share_out -> no share skim.

print('{:18} {:>12} {:>12} {:>12} {:>12} {:>10} {:>10}'.format(
    'tx','wallet_out','mkr_makerAmt','mkr+fee','NO_recv','mkr_takerAmt','share_out'))
for r in R:
    m=r['meta']
    net_out=sum((info['out']-info['in']) for tok,info in r['erc20'].items() if tok in (USDC,PUSD))/1e6
    mk=[e for e in r['of_events'] if e['maker']==SUB]
    # assume single maker fill is the order leg
    mk_makerAmt=sum(e['makerAmt'] for e in mk)/1e6
    mk_fee=sum(e['fee'] for e in mk)/1e6
    mk_takerAmt=sum(e['takerAmt'] for e in mk)/1e6
    no_asset=int(m['asset'])
    in_no=sum(v for aid,v,frm in r['erc1155_in'] if aid==no_asset)/1e6
    out_no=sum(v for aid,v,to in r['erc1155_out'])/1e6
    print('{:18} {:12.6f} {:12.6f} {:12.6f} {:12.6f} {:12.6f} {:10.4f}'.format(
        r['tx'][:18], net_out, mk_makerAmt, mk_makerAmt+mk_fee, in_no, mk_takerAmt, out_no))

print()
print('Interpretation per tx:')
for r in R:
    m=r['meta']
    net_out=sum((info['out']-info['in']) for tok,info in r['erc20'].items() if tok in (USDC,PUSD))/1e6
    mk=[e for e in r['of_events'] if e['maker']==SUB]
    mk_makerAmt=sum(e['makerAmt'] for e in mk)/1e6
    mk_fee=sum(e['fee'] for e in mk)/1e6
    # taker collateral he also pays directly (complement legs)
    tk=[e for e in r['of_events'] if e['taker']==SUB]
    tk_coll=sum(e['makerAmt'] for e in tk if e['makerAssetId']==0)/1e6
    tk_fee=sum(e['fee'] for e in tk)/1e6
    # candidate models
    model_makerOnly = mk_makerAmt          # if maker leg IS the whole order (taker legs are internal sourcing not extra wallet debit)
    model_maker_plus_fee = mk_makerAmt+mk_fee
    diff_makerOnly = net_out - model_makerOnly
    diff_plusfee = net_out - model_maker_plus_fee
    print('  {} wallet_out={:.6f}'.format(r['tx'][:14], net_out))
    print('      maker_makerAmt={:.6f} (diff {:+.6f})  maker+fee={:.6f} (diff {:+.6f})  mk_fee={:.6f} tk_coll={:.6f} tk_fee={:.6f}'.format(
        mk_makerAmt, diff_makerOnly, mk_makerAmt+mk_fee, diff_plusfee, mk_fee, tk_coll, tk_fee))
