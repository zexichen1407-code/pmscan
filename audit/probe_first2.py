import json, urllib.request, urllib.error, sys, time, datetime, calendar
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
def get(url,tries=4):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=60) as r: return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (502,503,500,429): time.sleep(1); continue
            return e.code, e.read().decode('utf-8','replace')[:80]
        except Exception: time.sleep(1)
    return "ERR","x"
def u(y,m,d): return calendar.timegm((y,m,d,0,0,0,0,0,0))
cands=[(y,m) for y in (2025,2026) for m in range(1,13)]
print("correct end= date probe (oldest ts returned at/before each date):")
for y,m in cands:
    ts=u(y,m,1)
    if ts>time.time(): continue
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=3&end={ts}")
    if isinstance(d,list) and d:
        old=min(int(x['timestamp']) for x in d)
        print(f"  end={y}-{m:02d}-01 -> n={len(d)} oldest={datetime.datetime.utcfromtimestamp(old)}")
    else:
        print(f"  end={y}-{m:02d}-01 -> EMPTY (no activity before this)")
