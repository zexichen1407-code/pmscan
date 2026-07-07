import json, urllib.request, urllib.error, sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def fnum(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d

def ascii_title(t):
    return re.sub(r"[^\x00-\x7f]", "?", (t or ""))[:44]

events = []
for off in range(0, 2100, 100):
    url = f"https://gamma-api.polymarket.com/events?closed=false&limit=100&offset={off}&order=volume24hr&ascending=false"
    try:
        page = get(url)
    except urllib.error.HTTPError as e:
        break
    if not page:
        break
    events.extend(page)
    if len(page) < 100:
        break
print("fetched events:", len(events))

rows = []
for ev in events:
    mks = ev.get("markets") or []
    if len(mks) < 3:
        continue
    pool = 0.0
    maxspreads, minsizes, yes_asks = [], [], []
    accepting = 0
    for m in mks:
        for c in (m.get("clobRewards") or []):
            pool += fnum(c.get("rewardsDailyRate"))
        ms = m.get("rewardsMaxSpread")
        if ms is not None and fnum(ms) > 0:
            maxspreads.append(fnum(ms))
        mn = m.get("rewardsMinSize")
        if mn is not None and fnum(mn) > 0:
            minsizes.append(fnum(mn))
        ba = m.get("bestAsk")
        if ba is not None and fnum(ba) > 0:
            yes_asks.append(fnum(ba))
        if m.get("acceptingOrders"):
            accepting += 1
    neg = ev.get("negRisk") or any(m.get("negRisk") for m in mks)
    sya = sum(yes_asks) if yes_asks else None
    rows.append({
        "title": ascii_title(ev.get("title")),
        "n": len(mks), "acc": accepting, "neg": bool(neg),
        "pool": round(pool, 0),
        "perleg": round(pool / len(mks), 1),
        "maxspr": (max(maxspreads) if maxspreads else None),
        "minsz": (min(minsizes) if minsizes else None),
        "sya": (round(sya, 4) if sya is not None else None),
        "gap": (round(1 - sya, 4) if sya is not None else None),
        "v24": round(fnum(ev.get("volume24hr")), 0),
    })

rew = sorted([r for r in rows if r["neg"] and r["pool"] > 0], key=lambda r: r["pool"], reverse=True)
print(f"\n=== A) NEG-RISK MULTI-OUTCOME EVENTS WITH REWARD POOL  (count {len(rew)}, total ${sum(r['pool'] for r in rew):,.0f}/day) ===")
print(f"{'pool$/d':>8}{'/leg':>6}{'#out':>5}{'acc':>4}{'maxSpr':>7}{'minSz':>6}{'sumAsk':>7}  title")
for r in rew[:30]:
    print(f"{r['pool']:>8.0f}{r['perleg']:>6.0f}{r['n']:>5}{r['acc']:>4}{str(r['maxspr']):>7}{str(r['minsz']):>6}{str(r['sya']):>7}  {r['title']}")

# clean farmable: every leg liquid (acc==n), small/medium, decent per-leg reward, book sums near 1
clean = sorted([r for r in rew if r["acc"] == r["n"] and 3 <= r["n"] <= 15
                and r["sya"] is not None and 0.9 <= r["sya"] <= 1.2 and r["perleg"] >= 20],
               key=lambda r: r["perleg"], reverse=True)
print(f"\n=== B) CLEAN FARMABLE (all legs live, 3-15 outcomes, book~1, >=$20/leg/day)  (count {len(clean)}) ===")
print(f"{'/leg$/d':>7}{'pool':>7}{'#out':>5}{'maxSpr':>7}{'minSz':>6}{'sumAsk':>7}{'gap':>8}  title")
for r in clean[:30]:
    print(f"{r['perleg']:>7.0f}{r['pool']:>7.0f}{r['n']:>5}{str(r['maxspr']):>7}{str(r['minsz']):>6}{str(r['sya']):>7}{r['gap']:>8.4f}  {r['title']}")

# live arb: book sums under 1 across all-live legs
arb = sorted([r for r in rows if r["neg"] and r["gap"] is not None and r["gap"] > 0
              and r["acc"] == r["n"] and r["n"] <= 20],
             key=lambda r: r["gap"], reverse=True)
print(f"\n=== C) LIVE DUTCH-BOOK SIGNAL (all legs live, sum YES ask < 1)  (count {len(arb)}) ===")
print(f"{'gap':>8}{'sumAsk':>7}{'#out':>5}{'pool$/d':>8}{'v24h$':>11}  title")
for r in arb[:20]:
    print(f"{r['gap']:>8.4f}{str(r['sya']):>7}{r['n']:>5}{r['pool']:>8.0f}{r['v24']:>11,.0f}  {r['title']}")
