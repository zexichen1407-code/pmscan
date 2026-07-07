"""
Full single-transaction teardown of a BINARY BUY trade to nail down:
  - which OrderFilled leg is the TAKER order (the aggressive BUY) vs the MAKER order
  - whether that taker BUY leg carries non-zero fee
  - reverse feeRate against the ACTUAL fill price reported by data-api

Polymarket CtfExchange semantics (verified by structure):
  matchOrders(takerOrder, makerOrders[]) emits one OrderFilled per maker order with
     (orderHash, maker=makerOrderSigner, taker=takerOrderSigner) and a FINAL OrderFilled
     for the taker order itself with (maker=takerOrderSigner, taker=Exchange address?)...
  We empirically separate by: the leg whose topics[3] == Exchange contract address is the
  taker's aggregated leg; the leg whose topics[3] == a real EOA/proxy is a maker leg.
"""
import json, requests, time, sys
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
HDRG={'User-Agent':'Mozilla/5.0'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
OF_PREFIXES=('0xd543adfd','0xd0a08e8c')
STD_EXCH='0xe111180000d2663c0091e4f400237545b87b996b'
CTF='0x4d97dcd97ec945f40cf65f87097ace5ea0476045'
TRANSFER_SINGLE='0xc3d58168'
ERC20_TRANSFER='0xddf252ad'

def w(data,i):
    s=data[2:]; return int(s[i*64:(i+1)*64],16)
def ta(t): return '0x'+t[-40:].lower()
def receipt(tx):
    pl={'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}
    for rpc in RPCS:
        try:
            r=requests.post(rpc,json=pl,headers=HDR,timeout=25)
            if r.status_code==200 and r.json().get('result'): return r.json()['result']
        except Exception: time.sleep(0.3)
    return None

def teardown(tx, api_trade):
    rec=receipt(tx)
    print(f"\n================ TX {tx} ================")
    print(f"  data-api: side={api_trade.get('side')} price={api_trade.get('price')} size={api_trade.get('size')} outcome={api_trade.get('outcome')} taker(proxy)={api_trade.get('proxyWallet')}")
    of=[]
    for lg in rec['logs']:
        tp=lg.get('topics',[])
        if tp and any(tp[0].lower().startswith(p) for p in OF_PREFIXES) and len(tp)>=4:
            maker=ta(tp[2]); taker=ta(tp[3])
            mkid,tkid=w(lg['data'],0),w(lg['data'],1)
            mamt,tamt,fee=w(lg['data'],2),w(lg['data'],3),w(lg['data'],4)
            of.append({'addr':lg['address'].lower(),'maker':maker,'taker':taker,
                       'makerAssetId':mkid,'takerAssetId':tkid,
                       'makerAmt':mamt,'takerAmt':tamt,'fee':fee})
    print(f"  OrderFilled legs: {len(of)}")
    for i,l in enumerate(of):
        is_taker_leg = (l['taker']==STD_EXCH)
        # token/usdc split
        if l['makerAssetId']==0:
            usdc=l['makerAmt']; tok=l['takerAmt']; tokid=l['takerAssetId']
        elif l['takerAssetId']==0:
            usdc=l['takerAmt']; tok=l['makerAmt']; tokid=l['makerAssetId']
        else:
            usdc=tok=None; tokid=None
        px=(usdc/tok if usdc and tok else None)
        role = 'TAKER-ORDER-LEG' if is_taker_leg else 'maker-order-leg'
        # for feeRate, use api fill price when this is a token-for-token leg
        px_for_fee = px if px else float(api_trade.get('price') or 0)
        # shares: if token-for-token, both amts are token; use the larger token amount as shares proxy
        shares = tok if tok else max(l['makerAmt'], l['takerAmt'])
        fr = (l['fee']/(shares*px_for_fee*(1-px_for_fee)) if (l['fee'] and shares and 0<px_for_fee<1) else 0)
        pxs = f'{px:.4f}' if px else 'NA(token-for-token)'
        mid = 'USDC' if l['makerAssetId']==0 else (str(l['makerAssetId'])[:12]+'..(tok)')
        tid = 'USDC' if l['takerAssetId']==0 else (str(l['takerAssetId'])[:12]+'..(tok)')
        print(f"   [{i}] {role}  on {l['addr']}")
        print(f"        maker={l['maker']} taker={l['taker']}")
        print(f"        makerAssetId={mid}  takerAssetId={tid}")
        print(f"        makerAmt={l['makerAmt']} takerAmt={l['takerAmt']}  => px(usdc/tok)={pxs}")
        print(f"        shares={shares/1e6 if shares else None}  FEE={l['fee']} micro (${l['fee']/1e6:.6f})  reverse feeRate(@px={px_for_fee:.3f})={fr:.4f}")
    return of

def main():
    # find a binary negRisk=False market, grab a couple of BUY trades, teardown
    g=requests.get('https://gamma-api.polymarket.com/markets?closed=false&limit=120&order=volume24hr&ascending=false',headers=HDRG,timeout=25).json()
    cands=[m for m in g if m.get('negRisk') is False and m.get('conditionId')]
    chosen=[]
    for m in cands:
        cid=m['conditionId']
        tr=requests.get(f'https://data-api.polymarket.com/trades?market={cid}&limit=100',headers=HDRG,timeout=20).json()
        if not isinstance(tr,list): continue
        # want BUY trades at a NON-extreme price so p(1-p) is meaningful and feeRate is well-conditioned
        buys=[t for t in tr if t.get('side')=='BUY' and 0.15<=float(t.get('price',0))<=0.85]
        if buys:
            print(f"\n##### MARKET {m.get('slug')}  (negRisk=False, cid={cid}) #####")
            seen=set()
            for t in buys:
                if t['transactionHash'] in seen: continue
                seen.add(t['transactionHash'])
                teardown(t['transactionHash'], t)
                chosen.append(1)
                if len(seen)>=3: break
        if len(chosen)>=6:
            break

if __name__=='__main__':
    main()
