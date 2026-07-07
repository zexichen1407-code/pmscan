import json, collections
from datetime import datetime, timezone

fills = json.load(open(r"C:/Users/zexi/pmscan/audit/_tennis_fills.json"))

print("Total records:", len(fills))
types = collections.Counter(f["type"] for f in fills)
print("Types:", dict(types))

# Group by asset (token id) + side + slug
by_market = collections.defaultdict(list)
for f in fills:
    by_market[(f["slug"], f["outcome"], f["asset"])].append(f)

print("\nDistinct (slug, outcome, asset):")
for (slug, outcome, asset), recs in sorted(by_market.items(), key=lambda kv: -len(kv[1])):
    trades = [r for r in recs if r["type"]=="TRADE"]
    buys = [r for r in trades if r["side"]=="BUY"]
    sells = [r for r in trades if r["side"]=="SELL"]
    print(f"  {slug} | {outcome} | asset={asset[:20]}... | trades={len(trades)} buys={len(buys)} sells={len(sells)}")

# Time range of TRADE fills
trades = [f for f in fills if f["type"]=="TRADE" and f["timestamp"]]
trades.sort(key=lambda r: r["timestamp"])
if trades:
    t0 = datetime.fromtimestamp(trades[0]["timestamp"], timezone.utc)
    t1 = datetime.fromtimestamp(trades[-1]["timestamp"], timezone.utc)
    print(f"\nTRADE time range: {t0.isoformat()}  ->  {t1.isoformat()}")
    print(f"  span minutes: {(trades[-1]['timestamp']-trades[0]['timestamp'])/60:.1f}")

# Show distinct assets and full ids
print("\nFull asset ids:")
assets = {}
for f in fills:
    if f["asset"]:
        assets.setdefault(f["asset"], (f["outcome"], f["slug"], f["outcomeIndex"]))
for a,(o,s,oi) in assets.items():
    print(f"  {a}  outcome={o} idx={oi} slug={s}")
print("\nconditionId:", fills[0]["conditionId"] if fills else None)
print("title sample:", fills[0]["title"] if fills else None)
