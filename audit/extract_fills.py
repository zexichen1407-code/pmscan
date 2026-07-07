import ijson, json, collections
from decimal import Decimal

PATH = r"C:/Users/zexi/pmscan/audit/raw_activity_full.json"
WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"

tennis_slug = "atp-cobolli-zverev-2026-06-07"

# Collect: all TRADE fills for tennis eventSlug, plus tally eventSlugs for soccer in-play
tennis_fills = []
eventslug_counts = collections.Counter()
# track in-play soccer markets: eventSlug containing match patterns, count of TRADE fills
soccer_candidates = collections.Counter()
cond_by_event = collections.defaultdict(set)

def num(x):
    try:
        return float(x)
    except Exception:
        return x

with open(PATH, "rb") as f:
    for rec in ijson.items(f, "item"):
        t = rec.get("type")
        es = rec.get("eventSlug", "")
        if t == "TRADE":
            eventslug_counts[es] += 1
            # soccer heuristic: slug looks like a soccer match event with many trades
            if any(k in es for k in ["fifwc", "soccer", "-vs-", "epl", "uefa", "fifa", "wcq", "ucl"]) or True:
                pass
        if es == tennis_slug:
            tennis_fills.append({
                "timestamp": rec.get("timestamp"),
                "type": t,
                "size": num(rec.get("size")),
                "usdcSize": num(rec.get("usdcSize")),
                "price": num(rec.get("price")),
                "asset": rec.get("asset"),
                "side": rec.get("side"),
                "outcomeIndex": rec.get("outcomeIndex"),
                "outcome": rec.get("outcome"),
                "title": rec.get("title"),
                "slug": rec.get("slug"),
                "conditionId": rec.get("conditionId"),
            })
            if rec.get("conditionId"):
                cond_by_event[es].add(rec.get("conditionId"))

with open(r"C:/Users/zexi/pmscan/audit/_tennis_fills.json", "w") as out:
    json.dump(tennis_fills, out, indent=1)

print("TENNIS FILLS:", len(tennis_fills))
print("tennis conditionIds:", cond_by_event.get(tennis_slug))
print()
print("TOP 40 eventSlugs by TRADE count:")
for es, c in eventslug_counts.most_common(40):
    print(f"  {c:6d}  {es}")
