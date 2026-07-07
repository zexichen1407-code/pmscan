import json, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed

SUBJECT="0x4f1d5ae26fc31472966e951af3183308736d8de2"
OM_TOPICS={"0x174b3811690657c217184f89418266767c87e4805d09680c39fc9c031c0cab7c",
 "0xa0be4ceb51b257c29c633330e760b79c7094cf821c145e735d785f51bce0dd9a"}
OM_PREFIX="0x63bf4d16"
OF_A="0xd543adfd"; OF_B="0xd0a08e8c"
EXCH={"0xe2222d279d744050d28e00520010520000310f59","0xe111180000d2663c0091e4f400237545b87b996b",
 "0xc5d563a36ae78145c45a50134d48a1215220f80a","0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"}
RPCS=["https://polygon.drpc.org","https://polygon.api.onfinality.io/public","https://polygon-rpc.com"]
HDR={"Content-Type":"application/json","User-Agent":"Mozilla/5.0 audit"}
ta=lambda t:"0x"+t[-40:].lower()

def receipt(tx):
    pl={"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":[tx]}
    for rpc in RPCS:
        for a in range(4):
            try:
                r=requests.post(rpc,json=pl,headers=HDR,timeout=35)
                if r.status_code!=200: time.sleep(0.4*(a+1)); continue
                res=r.json().get("result")
                if res: return res,rpc
                break
            except Exception: time.sleep(0.4*(a+1))
    return None,None

def decode(p):
    tx=p['tx']; rec,rpc=receipt(tx)
    if not rec: return {**p,"verdict":"UNDECIDABLE","reason":"no receipt"}
    toms=set(); of_makers=set(); exch=set(); nom=0
    for lg in rec.get("logs",[]):
        addr=lg["address"].lower(); tp=lg.get("topics",[])
        if not tp: continue
        t0=tp[0].lower()
        if ((t0 in OM_TOPICS) or t0.startswith(OM_PREFIX)) and addr in EXCH and len(tp)>=3:
            toms.add(ta(tp[2])); exch.add(addr); nom+=1
        if (t0.startswith(OF_A) or t0.startswith(OF_B)) and len(tp)>=3:
            of_makers.add(ta(tp[2]))
    if nom==0: return {**p,"verdict":"UNDECIDABLE","reason":"no OM log","rpc":rpc}
    if SUBJECT in toms:
        v="TAKER"
    else:
        v="MAKER"
    return {**p,"verdict":v,"om_toms":sorted(toms),"ext_toms":sorted(toms-{SUBJECT}),
            "subj_in_OF_maker":SUBJECT in of_makers,"n_om":nom,"exch":sorted(exch),"rpc":rpc}

picks=json.load(open('_sample_jun_late_404.json'))
res=[]
with ThreadPoolExecutor(max_workers=14) as ex:
    futs=[ex.submit(decode,p) for p in picks]
    for f in as_completed(futs): res.append(f.result())
json.dump(res, open('sample3mo_404.json','w'), indent=1)
from collections import Counter
c=Counter(r['verdict'] for r in res)
print("decoded",len(res),"->",dict(c))
# undecidable retry note
und=[r['tx'] for r in res if r['verdict']=='UNDECIDABLE']
print("undecidable", len(und))
# maker hardening check
mk=[r for r in res if r['verdict']=='MAKER']
print("makers", len(mk), "with subj_in_OF_maker", sum(1 for r in mk if r.get('subj_in_OF_maker')))
print("makers where tom==subject (should be 0):", sum(1 for r in mk if SUBJECT in r.get('om_toms',[])))
