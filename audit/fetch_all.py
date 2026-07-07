"""
p3nny forensic fetcher  (wallet 0x4f1d5ae26fc31472966e951af3183308736d8de2)
Independently re-fetches all Polymarket public-API data and dumps raw JSON to audit/.

KEY METHOD DECISIONS (verified by probing, see report):
- lb-api profit/volume: one call per window, read [0].amount.
- /activity type= filter is currently 502-flaky server-side, so we DO NOT rely on it.
  Instead we paginate the UNFILTERED feed with limit=1000 & offset, which returns
  distinct, time-descending pages (verified overlap=0). We break down by type locally.
- offset pagination is verified to work on the unfiltered feed.
- 502s are retried with backoff.
"""
import json, urllib.request, urllib.error, sys, time, os
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

W   = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
DAY = 86400

def get(url, tries=8):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=90) as r:
                return r.getcode(), json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code, e.read().decode("utf-8","replace")[:120])
            if e.code in (500,502,503,429,408):
                time.sleep(1.0 + i*0.8); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:120]); time.sleep(1.0 + i*0.8)
    return last

def dump(name, obj):
    p=os.path.join(OUT,name)
    with open(p,"w",encoding="utf-8") as f:
        json.dump(obj,f,ensure_ascii=False)
    print(f"  [dump] {name}  ({os.path.getsize(p):,} bytes)")

# ---------- 1) lb-api profit & volume ----------
print("== lb-api profit / volume ==")
profit_raw={}; volume_raw={}
for metric,store in (("profit",profit_raw),("volume",volume_raw)):
    for w in ["1d","7d","30d","all"]:
        c,d = get(f"https://lb-api.polymarket.com/{metric}?window={w}&address={W}")
        store[w]={"code":c,"raw":d}
        amt = d[0].get("amount") if (isinstance(d,list) and d) else None
        print(f"  {metric:7} {w:4} code={c} amount={amt}")
dump("raw_profit.json", profit_raw)
dump("raw_volume.json", volume_raw)

# ---------- 2) data-api value / traded ----------
print("\n== data-api value / traded ==")
c1,value = get(f"https://data-api.polymarket.com/value?user={W}")
c2,traded= get(f"https://data-api.polymarket.com/traded?user={W}")
print("  value ", c1, value)
print("  traded", c2, traded)
dump("raw_value_traded.json", {"value":{"code":c1,"raw":value},"traded":{"code":c2,"raw":traded}})

# ---------- 3) FULL unfiltered activity via offset pagination ----------
print("\n== FULL activity (unfiltered, offset paginated) ==")
LIM=1000
acts=[]; offset=0; pages=0; page_meta=[]
while True:
    c,d = get(f"https://data-api.polymarket.com/activity?user={W}&limit={LIM}&offset={offset}")
    if c!=200 or not isinstance(d,list):
        page_meta.append({"offset":offset,"code":c,"n":0,"note":"FAILED"})
        print(f"  offset={offset} FAILED code={c}; stopping")
        break
    n=len(d)
    ts=[int(a['timestamp']) for a in d if a.get('timestamp')]
    page_meta.append({"offset":offset,"code":c,"n":n,
                      "ts_min":min(ts) if ts else None,"ts_max":max(ts) if ts else None})
    acts.extend(d); pages+=1
    print(f"  offset={offset:6} got {n:5}  total={len(acts):6}")
    if n < LIM:
        break
    offset += LIM
    time.sleep(0.25)

print(f"  pages={pages} total_records={len(acts)}")
dump("raw_activity.json", acts)
dump("activity_page_meta.json", page_meta)

# ---------- 4) dedup check (offset paging can rarely overlap) ----------
seen=set(); dups=0
for a in acts:
    k=(a.get("transactionHash"),a.get("asset"),a.get("type"),a.get("timestamp"),a.get("usdcSize"),a.get("outcomeIndex"))
    if k in seen: dups+=1
    seen.add(k)
print(f"  duplicate-ish rows: {dups}")

print("\nDONE fetch_all")
