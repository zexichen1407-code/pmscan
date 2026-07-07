import json, requests, time

SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2".lower()
NEGRISK = "0xe2222d279d744050d28e00520010520000310f59".lower()
STDV2   = "0xe111180000d2663c0091e4f400237545b87b996b".lower()
T_LEGACY = "0xd0a08e8c"  # bytes32,addr,addr,uint256x5  -> 5 data words, fee idx4 (last)
T_NEW    = "0xd543adfd"  # bytes32,addr,addr,uint8,uint256,uint256,uint256,uint256,bytes32,bytes32 -> 7 data words
USDC_TRANSFER = "0xddf252ad"

RPCS = ["https://polygon.drpc.org","https://polygon-rpc.com","https://polygon.api.onfinality.io/public"]
HDR = {"User-Agent":"Mozilla/5.0 audit","Content-Type":"application/json"}

def rpc(method, params):
    last=None
    for url in RPCS:
        try:
            r=requests.post(url, headers=HDR, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=25)
            j=r.json()
            if 'result' in j and j['result'] is not None:
                return j['result']
            last=j
        except Exception as e:
            last=str(e)
        time.sleep(0.3)
    raise RuntimeError(f"rpc fail {method}: {last}")

def w(data,i):  # 32-byte word i as int
    h=data[2:]
    return int(h[i*64:(i+1)*64],16)

txs = [
 # tx, slug, side, outcome, price, size, usdcSize, conditionId
 ("0x90dca1f5ad0cd342991f5b06cb7c8116be84ba002025913926513f10e90346ae","beijing-28c","BUY","No",0.78,5.03,3.96655),
 ("0xad1543692d95d6e59ec79abd6a0f256d0a7874bf7e46cf6237386cbad100ad89","beijing-29c (p~0.53 high-fee)","BUY","No",0.53,5.0,2.71227),
 ("0x88638f0cc9aefa31009c456a7ed167508f68c35c41f52c721aa1f826cda65e09","beijing-31c","BUY","No",0.974,5.03,4.90558),
]

for tx,label,side,outc,price,size,usd in txs:
    print("="*80)
    print(f"TX {tx}\n  {label}  side={side} {outc} price={price} size={size} usdc={usd}")
    rec=rpc("eth_getTransactionReceipt",[tx])
    logs=rec["logs"]
    of=[]
    for lg in logs:
        t0=lg["topics"][0].lower()
        if t0.startswith(T_LEGACY) or t0.startswith(T_NEW):
            of.append(lg)
    print(f"  #logs={len(logs)}  #OrderFilled={len(of)}")
    for lg in of:
        addr=lg["address"].lower()
        t0=lg["topics"][0].lower()
        ver = "NEGRISK" if addr==NEGRISK else ("STD-V2" if addr==STDV2 else addr)
        topic_kind = "LEGACY(d0a08e8c)" if t0.startswith(T_LEGACY) else ("NEW(d543adfd)" if t0.startswith(T_NEW) else t0[:10])
        maker = "0x"+lg["topics"][2][-40:]
        taker = "0x"+lg["topics"][3][-40:]
        data=lg["data"]
        nwords=(len(data)-2)//64
        words=[w(data,i) for i in range(nwords)]
        is_subj_taker = taker.lower()==SUBJECT
        is_subj_maker = maker.lower()==SUBJECT
        print(f"  --- exch={ver} topic={topic_kind} nwords={nwords}")
        print(f"      maker={maker} {'<=SUBJ' if is_subj_maker else ''}")
        print(f"      taker={taker} {'<=SUBJ' if is_subj_taker else ''}")
        # print all words raw + scaled by 1e6
        for i,val in enumerate(words):
            print(f"      word[{i}] = {val}  (/1e6={val/1e6:.6f})")
        # fee candidate index 4
        if nwords>=5:
            print(f"      >>> fee@idx4 = {words[4]}  (/1e6 = {words[4]/1e6:.8f})")
