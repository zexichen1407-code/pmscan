"""
回答"如何得知他不付taker费":完全脱离 OrderFilled.fee 字段的独立证明。
重建每笔 BUY 的 [付出抵押物 pUSD] 与 [收到 outcome 股数],算有效单价 = 付/收。
任何形式的费(扣钱 or 扣股)都会让 有效单价 > 报价。
对照:他的 neg-risk 买单 vs 二元盘的 taker 买单。
"""
import json, requests, sys
from concurrent.futures import ThreadPoolExecutor

SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'
HDR={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
ERC20='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
T1155_SINGLE='0xc3d58168c5aedeaf4f5db1de9e1ed3e9e3d8e9b18a91e9e0e3f1b...'  # 用前缀匹配
SINGLE_PFX='0xc3d58168'
BATCH_PFX='0x4a39dc06'
# 10-char 前缀(从链上验证实测: pUSD=0xc011a7e1, USDC.e=0x2791bca1, USDC=0x3c499c54)
STABLE_ADDRS={'0xc011a7e1','0x2791bca1','0x3c499c54'}
def ta(t): return '0x'+t[-40:].lower()
def receipt(tx):
    pl={'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[tx]}
    for rpc in RPCS:
        try:
            r=requests.post(rpc,json=pl,headers=HDR,timeout=20)
            if r.status_code==200 and r.json().get('result'): return r.json()['result']
        except Exception: pass
    return None

def reconstruct(tx, who):
    """who=taker地址。返回 付出pUSD, 收到股数, 付出股数(被抽?), fee字段合计"""
    rec=receipt(tx)
    if not rec: return None
    paid=0.0; got=0.0; gave_shares=0.0; feefield=0
    for lg in rec['logs']:
        tp=lg.get('topics',[]);
        if not tp: continue
        t0=tp[0].lower()
        # 抵押物 ERC20
        if t0==ERC20 and len(tp)>=3 and lg['address'][:10].lower() in STABLE_ADDRS:
            frm=ta(tp[1]); to=ta(tp[2]); val=int(lg['data'],16)/1e6
            if frm==who: paid+=val
            # 找零/退款(to==who)很少,这里只算净付:稍后 paid-refund
            if to==who: paid-=val
        # ERC1155 单笔
        elif t0.startswith(SINGLE_PFX) and len(tp)>=4:
            frm=ta(tp[2]); to=ta(tp[3])
            # data=[id, value]
            val=int(lg['data'][2+64:2+128],16)/1e6
            if to==who: got+=val
            if frm==who: gave_shares+=val
        # ERC1155 批量: data=[idsOffset, valsOffset, ...arrays]
        elif t0.startswith(BATCH_PFX) and len(tp)>=4:
            frm=ta(tp[2]); to=ta(tp[3])
            s=lg['data'][2:]
            try:
                # 解析 values 数组(第二个动态数组)
                vals_off=int(s[64:128],16)*2
                n=int(s[vals_off:vals_off+64],16)
                tot=0
                for i in range(n):
                    tot+=int(s[vals_off+64+i*64: vals_off+128+i*64],16)
                tot/=1e6
                if to==who: got+=tot
                if frm==who: gave_shares+=tot
            except Exception: pass
        # OrderFilled fee 字段(仅作旁证)
        elif (t0.startswith('0xd543adfd') or t0.startswith('0xd0a08e8c')) and len(tp)>=4:
            if ta(tp[3])==who:
                feefield+=int(lg['data'][2+256:2+320],16)
    return paid, got, gave_shares, feefield

def analyze(label, items):
    print(f"\n=== {label} ===")
    print(f"{'tx':18} {'报价':6} {'付pUSD$':9} {'收股数':9} {'被抽股':7} {'有效价':7} {'有效/报价':9} {'fee字段':8}")
    for tx, who, px in items:
        r=reconstruct(tx, who)
        if not r: print(f"{tx[:18]} no receipt"); continue
        paid,got,gave,ff=r
        eff = paid/got if got else 0
        ratio = eff/px if px else 0
        flag = '  <= 有费!' if ratio>1.003 else ''
        print(f"{tx[:18]} {px:6.3f} {paid:9.3f} {got:9.3f} {gave:7.3f} {eff:7.4f} {ratio:8.4f}x {ff:8}{flag}")

# ---- 他的 neg-risk 买单 ----
d=json.load(open('recent_activity.json'))
buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No'
      and r.get('proxyWallet','').lower()==SUB]
buys.sort(key=lambda r:-r['timestamp'])
# 选不同价位的单腿tx(去重tx)
seen={}; his=[]
for r in buys:
    tx=r['transactionHash']
    if tx in seen: continue
    seen[tx]=1
    his.append((tx, SUB, float(r['price'])))
    if len(his)>=8: break
analyze("他的 neg-risk BUY-No(走 NegRiskCtfExchange)", his)

# ---- 二元盘 taker 买单(对照,应出现费) ----
g=requests.get('https://gamma-api.polymarket.com/markets?closed=false&limit=60&order=volume24hr&ascending=false',headers={'User-Agent':'Mozilla/5.0'},timeout=20).json()
binitems=[]
for m in g:
    if m.get('negRisk') is not False or not m.get('conditionId'): continue
    cid=m['conditionId']
    try:
        tr=requests.get(f'https://data-api.polymarket.com/trades?market={cid}&takerOnly=true&limit=60',headers={'User-Agent':'Mozilla/5.0'},timeout=20).json()
    except Exception: continue
    if not isinstance(tr,list): continue
    for t in tr:
        if t.get('side')!='BUY': continue
        tx=t.get('transactionHash'); who=(t.get('proxyWallet') or '').lower(); px=float(t.get('price',0))
        if tx and who and 0.1<px<0.9:
            binitems.append((tx,who,px))
        if len(binitems)>=8: break
    if len(binitems)>=8: break
analyze("二元盘 taker BUY 对照(应出现 有效价>报价 = 有费)", binitems)
