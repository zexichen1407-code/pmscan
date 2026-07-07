"""
现金流口径 edge:每个事件他买NO花的钱(USDC流出) vs convert/merge/redeem收回的钱(USDC流入),净额=利润。
完全脱离 API 的 usdcSize(对conversion不准),直接读链上抵押物 ERC20 净流。
"""
import json, requests, sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
ERC20_XFER='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
# 抵押物候选(6位小数稳定币)
COLLATERAL={
 '0x2791bca1f2de4661ed88a30c99a7a9449aa84174':'USDC.e',
 '0x3c499c542cef5e3811e1192ce70d8cc03d5c3359':'USDC',
 '0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f':'pUSD?',  # 待验
}
def ta(t): return '0x'+t[-40:].lower()
def receipt(tx):
    pl={'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}
    for rpc in RPCS:
        try:
            r=requests.post(rpc,json=pl,headers=HDR,timeout=20)
            if r.status_code==200 and r.json().get('result'): return r.json()['result']
        except Exception: pass
    return None
def cash_flow(tx):
    """返回 (流入proxy, 流出proxy) 抵押物,单位USDC。按所有6位小数ERC20稳定币统计."""
    rec=receipt(tx)
    if not rec: return None
    inflow=0.0; outflow=0.0; tokens=set()
    for lg in rec['logs']:
        tp=lg.get('topics',[])
        if not tp or tp[0].lower()!=ERC20_XFER or len(tp)<3: continue
        token=lg['address'].lower()
        frm=ta(tp[1]); to=ta(tp[2])
        if SUB not in (frm,to): continue
        try: val=int(lg['data'],16)/1e6  # 6位小数
        except Exception: continue
        tokens.add(token)
        if to==SUB:  inflow+=val
        if frm==SUB: outflow+=val
    return inflow,outflow,tokens

d=json.load(open('recent_activity.json'))
mine=[r for r in d if r.get('proxyWallet','').lower()==SUB]
ev=defaultdict(lambda:{'txs':set(),'tmax':0,'spent_api':0.0,'types':defaultdict(int)})
for r in mine:
    e=ev[r['eventSlug']]
    if r.get('transactionHash'): e['txs'].add(r['transactionHash'])
    e['tmax']=max(e['tmax'],r['timestamp'])
    e['types'][r['type']]+=1
    if r['type']=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No':
        e['spent_api']+=r['usdcSize']

mode=sys.argv[1] if len(sys.argv)>1 else 'validate'
if mode=='validate':
    es='highest-temperature-in-beijing-on-june-29-2026'
    e=ev[es]
    print('验证事件:',es,' txs:',len(e['txs']),' API买No花费=$%.2f'%e['spent_api'])
    inf=outf=0.0; allts=set()
    with ThreadPoolExecutor(max_workers=10) as ex:
        for res in ex.map(cash_flow,e['txs']):
            if res: inf+=res[0]; outf+=res[1]; allts|=res[2]
    print(f'  链上抵押物: 流入=${inf:.2f} 流出=${outf:.2f} 净=${inf-outf:.2f}')
    print(f'  涉及token: {[COLLATERAL.get(t,t[:10]) for t in allts]}')
    print(f'  对照: 链上流出 ${outf:.2f} vs API买No ${e["spent_api"]:.2f} (应接近)')
else:
    events=sorted(ev.items(),key=lambda kv:-kv[1]['tmax'])[:20]
    print(f"{'事件':44} {'花费$':9} {'收回$':9} {'净edge$':9} {'edge%':8} {'类型'}")
    tot_s=tot_r=0
    for slug,e in events:
        inf=outf=0.0
        with ThreadPoolExecutor(max_workers=10) as ex:
            for res in ex.map(cash_flow,list(e['txs'])):
                if res: inf+=res[0]; outf+=res[1]
        net=inf-outf; pct=net/outf*100 if outf else 0
        tot_s+=outf; tot_r+=inf
        ty=','.join(f'{k}:{v}' for k,v in e['types'].items())
        print(f"{slug[:44]:44} {outf:9.2f} {inf:9.2f} {net:9.2f} {pct:7.2f}% {ty[:40]}")
    print(f"\n总计20事件: 花费 ${tot_s:.2f}  收回 ${tot_r:.2f}  净 ${tot_r-tot_s:.2f}  edge {((tot_r-tot_s)/tot_s*100 if tot_s else 0):.2f}%")
