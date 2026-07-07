import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = "0x4f1d5ae26fc31472966e951af3183308736d8de2"

def get(url, tries=4):
    last=None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.getcode(), json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code, e.read().decode("utf-8","replace")[:120])
            if e.code in (502,503,500,429): time.sleep(1.5); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:120]); time.sleep(1.5)
    return last

# Verify offset returns DISTINCT records (compare txhash of page0 vs page1)
print("=== offset distinctness (no type) ===")
c0,p0 = get(f"https://data-api.polymarket.com/activity?user={W}&limit=100&offset=0")
c1,p1 = get(f"https://data-api.polymarket.com/activity?user={W}&limit=100&offset=100")
def keyset(p): return set((a.get("transactionHash"),a.get("asset"),a.get("type"),a.get("timestamp")) for a in p)
s0,s1 = keyset(p0),keyset(p1)
print(f" page0 n={len(p0)} page1 n={len(p1)} overlap={len(s0&s1)}")
print(f" page0 ts range {min(int(a['timestamp']) for a in p0)}..{max(int(a['timestamp']) for a in p0)}")
print(f" page1 ts range {min(int(a['timestamp']) for a in p1)}..{max(int(a['timestamp']) for a in p1)}")

# REWARD pagination: does offset ALWAYS 502, or intermittent? try several times + offset 0/100/500
print("\n=== REWARD offset behavior (retry) ===")
for off in [0,100,500]:
    c,d = get(f"https://data-api.polymarket.com/activity?user={W}&type=REWARD&limit=100&offset={off}")
    n = len(d) if isinstance(d,list) else d
    print(f" REWARD offset={off}: code={c} n={n}")

# How many REWARD total via limit=1000 no offset?
print("\n=== REWARD via limit=1000 offset=0 ===")
c,d = get(f"https://data-api.polymarket.com/activity?user={W}&type=REWARD&limit=1000")
print(f" code={c} n={len(d) if isinstance(d,list) else d}")
if isinstance(d,list) and d:
    ts=[int(x['timestamp']) for x in d]
    print(f" ts range {min(ts)}..{max(ts)}  ({time.strftime('%Y-%m-%d',time.gmtime(min(ts)))} .. {time.strftime('%Y-%m-%d',time.gmtime(max(ts)))})")
    print(f" total usdcSize={sum(float(x.get('usdcSize',0)) for x in d):,.2f}")

# does REWARD limit=1000 hit exactly 1000 (truncated) or fewer?
