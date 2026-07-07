import json, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = "0x4f1d5ae26fc31472966e951af3183308736d8de2"

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

print("== lb-api profit/volume by window ==")
for win in ["1d", "7d", "30d", "all"]:
    try:
        p = get(f"https://lb-api.polymarket.com/profit?window={win}&address={W}")
        v = get(f"https://lb-api.polymarket.com/volume?window={win}&address={W}")
        print(f"{win:>4}: profit={json.dumps(p)[:160]}  volume={json.dumps(v)[:120]}")
    except Exception as e:
        print(f"{win}: ERR {e}")

print("\n== data-api value/traded ==")
for ep in ["value", "traded"]:
    try:
        print(ep, "->", json.dumps(get(f"https://data-api.polymarket.com/{ep}?user={W}"))[:160])
    except Exception as e:
        print(ep, "ERR", e)

print("\n== activity first page: keys + 3 samples (mixed types) ==")
act = get(f"https://data-api.polymarket.com/activity?user={W}&limit=100")
print("returned:", len(act))
if act:
    print("keys:", sorted(act[0].keys()))
    seen = set()
    for a in act:
        t = a.get("type")
        if t not in seen:
            seen.add(t)
            print(f"\n-- type={t} --")
            print(json.dumps({k: a.get(k) for k in ["type","timestamp","size","usdcSize","price","side","outcome","outcomeIndex","title","slug","eventSlug","conditionId"]}, ensure_ascii=False)[:300])
print("\ntypes on first page:", sorted(seen))
