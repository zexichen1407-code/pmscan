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
            return e.code,"http"
        except Exception: time.sleep(1)
    return "ERR","x"
# narrow within April: probe daily end through 2026-04
for d in range(1,31,2):
    ts=calendar.timegm((2026,4,d,0,0,0,0,0,0))
    c,r=get(f"https://data-api.polymarket.com/activity?user={W}&limit=3&end={ts}")
    if isinstance(r,list) and r:
        old=min(int(x['timestamp']) for x in r)
        print(f"  end=2026-04-{d:02d} -> n={len(r)} oldest={datetime.datetime.utcfromtimestamp(old)}")
    else:
        print(f"  end=2026-04-{d:02d} -> EMPTY")
