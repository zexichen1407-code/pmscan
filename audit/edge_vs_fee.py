"""
他最近 20 个 neg-risk 篮子(一个事件的一篮 NO = 一笔交易):
  毛 edge/套 = (N-1) - Σ(各腿NO价)   [需买齐全 N 腿才是干净套利]
  官方应收 taker费/套 = Σ_腿 feeRate * p * (1-p)   (按类目 feeRate)
  净 edge/套 = 毛 - 官方费
另列链上【实际】付的费,做对照。
"""
import json, requests, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
OF_PREFIXES=('0xd543adfd','0xd0a08e8c')

# 类目 -> feeRate (官方动态费公式 fee=shares*feeRate*p*(1-p);已用二元盘链上反算验证 Sports=0.03)
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
        usdc = mamt if mk==0 else (tamt if tk==0 else min(mamt,tamt))
        notion+=usdc
    return fee,notion

# ---- 1. 聚合他的 NO 买单 ----
d=json.load(open('recent_activity.json'))
buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No'
      and r.get('proxyWallet','').lower()==SUB]
# 按事件聚合,每个 leg(slug) size 加权均价
ev=defaultdict(lambda: {'legs':defaultdict(lambda:{'sz':0.0,'pv':0.0}), 'tmax':0, 'txs':set()})
for r in buys:
    e=ev[r['eventSlug']]
    lg=e['legs'][r['slug']]
    sz=float(r['size']); lg['sz']+=sz; lg['pv']+=sz*float(r['price'])
    e['tmax']=max(e['tmax'], r['timestamp'])
    e['txs'].add(r['transactionHash'])

# 最近 20 个事件
events=sorted(ev.items(), key=lambda kv:-kv[1]['tmax'])[:20]

def gamma_event(slug):
    try:
        j=requests.get(f'https://gamma-api.polymarket.com/events?slug={slug}',headers={'User-Agent':'Mozilla/5.0'},timeout=20).json()
        if isinstance(j,list) and j: return j[0]
    except Exception: pass
    return None

print(f"{'事件':42} {'类目':6} {'rate':5} {'买腿/总':7} {'ΣNO':7} {'毛edge¢':8} {'edge%':7} {'官费¢':7} {'净edge¢':8} {'官费/edge':9} {'链上实费%':9}")
rows=[]
for slug,e in events:
    ge=gamma_event(slug)
    n_total = len(ge.get('markets',[])) if ge else None
    tags=[t.get('label') if isinstance(t,dict) else t for t in (ge.get('tags',[]) if ge else [])]
    cat,rate=cat_rate(tags)
    legs=e['legs']
    n_bought=len(legs)
    # 每腿均价
    prices={s:(v['pv']/v['sz']) for s,v in legs.items() if v['sz']>0}
    sigma_no=sum(prices.values())  # 买的腿,一股各
    N = n_total or n_bought
    gross = (N-1) - sigma_no  # 若买齐=干净;否则缺腿使ΣNO偏小→gross偏乐观,标完整度
    edge_pct = gross/sigma_no*100 if sigma_no else 0
    fee_set = sum(rate*p*(1-p) for p in prices.values())  # 官方,一股各腿
    net = gross - fee_set
    fee_over_edge = fee_set/gross*100 if gross>0 else float('inf')
    rows.append((slug,cat,rate,n_bought,n_total,sigma_no,gross,edge_pct,fee_set,net,fee_over_edge,e['txs']))

# 链上实费(每事件抽样最多6个tx)
def actual(txs):
    txs=list(txs)[:6]
    tf=tn=0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for f,n in ex.map(tx_fee_notional,txs): tf+=f; tn+=n
    return (tf/tn*100) if tn else 0.0

for (slug,cat,rate,nb,nt,sno,gross,ep,fee,net,foe,txs) in rows:
    act=actual(txs)
    comp = f"{nb}/{nt}" if nt else f"{nb}/?"
    print(f"{slug[:42]:42} {cat[:6]:6} {rate:<5} {comp:7} {sno:7.3f} {gross*100:8.2f} {ep:6.1f}% {fee*100:7.3f} {net*100:8.2f} {(str(round(foe,1))+'%' if foe!=float('inf') else 'n/a'):>9} {act:8.4f}%")

# 汇总(只看买齐的干净套利篮子)
clean=[r for r in rows if r[4] and r[3]==r[4] and r[6]>0]
if clean:
    import statistics as st
    print(f"\n[买齐全腿的干净篮子 {len(clean)}/20]")
    print(f"  毛edge/套 中位 = {st.median(r[6] for r in clean)*100:.2f}¢  均值 {st.mean([r[6] for r in clean])*100:.2f}¢")
    print(f"  官费/套   中位 = {st.median(r[8] for r in clean)*100:.3f}¢")
    print(f"  官费吃掉毛edge 中位 = {st.median(r[10] for r in clean):.1f}%")
    net_pos=sum(1 for r in clean if r[9]>0)
    print(f"  扣官费后仍为正的篮子: {net_pos}/{len(clean)}")
