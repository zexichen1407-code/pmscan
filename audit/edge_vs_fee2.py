"""
修正版:只取【买齐全 N 腿】的干净篮子,用【首入价】(他每条腿最早那笔成交价)算真实开仓 edge。
一篮 = 一笔交易。
  毛edge/套 = (N-1) - Σ(各腿首入NO价)
  官方应收taker费/套 = Σ_腿 feeRate*p*(1-p)
  净edge/套 = 毛 - 官费
对照:链上实际付的费率。
"""
import json, requests
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
OF_PREFIXES=('0xd543adfd','0xd0a08e8c')
CAT_RATE=[('Geopolitics',0.0),('Crypto',0.07),('Economics',0.05),('Weather',0.05),
          ('Culture',0.05),('Mentions',0.04),('Politics',0.04),('Finance',0.04),
          ('Tech',0.04),('Sports',0.03),('Esports',0.03)]
def cat_rate(tags):
    tset=set(tags or [])
    for c,r in CAT_RATE:
        if c in tset: return c,r
    return 'Other',0.05
def w(data,i):
    s=data[2:]; return int(s[i*64:(i+1)*64],16)
def ta(t): return '0x'+t[-40:].lower()
def receipt(tx):
    pl={'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}
    for rpc in RPCS:
        try:
            r=requests.post(rpc,json=pl,headers=HDR,timeout=20)
            if r.status_code==200 and r.json().get('result'): return r.json()['result']
        except Exception: pass
    return None
def tx_fee_notional(tx):
    rec=receipt(tx)
    if not rec: return 0,0
    fee=0; notion=0
    for lg in rec['logs']:
        tp=lg.get('topics',[])
        if not tp or not any(tp[0].lower().startswith(p) for p in OF_PREFIXES) or len(tp)<4: continue
        if ta(tp[3])!=SUB: continue
        mk,tk=w(lg['data'],0),w(lg['data'],1)
        mamt,tamt,f=w(lg['data'],2),w(lg['data'],3),w(lg['data'],4)
        fee+=f
        notion += mamt if mk==0 else (tamt if tk==0 else min(mamt,tamt))
    return fee,notion

d=json.load(open('recent_activity.json'))
buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No'
      and r.get('proxyWallet','').lower()==SUB]
# 每事件:每腿(slug)记录 (最早时间, 该笔价格) + 全部 tx
ev=defaultdict(lambda: {'first':{}, 'tmax':0, 'txs':defaultdict(set)})
for r in buys:
    e=ev[r['eventSlug']]; s=r['slug']; t=r['timestamp']
    if s not in e['first'] or t<e['first'][s][0]:
        e['first'][s]=(t,float(r['price']))
    e['tmax']=max(e['tmax'],t)
    e['txs'][s].add(r['transactionHash'])

cache={}
def gamma_n(slug):
    if slug in cache: return cache[slug]
    try:
        j=requests.get(f'https://gamma-api.polymarket.com/events?slug={slug}',headers={'User-Agent':'Mozilla/5.0'},timeout=20).json()
        e=j[0] if isinstance(j,list) and j else None
        n=len(e.get('markets',[])) if e else None
        tags=[t.get('label') if isinstance(t,dict) else t for t in (e.get('tags',[]) if e else [])]
        cache[slug]=(n,tags); return n,tags
    except Exception:
        cache[slug]=(None,[]); return None,[]

# 按最近排序,逐个查全腿数,挑买齐(n_bought==n_total)的,凑够20个
events=sorted(ev.items(), key=lambda kv:-kv[1]['tmax'])
clean=[]
for slug,e in events:
    n_total,tags=gamma_n(slug)
    nb=len(e['first'])
    if n_total and nb==n_total:
        clean.append((slug,e,n_total,tags))
    if len(clean)>=20: break

print(f"{'事件(买齐全腿)':40} {'类目':6} {'N':3} {'ΣNO首入':8} {'毛edge¢':8} {'edge%':7} {'官费¢/套':8} {'净edge¢':8} {'官费/毛edge':10} {'链上实费%':9}")
def actual(txsets):
    txs=set()
    for s in txsets.values(): txs|=s
    txs=list(txs)[:8]
    tf=tn=0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for f,n in ex.map(tx_fee_notional,txs): tf+=f; tn+=n
    return (tf/tn*100) if tn else 0.0

agg=[]
for slug,e,N,tags in clean:
    cat,rate=cat_rate(tags)
    prices=[p for (t,p) in e['first'].values()]
    sigma=sum(prices)
    gross=(N-1)-sigma
    edge_pct=gross/sigma*100 if sigma else 0
    fee=sum(rate*p*(1-p) for p in prices)
    net=gross-fee
    foe=fee/gross*100 if gross>0 else None
    act=actual(e['txs'])
    agg.append((slug,cat,N,sigma,gross,edge_pct,fee,net,foe,act))
    foes = f"{foe:.0f}%" if foe is not None else "n/a(负)"
    print(f"{slug[:40]:40} {cat[:6]:6} {N:<3} {sigma:8.3f} {gross*100:8.2f} {edge_pct:6.2f}% {fee*100:8.3f} {net*100:8.2f} {foes:>10} {act:8.4f}%")

import statistics as st
pos=[a for a in agg if a[4]>0]
print(f"\n=== 汇总({len(agg)}个买齐篮子) ===")
print(f"毛edge/套: 中位 {st.median(a[4] for a in agg)*100:.2f}¢ , 均值 {st.mean([a[4] for a in agg])*100:.2f}¢ , 正edge篮子 {len(pos)}/{len(agg)}")
print(f"官方费/套: 中位 {st.median(a[6] for a in agg)*100:.3f}¢ , 均值 {st.mean([a[6] for a in agg])*100:.3f}¢")
print(f"净edge(扣官费)/套: 中位 {st.median(a[7] for a in agg)*100:.2f}¢ , 扣官费后仍为正: {sum(1 for a in agg if a[7]>0)}/{len(agg)}")
if pos:
    print(f"[只看正edge篮子] 官费吃掉毛edge: 中位 {st.median(a[8] for a in pos):.0f}% , 扣后仍正 {sum(1 for a in pos if a[7]>0)}/{len(pos)}")
print(f"链上实际费率: 均值 {st.mean([a[9] for a in agg]):.4f}% (≈0)")
