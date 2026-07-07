"""
META-AUDIT third-party (NON-Polymarket) cross-verification via Polygonscan/Etherscan v2.
Goal: break the same-source loop. Confirm independently that this address exists
on Polygon, has tx volume of the right order of magnitude, and interacts with
Polymarket contracts (CTF Exchange / NegRiskAdapter / USDC).
Free tier, no key -> may rate-limit. Try multiple endpoints.
"""
import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
USDC="0x2791bca1f2de4661ed88a30c99a7a9449aa84174"  # USDC.e on Polygon
# Polymarket key contracts on Polygon:
CTF="0x4d97dcd97ec945f40cf65f87097ace5ea0476045"          # Conditional Tokens Framework
CTF_EXCHANGE="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"  # CTF Exchange
NEGRISK_ADAPTER="0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"
NEGRISK_EXCHANGE="0xc5d563a36ae78145c45a50134d48a1215220f80a"

def get(url,tries=4):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=45) as r:
                return r.getcode(), json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code, e.read().decode('utf-8','replace')[:200]); time.sleep(1+i)
        except Exception as e:
            last=("ERR", str(e)[:200]); time.sleep(1+i)
    return last

ENDPOINTS=[
  ("etherscan_v2_polygon", "https://api.etherscan.io/v2/api?chainid=137"),
  ("polygonscan_legacy",   "https://api.polygonscan.com/api"),
]

results={}
for name,base in ENDPOINTS:
    print(f"\n########## {name} ##########")
    # 1) native tx count (proxy: balance call always works, txlist tells tx count)
    url=f"{base}&module=account&action=txlist&address={W}&startblock=0&endblock=99999999&page=1&offset=20&sort=desc&apikey=YourApiKeyToken"
    c,d=get(url)
    print(f"[txlist] code={c}")
    if isinstance(d,dict):
        print(f"  status={d.get('status')} message={d.get('message')}")
        res=d.get('result')
        if isinstance(res,list):
            print(f"  returned {len(res)} txs (page1 offset20)")
            results[name+"_txlist_n"]=len(res)
            for t in res[:5]:
                print(f"    blk={t.get('blockNumber')} to={t.get('to')} method={t.get('methodId')} val={t.get('value')}")
        else:
            print(f"  result={str(res)[:200]}")
    else:
        print(f"  raw={str(d)[:200]}")
    time.sleep(2)
    # 2) USDC token transfers (count + counterparties)
    url=f"{base}&module=account&action=tokentx&contractaddress={USDC}&address={W}&page=1&offset=100&sort=desc&apikey=YourApiKeyToken"
    c,d=get(url)
    print(f"[USDC tokentx] code={c}")
    if isinstance(d,dict) and isinstance(d.get('result'),list):
        res=d['result']
        print(f"  returned {len(res)} USDC transfers (capped at offset100)")
        results[name+"_usdc_n"]=len(res)
        cps={}
        for t in res:
            for addr in (t.get('from'),t.get('to')):
                if addr and addr.lower()!=W.lower():
                    cps[addr.lower()]=cps.get(addr.lower(),0)+1
        top=sorted(cps.items(),key=lambda x:-x[1])[:8]
        print("  top USDC counterparties:")
        known={CTF:'CTF',CTF_EXCHANGE:'CTF_EXCHANGE',NEGRISK_ADAPTER:'NEGRISK_ADAPTER',NEGRISK_EXCHANGE:'NEGRISK_EXCHANGE'}
        for a,n in top:
            tag=known.get(a,'')
            print(f"    {a} x{n} {tag}")
    else:
        print(f"  status={d.get('status') if isinstance(d,dict) else '?'} msg={d.get('message') if isinstance(d,dict) else d}")
        print(f"  result={str(d.get('result'))[:200] if isinstance(d,dict) else str(d)[:200]}")
    time.sleep(2)

json.dump(results,open("meta_onchain_out.json","w"),indent=1)
print("\nwrote meta_onchain_out.json")
print("\nNOTE: free-tier no-key calls often return 'Max rate limit reached' or NOTOK.")
