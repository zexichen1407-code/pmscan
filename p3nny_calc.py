import json, urllib.request, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
DAY = 86400

def get(url):
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            last = e
    raise last

def lb(metric, win):
    d = get(f"https://lb-api.polymarket.com/{metric}?window={win}&address={W}")
    return d[0]["amount"] if d else 0.0

# ---------- 1) lb-api trading PnL + volume by window ----------
print("== TRADING PnL (lb-api) & VOLUME by window ==")
pnl, vol = {}, {}
for w in ["1d", "7d", "30d", "all"]:
    pnl[w] = lb("profit", w); vol[w] = lb("volume", w)
    roi = (pnl[w] / vol[w] * 100) if vol[w] else 0
    print(f"  {w:>3}: PnL ${pnl[w]:>12,.0f}   volume ${vol[w]:>13,.0f}   ROI {roi:>6.2f}%")

# ---------- 2) FULL reward history ----------
rewards = []
off = 0
while True:
    page = get(f"https://data-api.polymarket.com/activity?user={W}&type=REWARD&limit=500&offset={off}")
    if not page: break
    rewards.extend(page)
    if len(page) < 500: break
    off += 500
rtot = sum(float(r.get("usdcSize", 0)) for r in rewards)
rts = [int(r["timestamp"]) for r in rewards if r.get("timestamp")]
if rts:
    tmax = max(rts); tmin = min(rts)
    span_days = max(1, (tmax - tmin) / DAY)
    r7 = sum(float(r.get("usdcSize", 0)) for r in rewards if int(r["timestamp"]) >= tmax - 7*DAY)
    r30 = sum(float(r.get("usdcSize", 0)) for r in rewards if int(r["timestamp"]) >= tmax - 30*DAY)
    print(f"\n== LIQUIDITY REWARDS (full pull, {len(rewards)} events) ==")
    print(f"  all-time reward total : ${rtot:,.0f}  over ~{span_days:.0f} days  => ${rtot/span_days:,.0f}/day avg")
    print(f"  last 7d reward        : ${r7:,.0f}  => ${r7/7:,.0f}/day")
    print(f"  last 30d reward       : ${r30:,.0f}  => ${r30/30:,.0f}/day")

# ---------- 3) activity sample: type mix + market breakdown + date range ----------
acts = []
for lim in (1000, 500, 200, 100):
    try:
        acts = get(f"https://data-api.polymarket.com/activity?user={W}&limit={lim}")
        break
    except Exception:
        continue
typ_cnt = defaultdict(int); typ_usdc = defaultdict(float)
buy = sell = 0.0
ev_usdc = defaultdict(float)
cat = defaultdict(float)
ats = [int(a["timestamp"]) for a in acts if a.get("timestamp")]
for a in acts:
    t = a.get("type"); u = float(a.get("usdcSize", 0))
    typ_cnt[t] += 1; typ_usdc[t] += u
    if t == "TRADE":
        if a.get("side") == "BUY": buy += u
        elif a.get("side") == "SELL": sell += u
        ev_usdc[a.get("eventSlug") or a.get("slug") or "?"] += u
        sl = (a.get("eventSlug") or "")
        key = ("weather" if "temperature" in sl else
               "soccer/worldcup" if ("world-cup" in sl or "-vs-" in sl) else
               "fed/centralbank" if ("fed-" in sl or "decision" in sl or "rate" in sl) else
               "election/politics" if ("election" in sl or "primary" in sl or "governor" in sl or "senate" in sl or "president" in sl or "prime-minister" in sl) else
               "macro/econ" if ("gdp" in sl or "cpi" in sl or "inflation" in sl or "q2" in sl or "q3" in sl) else
               "other")
        cat[key] += u
span = f"{(max(ats)-min(ats))/DAY:.1f}d" if ats else "?"
print(f"\n== ACTIVITY SAMPLE ({len(acts)} most-recent events, spans {span}) ==")
print("  type mix (count / USDC):")
for t in sorted(typ_cnt, key=lambda x: -typ_usdc[x]):
    print(f"    {t:<12} {typ_cnt[t]:>5}  ${typ_usdc[t]:>12,.0f}")
print(f"  TRADE side: BUY ${buy:,.0f}  /  SELL ${sell:,.0f}   (BUY {100*buy/(buy+sell):.0f}%)" if (buy+sell) else "  no trades")
print("\n  TRADE volume by category (sample):")
for k, v in sorted(cat.items(), key=lambda x: -x[1]):
    print(f"    {k:<18} ${v:>12,.0f}  ({100*v/sum(cat.values()):.0f}%)")
print("\n  top 12 events by TRADE volume (sample):")
for s, v in sorted(ev_usdc.items(), key=lambda x: -x[1])[:12]:
    print(f"    ${v:>10,.0f}  {s[:60]}")

# ---------- 4) split synthesis ----------
print("\n== SPLIT (recent 30d, precise) ==")
if rts:
    trade30 = pnl["30d"]; rew30 = r30
    print(f"  trading/arb PnL (spread capture) 30d : ${trade30:,.0f}  (${trade30/30:,.0f}/day)")
    print(f"  liquidity rewards 30d                : ${rew30:,.0f}  (${rew30/30:,.0f}/day)")
    net30 = trade30 + rew30
    print(f"  NET 30d (trade+reward)               : ${net30:,.0f}  (${net30/30:,.0f}/day)")
    print(f"  reward share of net                  : {100*rew30/net30:.0f}%" if net30 else "")
    print(f"\n  last 7d trading PnL                  : ${pnl['7d']:,.0f}  (${pnl['7d']/7:,.0f}/day)  [NEGATIVE]")
    print(f"  last 7d rewards                      : ${r7:,.0f}  (${r7/7:,.0f}/day)")
    print(f"  NET 7d                               : ${pnl['7d']+r7:,.0f}  (${(pnl['7d']+r7)/7:,.0f}/day)")
