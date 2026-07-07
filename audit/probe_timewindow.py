import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
def get(url,tries=4):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code,e.read().decode('utf-8','replace')[:140])
            if e.code in (502,503,500,429): time.sleep(1); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:120]); time.sleep(1)
    return last

# the 4000th-most-recent record's timestamp = oldest we have. Try start/end to page by time.
import os
acts=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"raw_activity.json"),encoding="utf-8"))
tss=[int(a['timestamp']) for a in acts if a.get('timestamp')]
oldest=min(tss); newest=max(tss)
print(f"current pull ts range: {oldest} .. {newest}")
import datetime
print("  =", datetime.datetime.utcfromtimestamp(oldest), "..", datetime.datetime.utcfromtimestamp(newest))

# Does /activity accept start / end (unix secs)? try fetching window OLDER than oldest
print("\n/activity time params test (window older than current pull):")
older_end = oldest  # ask for stuff before our oldest
for params in [f"limit=10&end={older_end}",
               f"limit=10&start=0&end={older_end}",
               f"limit=10&before={older_end}",
               f"limit=10&endTime={older_end}",
               f"limit=10&startTs=0&endTs={older_end}"]:
    c,d=get(f"https://data-api.polymarket.com/activity?user={W}&{params}")
    if isinstance(d,list) and d:
        dts=[int(x['timestamp']) for x in d]
        info=f"n={len(d)} ts {min(dts)}..{max(dts)}"
    else:
        info=str(d)[:80] if not isinstance(d,list) else f"n={len(d)}"
    print(f"  [{params:28}] code={c} {info}")

# CLOB trades endpoint?
print("\nalt endpoints:")
for url in [f"https://data-api.polymarket.com/trades?user={W}&limit=5",
            f"https://data-api.polymarket.com/positions?user={W}&limit=5",
            f"https://data-api.polymarket.com/holdings?user={W}",
            f"https://clob.polymarket.com/trades?maker={W}&limit=5"]:
    c,d=get(url)
    info=f"n={len(d)}" if isinstance(d,list) else str(d)[:100]
    print(f"  {url[:55]:55} code={c} {info}")
