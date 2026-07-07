"""
深核:(a) 全量聚合 152 腿的 fee + 隐含价格分布;(b) dump 整笔 receipt 所有日志,
找 FeeCharged / 费用形态的 ERC1155 transfer,确认费不是在别处以股份收。
"""
import json, requests, time, sys
from concurrent.futures import ThreadPoolExecutor

SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
OF_PREFIXES = ("0xd543adfd","0xd0a08e8c")
# 已知事件 topic0
SIGS = {
 "0xd543adfd":"OrderFilled(A?)",
 "0xd0a08e8c":"OrderFilled(B?)",
 # ERC1155
 "0xc3d58168":"TransferSingle",
 "0x4a39dc06":"TransferBatch",
 # ERC20 Transfer
 "0xddf252ad":"ERC20Transfer",
 # CTF
 "0x2e6bcf1a":"PositionSplit?",
 "0x6f297f5e":"PositionsMerge?",
 "0x6cb0e615":"OrdersMatched?",
 "0x174b3811":"NegRiskOrdersMatched?",
 "0x63bf4d16":"OrdersMatched-legacy?",
}
RPCS=["https://polygon.drpc.org","https://polygon.api.onfinality.io/public","https://polygon-rpc.com"]
HDR={"Content-Type":"application/json","User-Agent":"Mozilla/5.0 audit"}

def topic_addr(t): return "0x"+t[-40:].lower()
def w(data,i):
    s=data[2:]; return int(s[i*64:(i+1)*64],16)
def get_receipt(tx):
    pl={"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":[tx]}
    for rpc in RPCS:
        for a in range(3):
            try:
                r=requests.post(rpc,json=pl,headers=HDR,timeout=30)
                if r.status_code!=200: time.sleep(0.4*(a+1)); continue
                res=r.json().get("result")
                if res: return res,rpc
                break
            except Exception: time.sleep(0.4*(a+1))
    return None,None

def all_legs(tx):
    rec,rpc=get_receipt(tx)
    if not rec: return None,None
    legs=[]
    for lg in rec.get("logs",[]):
        topics=lg.get("topics",[]);
        if not topics: continue
        t0=topics[0].lower()
        if any(t0.startswith(p) for p in OF_PREFIXES) and len(topics)>=4:
            if topic_addr(topics[3])!=SUBJECT: continue
            data=lg["data"]
            mk,tk=w(data,0),w(data,1)
            mamt,tamt,fee=w(data,2),w(data,3),w(data,4)
            legs.append({"mk":mk,"tk":tk,"mamt":mamt,"tamt":tamt,"fee":fee})
    return legs,rec

if __name__=="__main__":
    d=json.load(open('recent_activity.json'))
    buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No']
    buys.sort(key=lambda r:-r['timestamp'])
    seen={}
    for r in buys: seen.setdefault(r['transactionHash'],r)
    txs=list(seen.keys())[:120]

    # (a) 全量聚合
    tot_fee=0; tot_notional=0; n=0; pricebins={}
    price_examples=[]
    def proc(tx):
        legs,rec=all_legs(tx); return tx,legs
    with ThreadPoolExecutor(max_workers=12) as ex:
        results=list(ex.map(proc,txs))
    for tx,legs in results:
        if not legs: continue
        for lg in legs:
            # BUY-NO: 他付USDC(其中一边assetId=0)得NO token。隐含价 = usdc/tokens
            mk,tk,mamt,tamt,fee=lg['mk'],lg['tk'],lg['mamt'],lg['tamt'],lg['fee']
            if mk==0:   usdc,tok=mamt,tamt
            elif tk==0: usdc,tok=tamt,mamt
            else:       usdc,tok=None,None  # TOK<->TOK (merge式), 跳过价
            n+=1; tot_fee+=fee
            if usdc and tok:
                px=usdc/tok
                tot_notional+=usdc
                b=round(px,1); pricebins[b]=pricebins.get(b,0)+1
                if len(price_examples)<6 and 0.4<px<0.7:
                    price_examples.append((round(px,3),fee))
    print("=== 全量聚合(单位:1e-6 = micro-USDC/micro-token) ===")
    print(f"subject-taker legs: {n}")
    print(f"  sum(fee field)   : {tot_fee}  (legs with nonzero fee: counted below)")
    print(f"  sum(notional usdc microUSDC): {tot_notional}  = ${tot_notional/1e6:.2f}")
    print(f"  实测 fee/notional : {tot_fee/tot_notional if tot_notional else 0:.6%}")
    nz=sum(1 for tx,legs in results if legs for lg in legs if lg['fee']>0)
    print(f"  腿数 fee>0       : {nz} / {n}")
    print("  价格分布(隐含NO价 -> 腿数):", dict(sorted(pricebins.items())))
    print("  0.4-0.7 价区样例 (px,fee):", price_examples)

    # (b) dump 一笔有便宜腿的 tx 的所有日志事件
    print("\n=== 单笔全日志 dump(找FeeCharged/费用transfer) ===")
    target=None
    for tx,legs in results:
        if legs and any((lg['mk']==0 and 0.4< lg['mamt']/lg['tamt'] <0.7) or (lg['tk']==0 and 0.4< lg['tamt']/lg['mamt'] <0.7) for lg in legs):
            target=tx; break
    if not target: target=results[0][0]
    rec,rpc=get_receipt(target)
    print("TX",target,"rpc",rpc)
    from collections import Counter
    cnt=Counter()
    fee_like=[]
    for lg in rec["logs"]:
        topics=lg.get("topics",[]);
        if not topics: continue
        t0=topics[0].lower()[:10]
        name=SIGS.get(t0,"?")
        cnt[(t0,name,lg["address"][:10])]+=1
        # ERC1155 TransferSingle: topics[2]=from topics[3]=to; data=[id,value]
        if t0=="0xc3d58168":
            frm=topic_addr(topics[2]); to=topic_addr(topics[3])
            val=w(lg["data"],1)
            if frm==SUBJECT and to!=SUBJECT and val>0:
                fee_like.append(("1155 out",to[:12],val))
    for k,v in cnt.most_common():
        print(f"  x{v}  {k[0]} {k[1]:22} @{k[2]}")
    print("  他转出的1155(可能含费):",fee_like[:12])
