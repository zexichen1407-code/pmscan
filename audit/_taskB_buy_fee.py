"""
TASK B (control experiment): On a BINARY (negRisk=False) market, does the BUY taker leg
write a non-zero fee into OrderFilled.fee?  Reverse feeRate = fee/(shares*p*(1-p)) ~ 0.03 ?

Independence: we confirmed topic0 d0a08e8c = keccak(OrderFilled(bytes32,address,address,uint256x5)).
fee = word index 4 (last). topics[2]=maker, topics[3]=taker.

Direction classification per leg (which asset is USDC, assetId==0):
  - If makerAssetId==0: maker gives USDC, receives token  => maker is BUYING token, taker is SELLING token.
        So the TAKER side = SELL.
  - If takerAssetId==0: taker gives USDC, receives token  => taker is BUYING token.
        So the TAKER side = BUY.   <-- this is the leg we care about.
We then cross-check the leg's taker address direction against the data-api trade's side label.
"""
import json, requests, time, sys
from concurrent.futures import ThreadPoolExecutor

HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
HDRG={'User-Agent':'Mozilla/5.0'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
OF_PREFIXES=('0xd543adfd','0xd0a08e8c')
STD_EXCH='0xe111180000d2663c0091e4f400237545b87b996b'   # standard CTF V2 (binary)
NEG_EXCH='0xe2222d279d744050d28e00520010520000310f59'   # neg-risk

def w(data,i):
    s=data[2:]; return int(s[i*64:(i+1)*64],16)
def ta(t): return '0x'+t[-40:].lower()

def receipt(tx):
    pl={'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}
    for rpc in RPCS:
        for _ in range(2):
            try:
                r=requests.post(rpc,json=pl,headers=HDR,timeout=25)
                if r.status_code==200 and r.json().get('result'): return r.json()['result']
            except Exception: time.sleep(0.3)
    return None

def of_legs(tx):
    rec=receipt(tx)
    if not rec: return None
    out=[]
    for lg in rec['logs']:
        tp=lg.get('topics',[])
        if not tp or not any(tp[0].lower().startswith(p) for p in OF_PREFIXES) or len(tp)<4: continue
        maker=ta(tp[2]); taker=ta(tp[3])
        mkid,tkid=w(lg['data'],0),w(lg['data'],1)
        mamt,tamt,fee=w(lg['data'],2),w(lg['data'],3),w(lg['data'],4)
        exch=lg['address'].lower()
        # direction from taker's perspective
        if tkid==0:        # taker pays USDC, receives token -> taker BUYS
            taker_side='BUY'; usdc=tamt; tok=mamt
        elif mkid==0:      # taker gives token, receives USDC -> taker SELLS
            taker_side='SELL'; usdc=mamt; tok=tamt
        else:
            taker_side='?'; usdc=None; tok=None
        px=(usdc/tok if (usdc and tok) else None)
        out.append({'exch':exch,'maker':maker,'taker':taker,'taker_side':taker_side,
                    'makerAssetId':mkid,'takerAssetId':tkid,'usdc':usdc,'tok':tok,
                    'fee':fee,'px':px})
    return out

def main():
    # 1. find binary (negRisk=False) high-volume sports/esports markets
    g=requests.get('https://gamma-api.polymarket.com/markets?closed=false&limit=120&order=volume24hr&ascending=false',headers=HDRG,timeout=25).json()
    cands=[]
    for m in g:
        if m.get('negRisk') is False and m.get('conditionId'):
            tags=[ (t.get('label') if isinstance(t,dict) else t) for t in (m.get('tags') or []) ]
            cands.append({'cid':m['conditionId'],'slug':m.get('slug'),'vol':m.get('volume24hr'),'tags':tags})
    print(f"[binary negRisk=False candidates: {len(cands)}]")
    for c in cands[:12]:
        print(f"   {c['slug'][:50]:52} vol24={c['vol']}  tags={c['tags'][:4]}")

    results=[]
    # iterate candidates until we have enough BUY-taker fee evidence
    for c in cands[:20]:
        cid=c['cid']
        try:
            tr=requests.get(f'https://data-api.polymarket.com/trades?market={cid}&limit=200',headers=HDRG,timeout=25).json()
        except Exception as e:
            continue
        if not isinstance(tr,list) or not tr: continue
        buys=[t for t in tr if t.get('side')=='BUY' and t.get('transactionHash')]
        # dedupe tx but remember the api trade side+taker
        seen=set(); picks=[]
        for t in buys:
            tx=t['transactionHash']
            if tx in seen: continue
            seen.add(tx); picks.append(t)
            if len(picks)>=12: break
        if not picks: continue
        print(f"\n=== {c['slug'][:48]} (cid {cid[:10]}..)  BUY trades sampled: {len(picks)} ===")
        with ThreadPoolExecutor(max_workers=10) as ex:
            legsets=list(ex.map(lambda t: of_legs(t['transactionHash']), picks))
        for t,legs in zip(picks,legsets):
            if legs is None:
                continue
            # api taker proxy wallet (the trader whose side=BUY)
            api_taker=(t.get('proxyWallet') or '').lower()
            std_legs=[lg for lg in legs if lg['exch']==STD_EXCH]
            for lg in std_legs:
                rec={'slug':c['slug'],'tx':t['transactionHash'],'exch':lg['exch'],
                     'api_side':t.get('side'),'api_taker':api_taker,
                     'leg_taker':lg['taker'],'leg_taker_side':lg['taker_side'],
                     'taker_is_api':(lg['taker']==api_taker),
                     'px':lg['px'],'tok':lg['tok'],'fee':lg['fee'],
                     'feeRate':(lg['fee']/(lg['tok']*lg['px']*(1-lg['px'])) if (lg['fee'] and lg['tok'] and lg['px'] and 0<lg['px']<1) else 0)}
                results.append(rec)
        # stop once we have a solid number of BUY-taker legs with the taker matching api buyer
        buy_taker_legs=[r for r in results if r['leg_taker_side']=='BUY' and r['taker_is_api']]
        if len(buy_taker_legs)>=15:
            break

    # ---- analysis ----
    print("\n\n################ ANALYSIS ################")
    # The crucial set: legs where taker side == BUY AND the leg taker == the api buyer (so it's genuinely a BUY taker)
    buy_taker = [r for r in results if r['leg_taker_side']=='BUY' and r['taker_is_api']]
    print(f"Total std-exchange OrderFilled legs decoded: {len(results)}")
    print(f"Legs where TAKER side=BUY and leg-taker==api-buyer (true BUY taker): {len(buy_taker)}")
    nz=[r for r in buy_taker if r['fee']>0]
    print(f"   of those, fee>0: {len(nz)}")
    if buy_taker:
        rates=sorted(r['feeRate'] for r in nz)
        print(f"   reverse feeRate of nonzero BUY-taker legs: " +
              (f"median={rates[len(rates)//2]:.4f} min={min(rates):.4f} max={max(rates):.4f}" if rates else "n/a"))
    print("\n  ---- sample BUY-taker legs (taker==api buyer) ----")
    for r in nz[:12]:
        print(f"   tx={r['tx']}")
        print(f"      slug={r['slug'][:38]} px={r['px']:.4f} shares={r['tok']/1e6:.2f} fee={r['fee']} micro (${r['fee']/1e6:.5f}) feeRate={r['feeRate']:.4f}")
    # Also show the SELL-taker comparison
    sell_taker=[r for r in results if r['leg_taker_side']=='SELL' and r['taker_is_api'] and r['fee']>0]
    print(f"\n  (compare) SELL-taker legs with fee>0: {len(sell_taker)}")
    # And maker-side fee check: maker legs should have fee handled where taker pays; show maker legs fee
    maker_fee_nonzero=[r for r in results if (not r['taker_is_api']) and r['fee']>0]
    json.dump(results, open('_taskB_buy_fee_out.json','w'), indent=1)
    print(f"\n[wrote _taskB_buy_fee_out.json  ({len(results)} legs)]")

if __name__=='__main__':
    main()
