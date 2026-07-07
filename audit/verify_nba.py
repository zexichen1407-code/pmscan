import json, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
SLUG = "2026-nba-champion"

# --- load only NBA rows ---
rows = []
try:
    import ijson
    with open(PATH, "rb") as f:
        for obj in ijson.items(f, "item"):
            if obj.get("eventSlug") == SLUG:
                rows.append(obj)
    src = "ijson stream"
except Exception as e:
    with open(PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = [o for o in data if o.get("eventSlug") == SLUG]
    src = f"json.load (ijson failed: {e})"

print(f"loaded via {src}; NBA rows = {len(rows)}")

def f(x):
    try: return float(x)
    except: return 0.0

# sort chronologically
rows.sort(key=lambda r: int(r.get("timestamp") or 0))

# --- 1) per-leg cumulative buys (the '77k vs 4455' claim) ---
leg = defaultdict(lambda: {"team": "", "no_buy_sz": 0.0, "no_buy_usd": 0.0,
                            "yes_buy_sz": 0.0, "yes_buy_usd": 0.0,
                            "merge_sz": 0.0, "sell_sz": 0.0})
typ = defaultdict(lambda: [0, 0.0])
for r in rows:
    t = r.get("type"); u = f(r.get("usdcSize")); sz = f(r.get("size"))
    typ[t][0] += 1; typ[t][1] += u
    cid = r.get("conditionId") or "?"
    L = leg[cid]
    if not L["team"]:
        L["team"] = (r.get("title") or r.get("slug") or cid)[:40]
    if t == "TRADE":
        oc = (r.get("outcome") or "").lower()
        side = r.get("side")
        if side == "BUY" and oc == "no":
            L["no_buy_sz"] += sz; L["no_buy_usd"] += u
        elif side == "BUY" and oc == "yes":
            L["yes_buy_sz"] += sz; L["yes_buy_usd"] += u
        elif side == "SELL":
            L["sell_sz"] += sz
    elif t == "MERGE":
        L["merge_sz"] += sz

print("\n== activity type mix (NBA event only) ==")
for t,(c,u) in sorted(typ.items(), key=lambda x:-x[1][1]):
    print(f"  {t:<12} {c:>5}  ${u:>12,.0f}")

print("\n== per-leg cumulative buys over 47 days (sorted by NO buy size) ==")
print(f"{'team':<32}{'NO_buy_sz':>12}{'NO_usd':>11}{'YES_buy_sz':>12}{'merge_sz':>11}")
for cid,L in sorted(leg.items(), key=lambda x:-x[1]['no_buy_sz']):
    print(f"{L['team']:<32}{L['no_buy_sz']:>12,.0f}{L['no_buy_usd']:>11,.0f}{L['yes_buy_sz']:>12,.0f}{L['merge_sz']:>11,.0f}")

# --- 2) PEAK CAPITAL: walk timeline, money out (BUY) minus money back (SELL/MERGE/CONVERSION/REDEEM) ---
bal = 0.0; peak = 0.0; peak_ts = 0
cum_buy = 0.0; cum_back = 0.0
for r in rows:
    t = r.get("type"); u = f(r.get("usdcSize"))
    if t == "TRADE" and r.get("side") == "BUY":
        bal += u; cum_buy += u
    elif t == "TRADE" and r.get("side") == "SELL":
        bal -= u; cum_back += u
    elif t in ("MERGE", "CONVERSION", "REDEEM"):
        bal -= u; cum_back += u
    elif t == "SPLIT":
        bal += u; cum_buy += u
    if bal > peak:
        peak = bal; peak_ts = int(r.get("timestamp") or 0)

import datetime
def ds(ts): return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"

print("\n== CAPITAL FOOTPRINT (chronological cash walk) ==")
print(f"  cumulative BUY (lifetime turnover) : ${cum_buy:,.0f}")
print(f"  cumulative returned (sell+merge+conv+redeem): ${cum_back:,.0f}")
print(f"  PEAK net capital ever deployed     : ${peak:,.0f}   (at {ds(peak_ts)})")
print(f"  => capital recycled ~{cum_buy/peak:.1f}x over the event" if peak>0 else "")

# --- 3) peak simultaneous NO inventory per leg (does any leg ever hold 77k at once?) ---
# track per-leg net token inventory over time: +BUY size, -SELL size, -MERGE size (merge consumes that leg's tokens)
# conversion is event-level (no per-leg cid on conv rows typically); we note that as a caveat.
inv = defaultdict(float); peakinv = defaultdict(float)
conv_has_cid = 0; conv_total = 0
for r in rows:
    t = r.get("type"); sz = f(r.get("size")); cid = r.get("conditionId") or "?"
    if t == "CONVERSION":
        conv_total += 1
        if r.get("conditionId"): conv_has_cid += 1
    if t == "TRADE" and r.get("side") == "BUY":
        inv[cid] += sz
    elif t == "TRADE" and r.get("side") == "SELL":
        inv[cid] -= sz
    elif t == "MERGE":
        inv[cid] -= sz
    if inv[cid] > peakinv[cid]:
        peakinv[cid] = inv[cid]

print(f"\n== peak SIMULTANEOUS per-leg inventory (BUY - SELL - MERGE; conversion not leg-attributed) ==")
print(f"   (conversion rows with conditionId: {conv_has_cid}/{conv_total})")
for cid,p in sorted(peakinv.items(), key=lambda x:-x[1])[:14]:
    print(f"   {leg[cid]['team']:<32} peak inv {p:>12,.0f}")
