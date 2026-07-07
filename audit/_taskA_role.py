import json, requests, time
SUBJECT="0x4f1d5ae26fc31472966e951af3183308736d8de2".lower()
NEGRISK="0xe2222d279d744050d28e00520010520000310f59".lower()
HDR={"User-Agent":"Mozilla/5.0 audit"}

# Use data-api to see how Polymarket labels these fills (maker/taker side)
# trades endpoint per market(conditionId)
cids = {
 "beijing-28c":"0x3cc4a0a1fe88ad8ac20cfddd7117fcda7ef99e5ca6cb58bd159e326ae27ad2d3",
 "beijing-29c":"0xfd8c5c3ff6a609c38cb12aaa50052e92b7c0e9342c37038c3a225d9884ab94fa",
}
for label,cid in cids.items():
    url=f"https://data-api.polymarket.com/trades?market={cid}&limit=50"
    try:
        r=requests.get(url,headers=HDR,timeout=20)
        j=r.json()
    except Exception as e:
        print(label,"ERR",e); continue
    print("="*70); print(label, cid, "ntrades=",len(j) if isinstance(j,list) else j)
    if isinstance(j,list):
        for t in j[:6]:
            keys=[k for k in t.keys()]
            print("  keys:",keys); 
            print("  ",{k:t.get(k) for k in ['proxyWallet','side','size','price','outcome','transactionHash'] if k in t})
            break
        # find subject trades
        subj=[t for t in j if str(t.get('proxyWallet','')).lower()==SUBJECT]
        print("  subject trades here:",len(subj))
        for t in subj[:3]:
            print("   ",{k:t.get(k) for k in ['side','size','price','outcome','transactionHash']})
