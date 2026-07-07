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
            last=(e.code,e.read().decode('utf-8','replace')[:120])
            if e.code in (502,503,500,429): time.sleep(1); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:100]); time.sleep(1)
    return last

# Does end + offset work together to get deep windows? and does offset cap still apply within an end-window?
end=1782252472
print("end-window + offset behavior:")
for off in [0,1000,2000,3000,3500]:
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=1000&offset={off}&end={end}")
    if isinstance(d,list) and d:
        dts=[int(x['timestamp']) for x in d]
        info=f"n={len(d)} ts {min(dts)}..{max(dts)}"
    else: info=str(d)[:90]
    print(f"  off={off:5} code={c} {info}")

# /trades schema
print("\n/trades schema:")
c,d=get(f"https://data-api.polymarket.com/trades?user={W}&limit=3")
print("code",c)
if isinstance(d,list) and d:
    print("keys:",sorted(d[0].keys()))
    print("sample:",json.dumps(d[0],ensure_ascii=False)[:400])
# does /trades have offset cap too / how deep?
c,d=get(f"https://data-api.polymarket.com/trades?user={W}&limit=1000&offset=3500")
print("/trades offset=3500:",c, len(d) if isinstance(d,list) else str(d)[:80])
