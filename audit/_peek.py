import ijson, sys, json
sys.stdout.reconfigure(encoding='utf-8')
f = open('C:/Users/zexi/pmscan/audit/raw_activity_full.json', 'rb')
n = 0
keys = None
for row in ijson.items(f, 'item'):
    if keys is None:
        keys = list(row.keys())
        print("KEYS:", keys)
    # print a compact ascii-safe version
    safe = {k: row.get(k) for k in ('type','eventSlug','conditionId','outcome','side','timestamp','size','usdcSize','price')}
    print(json.dumps(safe, default=str)[:300])
    n += 1
    if n >= 8:
        break
