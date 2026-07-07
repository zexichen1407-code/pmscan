import json, urllib.request, urllib.error, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = "0x4f1d5ae26fc31472966e951af3183308736d8de2"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    code = None
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code = r.getcode()
            return code, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8","replace")[:200]
    except Exception as e:
        return "ERR", str(e)[:200]

print("=== 1. lb-api profit/volume each window (raw [0]) ===")
for metric in ["profit","volume"]:
    for win in ["1d","7d","30d","all"]:
        c,d = get(f"https://lb-api.polymarket.com/{metric}?window={win}&address={W}")
        head = json.dumps(d)[:200] if not isinstance(d,str) else d
        print(f"  {metric:7} {win:4} code={c} -> {head}")

print("\n=== 2. data-api value / traded ===")
for ep in ["value","traded"]:
    c,d = get(f"https://data-api.polymarket.com/{ep}?user={W}")
    print(f"  {ep:7} code={c} -> {json.dumps(d)[:200] if not isinstance(d,str) else d}")

print("\n=== 3. activity first page schema ===")
c,d = get(f"https://data-api.polymarket.com/activity?user={W}&limit=5")
print(f"  code={c} returned={len(d) if isinstance(d,list) else d}")
if isinstance(d,list) and d:
    print("  keys:", sorted(d[0].keys()))
    print("  sample[0]:", json.dumps(d[0], ensure_ascii=False)[:600])

print("\n=== 4. activity OFFSET test (does offset 400?) ===")
# test no-type with offset
for params in ["limit=100","limit=100&offset=100","limit=100&offset=500",
               "type=TRADE&limit=100&offset=100","type=REWARD&limit=100&offset=100"]:
    c,d = get(f"https://data-api.polymarket.com/activity?user={W}&{params}")
    n = len(d) if isinstance(d,list) else d
    print(f"  [{params:35}] code={c} n={n}")

print("\n=== 5. max limit test ===")
for lim in [500,1000,2000,5000]:
    c,d = get(f"https://data-api.polymarket.com/activity?user={W}&limit={lim}")
    n = len(d) if isinstance(d,list) else d
    print(f"  limit={lim:5} code={c} n={n}")
