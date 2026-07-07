"""
Independent live re-call of lb-api profit/volume and data-api value/traded.
Compares against D1/D2 captured by forensic agent. 1d/7d expected to drift
(rolling windows + time passed); all & 30d magnitude is the integrity anchor.
"""
import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
UA={"User-Agent":"Mozilla/5.0"}

def get(url,tries=6):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers=UA)
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (500,502,503,429,408): time.sleep(1+i); continue
            return e.code,e.read().decode('utf-8','replace')[:160]
        except Exception as e:
            time.sleep(1+i); last=("ERR",str(e)[:160])
    return last

LB="https://lb-api.polymarket.com"
DATA="https://data-api.polymarket.com"
agent={
 "profit":{"1d":-92.9176540349506,"7d":-17171.121621911945,"30d":86208.89174780372,"all":278513.58949265635},
 "volume":{"1d":44463.359241,"7d":934687.8806470002,"30d":6970942.165357001,"all":11830383.483070001},
 "value":6275.5053,"traded":17081,
}

print("=== PROFIT (lb-api /profit) ===")
liveprofit={}
for win in ["1d","7d","30d","all"]:
    c,d=get(f"{LB}/profit?window={win}&limit=1&address={W}")
    amt=None
    if c==200 and isinstance(d,list) and d: amt=d[0].get("amount")
    liveprofit[win]=amt
    a=agent["profit"][win]
    dlt = (amt-a) if (amt is not None) else None
    print(f"  {win:4} live={amt}  agent={a}  delta={dlt}")

print("\n=== VOLUME (lb-api /volume) ===")
livevol={}
for win in ["1d","7d","30d","all"]:
    c,d=get(f"{LB}/volume?window={win}&limit=1&address={W}")
    amt=None
    if c==200 and isinstance(d,list) and d: amt=d[0].get("amount")
    livevol[win]=amt
    a=agent["volume"][win]
    dlt=(amt-a) if amt is not None else None
    pct=(100*dlt/a) if (dlt is not None and a) else None
    print(f"  {win:4} live={amt}  agent={a}  delta={dlt}  ({pct:.3f}% )" if pct is not None else f"  {win:4} live={amt} agent={a}")

print("\n=== VALUE / TRADED (data-api) ===")
c,d=get(f"{DATA}/value?user={W}")
liveval=d[0].get("value") if (c==200 and isinstance(d,list) and d) else None
print(f"  value live={liveval}  agent={agent['value']}")
c,d=get(f"{DATA}/traded?user={W}")
livetraded=d.get("traded") if (c==200 and isinstance(d,dict)) else None
print(f"  traded live={livetraded}  agent={agent['traded']}")

json.dump({"profit":liveprofit,"volume":livevol,"value":liveval,"traded":livetraded},
          open(r"C:/Users/zexi/pmscan/audit/audit1_apirecall_out.json","w",encoding="utf-8"),indent=1)
print("\nwrote audit1_apirecall_out.json")
