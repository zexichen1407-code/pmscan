# -*- coding: utf-8 -*-
"""
arb_classify.py
Read arb_event_agg.json, classify each event's dominant arb pattern, and
compute the best LOCKED-SPREAD estimate from ACTIVITY DATA ONLY.

LOCKED-SPREAD identities (all activity-only):

(A) "yes+no merge"  [MEASURED via MERGE rows]
    On a single conditionId we BUY both outcome labels (the two sides of the
    binary). A MERGE row burns 1 of each -> pays $1. The realized lock for the
    event = sum over merged conditionIds of:
        merge_size_c * (1 - vwap_buy_outcomeA - vwap_buy_outcomeB)
    where vwaps are size-weighted BUY prices on that conditionId.
    This is MEASURED: merge_size is the actual number of $1 pairs realized.

(B) "neg-risk NO conversion"  [MEASURED notional, INFERRED spread]
    CONVERSION rows exist. A conversion of k NO tokens returns (k-1) USDC plus
    complementary YES tokens. The clean activity-only lower bound on locked value:
        For the conversion legs we BUY 'No' outcomes cheaply. The conversion
        size S (tokens) yields (k-1)*S USDC immediately... but k is per-tx and not
        in the row. We instead bound the spread using the BUY cost of No legs vs
        the USDC released. Reported as conv_size and the avg No buy price; spread
        is INFERRED because k (number of NOs per conversion) is not in activity.
    We report measured: total No-buy cost, total conversion size, and the
    implied locked spread = conv_size_total - total_No_buy_usdc  (a documented
    proxy; flagged inferred).

(C) "complete-set dutch book"  [MEASURED via REDEEM + buys]
    Buy one YES of each of N submarkets for sum(asks); one resolves $1.
    Locked = (1 - sum_vwap_yes) * size, realized at REDEEM.
    Detect: many conditionIds, BUY-heavy on one label each, REDEEM present, little
    MERGE/CONVERSION. Spread computed from the per-submarket YES vwaps if a clean
    one-share-each basket is visible.

(D) "hold-to-redeem"  [residual / directional]
    REDEEM dominates with no merge/conversion lock; directional.

Classification priority by realized USDC channel:
    merge_usdc, conv_usdc, redeem_usdc, else trade-only.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
OUT = r"C:\Users\zexi\pmscan\audit\arb_event_classified.json"

with open(AGG, 'r', encoding='utf-8') as fh:
    events = json.load(fh)

def vwap(side_dict):
    # size-weighted price from minp/maxp not available; compute from usdc/size
    if side_dict and side_dict["size"]>0:
        return side_dict["usdc"]/side_dict["size"]
    return None

results = []
for es, e in events.items():
    conds = e["conds"]
    n_sub = len(conds)
    merge_usdc = e["merge_usdc"]; conv_usdc = e["conv_usdc"]; redeem_usdc = e["redeem_usdc"]
    split_usdc = e["split_usdc"]
    buy_usdc = e["trade_buy_usdc"]; sell_usdc = e["trade_sell_usdc"]

    # ---- (A) per-conditionId merge spread (MEASURED) ----
    merge_locked = 0.0
    merge_detail = []
    for cid, c in conds.items():
        if c["merge_size"] <= 0:
            continue
        # gather BUY vwap per label on this cond
        labels = list(c["out"].keys())
        buy_vwaps = {}
        for lab, od in c["out"].items():
            b = od.get("BUY")
            if b and b["size"]>0:
                buy_vwaps[lab] = b["usdc"]/b["size"]
        # need two opposing legs both bought
        if len(buy_vwaps) >= 2:
            # take the two cheapest-summing pair (the binary has 2 labels)
            vs = sorted(buy_vwaps.values())
            psum = vs[0] + vs[1]
            spread = (1.0 - psum) * c["merge_size"]
            merge_locked += spread
            merge_detail.append({
                "cid": cid, "title": c["title"], "merge_size": round(c["merge_size"],4),
                "buy_vwaps": {k: round(v,5) for k,v in buy_vwaps.items()},
                "p_sum": round(psum,5), "spread": round(spread,2)
            })
        else:
            # only one side bought; the merge still realized $1 but other leg cost
            # unknown from this event window -> approximate with merge_usdc share
            merge_detail.append({
                "cid": cid, "title": c["title"], "merge_size": round(c["merge_size"],4),
                "buy_vwaps": {k: round(v,5) for k,v in buy_vwaps.items()},
                "p_sum": None, "spread": None, "note": "single-leg buy visible"
            })

    # ---- (B) neg-risk NO conversion (MEASURED notional, INFERRED spread) ----
    # No-buy cost across event
    no_buy_usdc = 0.0; no_buy_size = 0.0
    yes_buy_usdc = 0.0; yes_buy_size = 0.0
    for cid, c in conds.items():
        for lab, od in c["out"].items():
            b = od.get("BUY")
            if not b: continue
            ll = lab.lower()
            if ll == "no":
                no_buy_usdc += b["usdc"]; no_buy_size += b["size"]
            elif ll == "yes":
                yes_buy_usdc += b["usdc"]; yes_buy_size += b["size"]
    # conversion-implied lock (proxy): USDC released by conversions minus cost of NOs bought
    conv_locked_proxy = conv_usdc - no_buy_usdc  # flagged inferred

    # ---- (C) complete-set dutch book (via redeem) ----
    # if REDEEMs present and many subs and buys spread across subs
    # estimate sum of one-share YES vwaps
    yes_like_vwaps = []
    for cid, c in conds.items():
        # dominant bought label per cond
        best = None
        for lab, od in c["out"].items():
            b = od.get("BUY")
            if b and b["size"]>0:
                vw = b["usdc"]/b["size"]
                if best is None or b["size"]>best[2]:
                    best = (lab, vw, b["size"])
        if best:
            yes_like_vwaps.append(best[1])
    dutch_sum = sum(yes_like_vwaps) if yes_like_vwaps else None

    # ---- choose dominant pattern ----
    channels = {
        "yes+no merge": merge_usdc,
        "neg-risk NO conversion": conv_usdc,
        "complete-set dutch book / hold-to-redeem": redeem_usdc,
    }
    dom = max(channels, key=lambda k: channels[k])
    dom_val = channels[dom]
    if dom_val <= 0 and (buy_usdc>0 or sell_usdc>0):
        dom = "trade-only (no lock channel)"

    # refine: if dom is redeem-channel, decide dutch-book vs hold-to-redeem
    pattern = dom
    if dom.startswith("complete-set"):
        if n_sub >= 3 and dutch_sum is not None:
            pattern = "complete-set dutch book"
        else:
            pattern = "hold-to-redeem"

    # best locked-spread estimate per chosen identity
    if pattern == "yes+no merge":
        locked = merge_locked
        locked_basis = "MEASURED: sum_c merge_size*(1-buy_vwap_A-buy_vwap_B)"
    elif pattern == "neg-risk NO conversion":
        locked = conv_locked_proxy
        locked_basis = "INFERRED proxy: conversion_usdc - No_buy_usdc (k-per-tx not in activity)"
    elif pattern == "complete-set dutch book":
        # locked per redeemed set ~ (1 - dutch_sum) * redeemed_set_size; redeemed_set_size unknown
        # use buy basket: profit upper bound = (#subs payoff $1 once) ; report (1-dutch_sum)
        locked = None
        locked_basis = "INFERRED: (1 - sum_yes_vwap) per set; set size not isolalso in activity"
    else:
        locked = None
        locked_basis = "n/a (directional / trade-only)"

    # category heuristic from slug/title
    sample_title = max(e["titles"], key=lambda k: e["titles"][k]) if e["titles"] else ""
    slugl = (es or "").lower(); titl = sample_title.lower()
    def cat():
        if any(k in slugl for k in ["fifwc","nba","nfl","mlb","nhl","epl","ucl","soccer","-vs-","champion","cup","series","-win","match","game","fight","ufc","tennis","golf"]) or any(k in titl for k in ["exact score","o/u","vs.","total","win the","champion"]):
            return "sports"
        if any(k in slugl for k in ["election","president","senate","governor","primary","nominee","mayor","parliament","vote","poll","democrat","republican"]):
            return "election/politics"
        if any(k in slugl for k in ["gpt","llm","model","ai-","openai","anthropic","gemini","grok","claude","benchmark","lmsys","arena"]) or "ai " in titl or "model" in titl:
            return "AI-model"
        if any(k in slugl for k in ["temperature","temp-","weather","highest-temp","rain","snow","degrees","nyc-temp","celsius","fahrenheit"]) or "temperature" in titl or "highest temp" in titl:
            return "weather"
        if any(k in slugl for k in ["fed","rate","cpi","inflation","gdp","jobs","recession","fomc","interest"]):
            return "macro/Fed"
        if any(k in slugl for k in ["bitcoin","btc","ethereum","eth","crypto","valuation","acquisition","ipo","market-cap","stock","price-on","reach"]) or "acquisition" in titl or "valuation" in titl:
            return "company/crypto"
        return "other"

    results.append({
        "eventSlug": es,
        "sample_title": sample_title,
        "category": cat(),
        "n_submarkets": n_sub,
        "pattern": pattern,
        "merge_usdc": round(merge_usdc,2),
        "merge_count": e["merge_count"],
        "merge_size": round(e["merge_size"],2),
        "merge_locked_measured": round(merge_locked,2),
        "conv_usdc": round(conv_usdc,2),
        "conv_count": e["conv_count"],
        "conv_size": round(e["conv_size"],2),
        "conv_locked_proxy": round(conv_locked_proxy,2),
        "redeem_usdc": round(redeem_usdc,2),
        "redeem_count": e["redeem_count"],
        "split_usdc": round(split_usdc,2),
        "buy_usdc": round(buy_usdc,2),
        "sell_usdc": round(sell_usdc,2),
        "no_buy_usdc": round(no_buy_usdc,2),
        "yes_buy_usdc": round(yes_buy_usdc,2),
        "dutch_sum_yes_vwap": round(dutch_sum,5) if dutch_sum is not None else None,
        "locked_estimate": (round(locked,2) if isinstance(locked,(int,float)) else None),
        "locked_basis": locked_basis,
        "min_ts": e["min_ts"], "max_ts": e["max_ts"],
        "merge_detail": merge_detail,
    })

with open(OUT, 'w', encoding='utf-8') as o:
    json.dump(results, o, ensure_ascii=False)

# global pattern breakdown
from collections import defaultdict
pc = defaultdict(int); pu = defaultdict(float)
for r in results:
    pc[r["pattern"]] += 1
    # attribute realized usdc to pattern channel
    if r["pattern"] == "yes+no merge":
        pu[r["pattern"]] += r["merge_usdc"]
    elif r["pattern"] == "neg-risk NO conversion":
        pu[r["pattern"]] += r["conv_usdc"]
    else:
        pu[r["pattern"]] += r["redeem_usdc"]

print("=== PATTERN BREAKDOWN (event counts / channel usdc) ===")
for p in sorted(pc, key=lambda k:-pu[k]):
    print(f"{p:45s}  events={pc[p]:5d}  channel_usdc={pu[p]:14,.2f}")
print("total events:", len(results))

# category breakdown
cc = defaultdict(int)
for r in results: cc[r["category"]] += 1
print("\n=== CATEGORY counts ===")
for c in sorted(cc, key=lambda k:-cc[k]):
    print(f"{c:20s} {cc[c]}")
print("WROTE", OUT, file=sys.stderr)
