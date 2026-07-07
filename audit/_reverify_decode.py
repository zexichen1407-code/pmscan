import json, requests, time
from concurrent.futures import ThreadPoolExecutor

SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
OM_TOPICS = {
 "0x174b3811690657c217184f89418266767c87e4805d09680c39fc9c031c0cab7c",
 "0xa0be4ceb51b257c29c633330e760b79c7094cf821c145e735d785f51bce0dd9a",
}
OM_PREFIX = "0x63bf4d16"  # legacy variant prefix
OF_TOPICS = {
 "0xd543adfd992eef220e3f6204169b86c0b3a4b14b7444cd62e6ec5f3e7d28d3eb".lower(),  # placeholder; we match by prefix below
}
OF_PREFIX_A = "0xd543adfd"
OF_PREFIX_B = "0xd0a08e8c"
EXCHANGES = {
 "0xe2222d279d744050d28e00520010520000310f59",
 "0xe111180000d2663c0091e4f400237545b87b996b",
 "0xc5d563a36ae78145c45a50134d48a1215220f80a",
 "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
}
RPCS = ["https://polygon.drpc.org","https://polygon.api.onfinality.io/public","https://polygon-rpc.com"]
HDR = {"Content-Type":"application/json","User-Agent":"Mozilla/5.0 audit"}

def topic_addr(t):
    return "0x"+t[-40:].lower()

def get_receipt(tx):
    payload={"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":[tx]}
    for rpc in RPCS:
        for attempt in range(3):
            try:
                r=requests.post(rpc,json=payload,headers=HDR,timeout=30)
                if r.status_code!=200:
                    time.sleep(0.5*(attempt+1)); continue
                j=r.json()
                res=j.get("result")
                if res: return res, rpc
                # null result -> pruned, try next rpc
                break
            except Exception:
                time.sleep(0.5*(attempt+1))
    return None, None

def decode(tx):
    rec,rpc=get_receipt(tx)
    if not rec:
        return {"tx":tx,"verdict":"UNDECIDABLE","reason":"no receipt"}
    logs=rec.get("logs",[])
    om_hits=[]
    of_makers=set(); of_takers=set()
    for lg in logs:
        addr=lg["address"].lower()
        topics=lg.get("topics",[])
        if not topics: continue
        t0=topics[0].lower()
        is_om = (t0 in OM_TOPICS) or t0.startswith(OM_PREFIX)
        if is_om and addr in EXCHANGES and len(topics)>=3:
            om_hits.append({"addr":addr,"t0":t0,"tom":topic_addr(topics[2]),"maker":topic_addr(topics[1]) if len(topics)>1 else None})
        if t0.startswith(OF_PREFIX_A) or t0.startswith(OF_PREFIX_B):
            # OrderFilled: topics[1]=orderHash, topics[2]=maker, topics[3]=taker (indexed maker/taker on PM)
            if len(topics)>=3: of_makers.add(topic_addr(topics[2]))
            if len(topics)>=4: of_takers.add(topic_addr(topics[3]))
    if not om_hits:
        return {"tx":tx,"verdict":"UNDECIDABLE","reason":"no OM log","rpc":rpc,"nlogs":len(logs)}
    toms={h["tom"] for h in om_hits}
    subj_is_tom = SUBJECT in toms
    verdict = "TAKER" if subj_is_tom else "MAKER"
    # for maker, the reported tom is the external taker; collect
    ext_toms = sorted(toms - {SUBJECT})
    subj_in_of_maker = SUBJECT in of_makers
    return {"tx":tx,"verdict":verdict,"om_toms":sorted(toms),"ext_toms":ext_toms,
            "om_exch":sorted({h['addr'] for h in om_hits}),"n_om":len(om_hits),
            "subj_in_OF_maker":subj_in_of_maker,"of_makers_n":len(of_makers),"rpc":rpc}

picks=json.load(open('_reverify_picks.json'))
out={}
with ThreadPoolExecutor(max_workers=12) as ex:
    futs={ex.submit(decode,p['tx']):p for p in picks}
    for f in futs:
        p=futs[f]; res=f.result(); res['claim']=p; out[p['tx']]=res

json.dump(out, open('_reverify_out.json','w'), indent=1)

# compare
print(f"{'BAND':10} {'CLAIM':6} {'MINE':12} {'MATCH':5}  TX")
agree=0; total=0
for p in picks:
    r=out[p['tx']]; total+=1
    claim=p['verdict']; mine=r['verdict']
    # role match
    role_ok = claim==mine
    # address match
    addr_ok=True; detail=""
    if claim=='TAKER':
        addr_ok = SUBJECT in r.get('om_toms',[])
    else:
        # claimed external tom must appear in om_toms and NOT be subject
        ct=(p['claimed_tom'] or '').lower()
        addr_ok = ct in r.get('om_toms',[]) and ct!=SUBJECT
        detail = f"subj_in_OF_maker={r.get('subj_in_OF_maker')}"
    ok = role_ok and addr_ok
    if ok: agree+=1
    print(f"{p['band']:10} {claim:6} {mine:12} {str(ok):5}  {p['tx'][:14]} {detail}")
print(f"\nAGREE {agree}/{total}")
