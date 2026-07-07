import json, urllib.request, urllib.error, sys, time, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
def get(url,tries=4):
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (502,503,500,429): time.sleep(1); continue
            return e.code, e.read().decode('utf-8','replace')[:100]
        except Exception as e:
            time.sleep(1)
    return "ERR","retry exhausted"
# Binary search earliest activity ts using end= param: find smallest end that still returns data
# probe a few candidate dates
cands = {
 "2026-06-01":1748736000,"2026-05-01":1746057600,"2026-03-01":1740787200,
 "2026-01-01":1735689600,"2025-09-01":1756684800,"2025-06-01":1748736000-365*86400,
 "2025-01-01":1735689600-365*86400,"2024-06-01":1717200000,"2024-01-01":1704067200,
}
print("earliest-activity probe (end=date, limit=5, want oldest ts returned):")
for label,ts in sorted(cands.items(), key=lambda x:x[1]):
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&limit=5&end={ts}")
    if isinstance(d,list) and d:
        dts=[int(x['timestamp']) for x in d]
        old=min(dts)
        print(f"  end={label}({ts}) -> n={len(d)} oldest_ret={datetime.datetime.utcfromtimestamp(old)}")
    else:
        print(f"  end={label}({ts}) -> n=0/{str(d)[:50]}  (NO activity at/before this date)")
