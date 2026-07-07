import json, requests, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
ORDERS_MATCHED = {
    "0x174b3811690657c217184f89418266767c87e4805d09680c39fc9c031c0cab7c",
    "0xa0be4ceb51b257c29c633330e760b79c7094cf821c145e735d785f51bce0dd9a",
}
ORDER_FILLED = {
    "0xd543adfd388dcccdba5f0e77fa55375db5c0f8c5cd6b62ef8474e5cebbe99e9",  # canonical OrderFilled
    "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6",
}
EXCHANGES = {
    "0xe2222d279d744050d28e00520010520000310f59",
    "0xe111180000d2663c0091e4f400237545b87b996b",
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",
}
EXCHANGES_L = {e.lower() for e in EXCHANGES}

RPCS = [
    "https://polygon.drpc.org",
    "https://polygon.api.onfinality.io/public",
    "https://polygon-rpc.com",
]

def topic_addr(t):
    return "0x" + t[-40:].lower()

def get_receipt(tx):
    payload = {"jsonrpc":"2.0","id":1,"method":"eth_getTransactionReceipt","params":[tx]}
    last_err=None
    for attempt in range(2):
        for rpc in RPCS:
            try:
                r = requests.post(rpc, json=payload, timeout=25)
                if r.status_code!=200:
                    last_err=f"http{r.status_code}@{rpc}"; continue
                j = r.json()
                if j.get("result") is not None:
                    return j["result"], None
                last_err=f"null@{rpc}"
            except Exception as e:
                last_err=f"{type(e).__name__}@{rpc}"
    return None, last_err

def decode(tx):
    rec, err = get_receipt(tx)
    if rec is None:
        return {"tx":tx,"verdict":"UNDECIDABLE","err":err}
    logs = rec.get("logs",[])
    om_logs=[]
    for lg in logs:
        topics = lg.get("topics",[])
        if not topics: continue
        t0 = topics[0].lower()
        addr = lg.get("address","").lower()
        if t0 in ORDERS_MATCHED and addr in EXCHANGES_L:
            om_logs.append(lg)
    # fallback: any OrdersMatched topic on exchange even if topic set unknown? keep strict to known set + exchange
    if not om_logs:
        # fallback2: OrdersMatched topic0 from known set regardless of address
        for lg in logs:
            topics=lg.get("topics",[])
            if topics and topics[0].lower() in ORDERS_MATCHED:
                om_logs.append(lg)
    if not om_logs:
        return {"tx":tx,"verdict":"UNDECIDABLE","err":"no_OrdersMatched"}
    # decode takerOrderMaker = topics[2]
    results=[]
    for lg in om_logs:
        topics=lg.get("topics",[])
        if len(topics)<3:
            continue
        tom = topic_addr(topics[2])
        results.append(tom)
    if not results:
        return {"tx":tx,"verdict":"UNDECIDABLE","err":"OM_no_topic2"}
    # if any OM has takerOrderMaker == subject => he was the taker (aggressor)
    is_taker = any(r==SUBJECT for r in results)
    if is_taker:
        return {"tx":tx,"verdict":"TAKER","takerOrderMaker":SUBJECT,"counterparties":results}
    # else subject was resting maker; counterparty = external taker (the takerOrderMaker addr)
    ext = results[0]
    # confirm subject appears in an OrderFilled maker slot with external taker (not exchange)
    return {"tx":tx,"verdict":"MAKER","takerOrderMaker":ext,"counterparties":results}

def main():
    sample = json.load(open(r"C:\Users\zexi\pmscan\audit\sample_list_303.json"))
    meta = {p["tx"]:p for p in sample}
    out=[]
    with ThreadPoolExecutor(max_workers=14) as ex:
        futs={ex.submit(decode,p["tx"]):p["tx"] for p in sample}
        for fu in as_completed(futs):
            r=fu.result()
            m=meta[r["tx"]]
            r["day"]=m["day"]; r["ts"]=m["ts"]; r["slug"]=m["slug"]
            out.append(r)
    json.dump(out,open(r"C:\Users\zexi\pmscan\audit\sample3mo_303.json","w"),indent=1)
    from collections import Counter
    c=Counter(r["verdict"] for r in out)
    print("sampled",len(out),dict(c))

if __name__=="__main__":
    main()
