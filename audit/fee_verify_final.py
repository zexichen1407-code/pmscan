"""
生死数最终核验:
A) 他的 neg-risk taker 腿 —— 实测费 vs 文档公式反事实费(尤其 p∈[0.35,0.65] 高费区)
B) 二元非 neg-risk 体育盘 taker 腿 —— 反算实收 feeRate,证明费引擎是活的
公式(docs): fee = shares × feeRate × p × (1-p)
"""
import json, requests, time
from concurrent.futures import ThreadPoolExecutor
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
OF_PREFIXES=('0xd543adfd','0xd0a08e8c')
SUBJECT='0x4f1d5ae26fc31472966e951af3183308736d8de2'
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
def of_legs(tx, taker_must=None):
    rec=receipt(tx)
    if not rec: return []
    out=[]
    for lg in rec['logs']:
        tp=lg.get('topics',[])
        if not tp or not any(tp[0].lower().startswith(p) for p in OF_PREFIXES) or len(tp)<4: continue
        maker=ta(tp[2]); taker=ta(tp[3])
        if taker_must and taker!=taker_must: continue
        mk,tkid=w(lg['data'],0),w(lg['data'],1)
        mamt,tamt,fee=w(lg['data'],2),w(lg['data'],3),w(lg['data'],4)
        # 价格:有一边 assetId=0(USDC)。usdc/token
        if mk==0:   usdc,tok=mamt,tamt
        elif tkid==0: usdc,tok=tamt,mamt
        else:       usdc,tok=None,None
        out.append({'maker':maker,'taker':taker,'usdc':usdc,'tok':tok,'fee':fee,
                    'px': (usdc/tok if usdc and tok else None)})
    return out

# ---- A: 他的 neg-risk ----
d=json.load(open('recent_activity.json'))
buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No']
buys.sort(key=lambda r:-r['timestamp'])
seen={}
for r in buys: seen.setdefault(r['transactionHash'],r)
txs=list(seen.keys())[:200]
with ThreadPoolExecutor(max_workers=12) as ex:
    legsets=list(ex.map(lambda tx: of_legs(tx, SUBJECT), txs))
neg=[lg for s in legsets for lg in s if lg['px']]
print(f"A) 他 neg-risk taker 腿(有价): {len(neg)}")
# 高费区
hot=[lg for lg in neg if 0.35<=lg['px']<=0.65]
tot_fee_hot=sum(lg['fee'] for lg in hot)
# 反事实:weather feeRate=0.05
cf=sum(lg['tok']*0.05*lg['px']*(1-lg['px']) for lg in hot)  # 单位 micro
print(f"   高费区 p∈[0.35,0.65] 腿数={len(hot)}")
print(f"     实测 fee 合计 = {tot_fee_hot} micro = ${tot_fee_hot/1e6:.5f}")
print(f"     若按 weather 0.05 档应收 = {cf:.0f} micro = ${cf/1e6:.5f}")
print(f"     => 实际只收了应收的 {100*tot_fee_hot/cf if cf else 0:.2f}%")
allfee=sum(lg['fee'] for lg in neg); allnotional=sum(lg['usdc'] for lg in neg)
print(f"   全部腿 实测 fee/notional = {allfee/allnotional:.6%}  (fee={allfee} micro / ${allnotional/1e6:.2f})")

# ---- B: 二元非 neg-risk 体育盘 反算 feeRate ----
g=requests.get('https://gamma-api.polymarket.com/markets?closed=false&limit=80&order=volume24hr&ascending=false',headers=HDR,timeout=20).json()
binm=[m for m in g if m.get('negRisk') is False and m.get('conditionId')][:3]
print(f"\nB) 二元非neg-risk盘反算 feeRate ({len(binm)}个盘):")
for m in binm:
    cid=m['conditionId']
    tr=requests.get(f'https://data-api.polymarket.com/trades?market={cid}&limit=60',headers={'User-Agent':'Mozilla/5.0'},timeout=20).json()
    if not isinstance(tr,list): continue
    rates=[]
    txseen=set()
    for t in tr:
        tx=t.get('transactionHash')
        if not tx or tx in txseen: continue
        txseen.add(tx)
        for lg in of_legs(tx):
            if lg['fee']>0 and lg['px'] and lg['tok']:
                p=lg['px']; implied=lg['fee']/(lg['tok']*p*(1-p))
                rates.append(implied)
        if len(txseen)>=18: break
    if rates:
        rates.sort()
        med=rates[len(rates)//2]
        print(f"   {m.get('slug')[:38]:40} 收费腿={len(rates)} 反算feeRate中位={med:.4f} (min {min(rates):.4f} max {max(rates):.4f})")
    else:
        print(f"   {m.get('slug')[:38]:40} 无收费腿")
