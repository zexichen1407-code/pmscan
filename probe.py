import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

# pull a page of active markets ordered by volume
url = "https://gamma-api.polymarket.com/markets?closed=false&limit=500&order=volumeNum&ascending=false"
mk = get(url)
print("returned markets:", len(mk))
print("\n== keys of market[0] ==")
print(sorted(mk[0].keys()))

# find first negRisk market and dump reward/price-relevant fields
def field(m, k):
    return m.get(k, None)

negs = [m for m in mk if m.get("negRisk")]
print("\nnegRisk markets in page:", len(negs))

sample = negs[0] if negs else mk[0]
keys_interest = ["question","slug","negRisk","negRiskMarketID","groupItemTitle",
                 "bestBid","bestAsk","spread","lastTradePrice","outcomes","outcomePrices",
                 "clobRewards","rewardsDailyRate","rewardsMinSize","rewardsMaxSpread",
                 "acceptingOrders","enableOrderBook","active","liquidityNum","volumeNum",
                 "events"]
print("\n== sample negRisk market relevant fields ==")
for k in keys_interest:
    v = sample.get(k, "<MISSING>")
    if k == "events" and isinstance(v, list) and v:
        v = {ek: v[0].get(ek) for ek in ["id","slug","title","negRisk","ticker"] if ek in v[0]}
    print(f"{k}: {json.dumps(v)[:300] if not isinstance(v,(str,int,float,bool,type(None))) else v}")

# how many have rewards?
def has_reward(m):
    cr = m.get("clobRewards")
    if isinstance(cr, list) and cr:
        return True
    if m.get("rewardsDailyRate"):
        return True
    return False

rew = [m for m in mk if has_reward(m)]
print("\nmarkets with some reward field set (this page):", len(rew))
if rew:
    print("\n== sample reward market clobRewards ==")
    print(json.dumps(rew[0].get("clobRewards"), indent=1)[:600])
    print("rewardsMinSize:", rew[0].get("rewardsMinSize"), "rewardsMaxSpread:", rew[0].get("rewardsMaxSpread"))
