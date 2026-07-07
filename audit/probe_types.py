import json, urllib.request, urllib.error, sys, time
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
def get(url, tries=3):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.getcode(), json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code,"http"); time.sleep(0.8)
        except Exception as e:
            last=("ERR",str(e)[:80]); time.sleep(0.8)
    return last

# 1) does unfiltered activity contain REWARD rows? sample 1000
c,d = get(f"https://data-api.polymarket.com/activity?user={W}&limit=1000")
print("unfiltered limit=1000 code",c,"n",len(d) if isinstance(d,list) else d)
if isinstance(d,list):
    print("type counts in first 1000:", dict(Counter(a.get('type') for a in d)))

# 2) per-type filter availability (single call each, no offset)
print("\nper-type filter (limit=5, no offset):")
for t in ["TRADE","REWARD","MERGE","SPLIT","CONVERSION","REDEEM"]:
    c,d = get(f"https://data-api.polymarket.com/activity?user={W}&type={t}&limit=5")
    print(f"  {t:12} code={c} n={len(d) if isinstance(d,list) else d}")
