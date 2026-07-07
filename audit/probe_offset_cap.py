import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
def get(url,tries=5):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code,e.read().decode('utf-8','replace')[:100])
            if e.code in (502,503,500,429): time.sleep(1); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:100]); time.sleep(1)
    return last
# binary-search the offset cap with limit=1000 and with smaller limit
print("limit=1000:")
for off in [3000,3500,3900,3999,4000,4001,5000]:
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=1000&offset={off}")
    print(f"  offset={off:5} code={c} n={len(d) if isinstance(d,list) else d}")
print("limit=500 (can we reach deeper?):")
for off in [4000,4500,5000,9000]:
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=500&offset={off}")
    print(f"  offset={off:5} code={c} n={len(d) if isinstance(d,list) else d}")
print("limit=100:")
for off in [4000,5000,9000]:
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=100&offset={off}")
    print(f"  offset={off:5} code={c} n={len(d) if isinstance(d,list) else d}")
