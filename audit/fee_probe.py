"""
查实 V2 NegRiskCtfExchange 上 0xp3nny 的真实 taker 费率。
直接读链上 OrderFilled.fee 字段,不依赖文档口径。
"""
import json, requests, time, sys
from concurrent.futures import ThreadPoolExecutor

SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
NEGRISK_EXCH = "0xe2222d279d744050d28e00520010520000310f59"  # NegRiskCtfExchange V2
STD_EXCH     = "0xe111180000d2663c0091e4f400237545b87b996b"  # 标准 CTF V2
OF_PREFIXES  = ("0xd543adfd", "0xd0a08e8c")  # OrderFilled topic0 两种版本
RPCS = ["https://polygon.drpc.org","https://polygon.api.onfinality.io/public","https://polygon-rpc.com"]
HDR  = {"Content-Type":"application/json","User-Agent":"Mozilla/5.0 audit"}

def topic_addr(t): return "0x"+t[-40:].lower()
def w(data, i):    # i-th 32-byte word of data hex (data starts with 0x)
    s = data[2:]
    return int(s[i*64:(i+1)*64], 16)

def get_receipt(tx):
    payload={"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":[tx]}
    for rpc in RPCS:
        for attempt in range(3):
            try:
                r=requests.post(rpc,json=payload,headers=HDR,timeout=30)
                if r.status_code!=200: time.sleep(0.4*(attempt+1)); continue
                res=r.json().get("result")
                if res: return res, rpc
                break
            except Exception: time.sleep(0.4*(attempt+1))
    return None, None

def decode_tx(tx):
    rec,rpc=get_receipt(tx)
    if not rec: return {"tx":tx,"ok":False,"reason":"no receipt"}
    legs=[]
    for lg in rec.get("logs",[]):
        addr=lg["address"].lower(); topics=lg.get("topics",[])
        if not topics: continue
        t0=topics[0].lower()
        if not any(t0.startswith(p) for p in OF_PREFIXES): continue
        if len(topics)<4: continue
        maker=topic_addr(topics[2]); taker=topic_addr(topics[3])
        if taker!=SUBJECT: continue  # 只要他做 taker 的腿
        data=lg["data"]
        legs.append({
            "exch":addr,
            "maker":maker,
            "makerAssetId":w(data,0),
            "takerAssetId":w(data,1),
            "makerAmt":w(data,2),
            "takerAmt":w(data,3),
            "fee":w(data,4),
        })
    return {"tx":tx,"ok":True,"rpc":rpc,"legs":legs,"nlegs":len(legs)}

if __name__=="__main__":
    # 取最近 BUY-No 成交的 txHash
    d=json.load(open('recent_activity.json'))
    buys=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No']
    buys.sort(key=lambda r:-r['timestamp'])
    # 按 tx 去重,保留每个 tx 的 data-api 价格信息(篮子里取第一笔)
    seen={}
    for r in buys:
        tx=r['transactionHash']
        seen.setdefault(tx, r)
    txs=list(seen.keys())[:120]
    print(f"decoding {len(txs)} recent BUY-No txs ...", file=sys.stderr)
    out=[]
    with ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(decode_tx, txs):
            res['api'] = {k:seen[res['tx']][k] for k in ['timestamp','price','size','usdcSize','outcome','slug']}
            out.append(res)
    json.dump(out, open('fee_probe_out.json','w'), indent=1)
    ok=[r for r in out if r['ok']]
    nlegs=sum(r['nlegs'] for r in ok)
    print(f"decoded {len(ok)}/{len(out)} txs, {nlegs} subject-taker legs", file=sys.stderr)
    # 先 verbose 打 8 个有腿的 tx
    shown=0
    for r in ok:
        if not r['legs']: continue
        a=r['api']
        print(f"\nTX {r['tx'][:18]} slug={a['slug'][:42]} apiPrice={a['price']} apiUsdc={a['usdcSize']}")
        for lg in r['legs'][:4]:
            print(f"   exch={lg['exch'][:10]} makerAsset={'0' if lg['makerAssetId']==0 else 'TOK'} "
                  f"takerAsset={'0' if lg['takerAssetId']==0 else 'TOK'} "
                  f"makerAmt={lg['makerAmt']} takerAmt={lg['takerAmt']} fee={lg['fee']}")
        shown+=1
        if shown>=8: break
