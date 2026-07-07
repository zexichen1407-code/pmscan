# -*- coding: utf-8 -*-
"""
arb_aggregate.py
Single streaming pass over raw_activity_full.json.
Builds a per-event aggregate structure and dumps it to arb_event_agg.json
plus a global type/pattern summary.

Identity definitions (computed from ACTIVITY ONLY):
- per-outcome merge arb: on same conditionId, buy outcome0 @p, buy outcome1 @q. If p+q<1, MERGE pair -> $1.
  locked = (1 - p - q) * min(size0,size1)  [we report MERGE usdc as realized lock]
- complete-set dutch book: across N submarkets of an event, buy one YES of each for sum(asks); exactly one pays $1.
  locked = (1 - sum(ask_i)) * size, realized at REDEEM.
- neg-risk NO conversion: buy k cheap NOs, CONVERSION -> (k-1) USDC + complementary YES tokens.
- hold-to-redeem: directional, REDEEM with no merge/conversion lock.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
OUT_AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
OUT_SUM = r"C:\Users\zexi\pmscan\audit\arb_global_summary.json"

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0

# event -> structure
events = {}   # eventSlug -> dict

global_type_count = {}
global_type_usdc = {}
n = 0
min_ts = None
max_ts = None

def get_event(es):
    e = events.get(es)
    if e is None:
        e = {
            "eventSlug": es,
            "titles": {},          # submarket title -> count
            "conds": {},           # conditionId -> leg agg
            "merge_usdc": 0.0, "merge_count": 0, "merge_size": 0.0,
            "split_usdc": 0.0, "split_count": 0, "split_size": 0.0,
            "conv_usdc": 0.0, "conv_count": 0, "conv_size": 0.0,
            "redeem_count": 0, "redeem_usdc": 0.0,
            "trade_buy_usdc": 0.0, "trade_sell_usdc": 0.0,
            "trade_count": 0,
            "min_ts": None, "max_ts": None,
        }
        events[es] = e
    return e

def get_cond(e, cid, title):
    c = e["conds"].get(cid)
    if c is None:
        c = {
            "conditionId": cid,
            "title": title,
            # per outcome label -> per side agg
            "out": {},   # label -> {BUY:{size,usdc,n,minp,maxp}, SELL:{...}}
            "merge_usdc": 0.0, "merge_count": 0, "merge_size": 0.0,
            "split_usdc": 0.0, "split_count": 0, "split_size": 0.0,
        }
        e["conds"][cid] = c
    elif title and not c["title"]:
        c["title"] = title
    return c

with open(PATH, 'rb') as fh:
    for rec in ijson.items(fh, 'item'):
        n += 1
        t = rec.get('type') or ""
        usd = f(rec.get('usdcSize'))
        size = f(rec.get('size'))
        ts = rec.get('timestamp')
        if isinstance(ts, str):
            try: ts = int(ts)
            except: ts = None
        global_type_count[t] = global_type_count.get(t, 0) + 1
        global_type_usdc[t] = global_type_usdc.get(t, 0.0) + usd
        if ts is not None:
            if min_ts is None or ts < min_ts: min_ts = ts
            if max_ts is None or ts > max_ts: max_ts = ts

        es = rec.get('eventSlug') or ""
        cid = rec.get('conditionId') or ""
        title = rec.get('title') or ""

        # rebate/reward/yield rows have no event; skip event grouping
        if t in ("MAKER_REBATE", "TAKER_REBATE", "REWARD", "YIELD"):
            continue
        if not es:
            # some rows might lack eventSlug but have slug; fall back
            es = rec.get('slug') or cid or "(none)"

        e = get_event(es)
        e["titles"][title] = e["titles"].get(title, 0) + 1
        if ts is not None:
            if e["min_ts"] is None or ts < e["min_ts"]: e["min_ts"] = ts
            if e["max_ts"] is None or ts > e["max_ts"]: e["max_ts"] = ts

        if t == "TRADE":
            side = rec.get('side') or ""
            label = rec.get('outcome') or ("idx"+str(rec.get('outcomeIndex')))
            price = f(rec.get('price'))
            c = get_cond(e, cid, title)
            od = c["out"].setdefault(label, {})
            sd = od.setdefault(side, {"size":0.0,"usdc":0.0,"n":0,"minp":None,"maxp":None,"mints":None,"maxts":None})
            sd["size"] += size
            sd["usdc"] += usd
            sd["n"] += 1
            if price>0:
                sd["minp"] = price if sd["minp"] is None else min(sd["minp"], price)
                sd["maxp"] = price if sd["maxp"] is None else max(sd["maxp"], price)
            if ts is not None:
                sd["mints"] = ts if sd["mints"] is None else min(sd["mints"], ts)
                sd["maxts"] = ts if sd["maxts"] is None else max(sd["maxts"], ts)
            e["trade_count"] += 1
            if side == "BUY": e["trade_buy_usdc"] += usd
            elif side == "SELL": e["trade_sell_usdc"] += usd
        elif t == "MERGE":
            e["merge_usdc"] += usd; e["merge_count"] += 1; e["merge_size"] += size
            c = get_cond(e, cid, title)
            c["merge_usdc"] += usd; c["merge_count"] += 1; c["merge_size"] += size
        elif t == "SPLIT":
            e["split_usdc"] += usd; e["split_count"] += 1; e["split_size"] += size
            c = get_cond(e, cid, title)
            c["split_usdc"] += usd; c["split_count"] += 1; c["split_size"] += size
        elif t == "CONVERSION":
            e["conv_usdc"] += usd; e["conv_count"] += 1; e["conv_size"] += size
        elif t == "REDEEM":
            e["redeem_count"] += 1; e["redeem_usdc"] += usd

        if n % 50000 == 0:
            print("...processed", n, "rows, events so far", len(events), file=sys.stderr)

print("TOTAL rows:", n, file=sys.stderr)
print("TS range:", min_ts, max_ts, file=sys.stderr)

summary = {
    "total_rows": n,
    "min_ts": min_ts, "max_ts": max_ts,
    "type_count": global_type_count,
    "type_usdc": {k: round(v,2) for k,v in global_type_usdc.items()},
    "n_events": len(events),
}
with open(OUT_SUM, 'w', encoding='utf-8') as o:
    json.dump(summary, o, ensure_ascii=False, indent=1)

with open(OUT_AGG, 'w', encoding='utf-8') as o:
    json.dump(events, o, ensure_ascii=False)

print("WROTE", OUT_AGG, "and", OUT_SUM, file=sys.stderr)
print(json.dumps(summary, ensure_ascii=False, indent=1))
