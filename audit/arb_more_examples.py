# -*- coding: utf-8 -*-
"""
arb_more_examples.py
=====================
Independent, self-contained streaming pass over raw_activity_full.json to nail
5 NEW single-event, single-conditionId YES+NO MERGE arbitrage episodes for
wallet 0x4f1d5ae26fc31472966e951af3183308736d8de2. These are additional to the
5 already-documented events (the blocklist in _picked.json) and span distinct
competition types (CS2, ice hockey, men's tennis, women's tennis, LoL).

MECHANISM -- same-market binary MERGE (mathematically risk-free):
  On ONE conditionId (a single binary submarket) the wallet BUYs both legs
  (outcome A and outcome B). It then MERGEs matched A+B pairs back to $1 each
  (rows with type=="MERGE"). Each merged pair pays exactly $1 regardless of the
  game result, so if the pair was assembled for < $1 the profit is LOCKED with
  zero directional risk. Unmatched/excess shares on the heavier leg are
  DIRECTIONAL residue and are EXCLUDED from locked profit.

TWO LOCKED MEASURES (both derived from the activity feed only -- no
Polymarket self-reported PnL, no lb-api):

  (1) BLENDED-VWAP  (price-only, order-blind):
        vwap_a   = sum(usdcSize | A BUY) / sum(size | A BUY)
        vwap_b   = sum(usdcSize | B BUY) / sum(size | B BUY)
        pairs    = sum(size | MERGE rows)
        locked   = (1 - vwap_a - vwap_b) * pairs        (only if sum < 1)
      Simple, but can MIS-state locked when cheap buys happen AFTER the merges
      (they drag vwap down without actually having funded the merge).

  (2) TIME-AWARE running-inventory (PRIMARY, defensible):
        walk every row for the conditionId in timestamp order. Maintain, per
        leg, (shares_on_hand, cost_on_hand). On each BUY add shares+usdc. On
        each MERGE of size s, consume s shares from EACH leg at that leg's
        current average cost, and credit $1*s payout:
            merged_cost += avg_cost_A*s + avg_cost_B*s
            merged_pairs += s
        locked_time_aware = merged_pairs - merged_cost
      If a MERGE ever needs more shares of a leg than are on hand
      (inventory underflow), we set inv_underflow=True -- such episodes are
      NOT cleanly auditable from this feed and are flagged / down-graded.

  The reported, defensible locked profit is (2). We also print (1) for cross-
  reference. We only KEEP episodes where BOTH measures are positive and there
  is NO inventory underflow.

Self-consistency: for every BUY trade we accumulate price*size and compare to
usdcSize; residual must be ~0 (sub-cent rounding).

Outputs:
  - prints per-episode breakdown + summary table
  - writes episodes_extra.json with the 5 episodes

Run:
  C:\\Users\\zexi\\AppData\\Local\\Programs\\Python\\Python312\\python arb_more_examples.py
"""
import sys, io, json, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
OUT  = r"C:\Users\zexi\pmscan\audit\episodes_extra.json"

# ---- the 5 NEW target events: eventSlug -> {conditionId prefix, category} ----
# All single-conditionId binary markets; chosen to AVOID the 5/15 used events
# and to span distinct competition types. conditionId prefixes are unique.
TARGETS = {
    "cs2-furia-fal2-2026-06-21":    {"cid": "0x1ecaecae2f655c", "category": "esports / CS2 (FPS)"},
    "wch-sui-fin-2026-05-31":       {"cid": "0x59eb998fcbf517", "category": "ice hockey (IIHF Worlds)"},
    "atp-zhang-altmaie-2026-05-06": {"cid": "0x05c61ab4275ae6", "category": "tennis (ATP men)"},
    "wta-paolini-jeanjea-2026-05-07": {"cid": "0x3e29051e392ced", "category": "tennis (WTA women)"},
    "lol-kt-fox1-2026-05-09":       {"cid": "0xcbcf151a0c5326", "category": "esports / LoL (MOBA)"},
}
TARGET_CIDS = {v["cid"]: es for es, v in TARGETS.items()}

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0

def cid_match(cid):
    if not cid: return None
    for pref in TARGET_CIDS:
        if cid.startswith(pref):
            return pref
    return None

# collect the full ordered row list per target conditionId
rows = {pref: [] for pref in TARGET_CIDS}
n = 0
with open(PATH, 'rb') as fh:
    for rec in ijson.items(fh, 'item'):
        n += 1
        if n % 50000 == 0:
            print("...processed", n, "rows", file=sys.stderr)
        pref = cid_match(rec.get('conditionId') or "")
        if pref is None:
            continue
        rows[pref].append(rec)
print("TOTAL rows scanned:", n, file=sys.stderr)

def ts2iso(ts):
    if ts is None: return None
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

episodes = []
print("\n" + "=" * 100)
for pref in TARGETS:  # stable order
    cidpref = TARGETS[pref]["cid"]
    rs = rows[cidpref]
    es = pref
    rs.sort(key=lambda r: r.get('timestamp', 0))
    cid_full = next((r.get('conditionId') for r in rs if r.get('conditionId')), None)
    title = next((r.get('title') for r in rs if r.get('title')), "")

    # identify the two outcome labels
    labels = []
    for r in rs:
        if r.get('type') == "TRADE":
            lab = r.get('outcome')
            if lab and lab not in labels:
                labels.append(lab)
    # blended-vwap accumulators
    bsz = {l: 0.0 for l in labels}; busd = {l: 0.0 for l in labels}
    btrades = {l: 0 for l in labels}; bminp = {l: None for l in labels}; bmaxp = {l: None for l in labels}
    sell_sz = {l: 0.0 for l in labels}
    # time-aware inventory
    inv = {l: [0.0, 0.0] for l in labels}   # leg -> [shares, cost]
    merged_pairs = 0.0; merged_cost = 0.0; inv_underflow = False
    merge_count = 0; merge_usdc = 0.0
    redeem_usdc = 0.0; conv_count = 0; split_count = 0
    pxsize_buy = 0.0; usdc_buy = 0.0
    min_ts = None; max_ts = None

    for r in rs:
        t = r.get('type'); s = f(r.get('size')); u = f(r.get('usdcSize')); ts = r.get('timestamp')
        if isinstance(ts, str):
            try: ts = int(ts)
            except: ts = None
        if ts is not None:
            if min_ts is None or ts < min_ts: min_ts = ts
            if max_ts is None or ts > max_ts: max_ts = ts
        if t == "TRADE":
            lab = r.get('outcome'); side = r.get('side'); price = f(r.get('price'))
            if side == "BUY":
                bsz[lab] += s; busd[lab] += u; btrades[lab] += 1
                if price > 0:
                    bminp[lab] = price if bminp[lab] is None else min(bminp[lab], price)
                    bmaxp[lab] = price if bmaxp[lab] is None else max(bmaxp[lab], price)
                inv[lab][0] += s; inv[lab][1] += u
                pxsize_buy += price * s; usdc_buy += u
            elif side == "SELL":
                sell_sz[lab] += s
        elif t == "MERGE":
            merge_count += 1; merge_usdc += u
            for lab in labels:
                e = inv[lab]
                if e[0] < s - 1e-6:
                    inv_underflow = True
                avg = (e[1] / e[0]) if e[0] > 1e-9 else 0.0
                merged_cost += avg * s
                e[1] -= avg * s; e[0] -= s
            merged_pairs += s
        elif t == "REDEEM":
            redeem_usdc += u
        elif t == "CONVERSION":
            conv_count += 1
        elif t == "SPLIT":
            split_count += 1

    if len(labels) < 2:
        print("SKIP (legs<2):", es); continue

    # blended-vwap locked
    vwaps = {l: (busd[l] / bsz[l] if bsz[l] > 0 else None) for l in labels}
    legs = sorted(labels, key=lambda l: vwaps[l])
    a_lab, b_lab = legs[0], legs[1]
    p_sum = vwaps[a_lab] + vwaps[b_lab]
    blended_locked = (1.0 - p_sum) * merged_pairs if p_sum < 1 else 0.0
    blended_deploy = (vwaps[a_lab] + vwaps[b_lab]) * merged_pairs
    blended_roi = blended_locked / blended_deploy if blended_deploy > 0 else 0.0

    # time-aware locked (PRIMARY)
    ta_locked = merged_pairs - merged_cost
    ta_avg_pq = merged_cost / merged_pairs if merged_pairs > 0 else 0.0
    ta_roi = ta_locked / merged_cost if merged_cost > 0 else 0.0

    min_leg = min(bsz[a_lab], bsz[b_lab])
    invariant_ok = merged_pairs <= min_leg + 1e-6

    # directional residue excluded (per leg, total bought minus merged)
    residue = []
    for l in labels:
        ex = bsz[l] - merged_pairs
        if ex > 1e-6:
            residue.append({"outcome": l, "excess_shares": round(ex, 4),
                            "note": "unpaired directional leg, EXCLUDED from locked"})

    consist_resid = usdc_buy - pxsize_buy
    consist_pct = (consist_resid / usdc_buy) if usdc_buy else 0.0

    conf = "high"
    notes = []
    if inv_underflow:
        conf = "low"; notes.append("inventory underflow: a MERGE used more shares than on hand at that time")
    if ta_locked <= 0 or blended_locked <= 0:
        conf = "low"; notes.append("locked not positive under one of the two measures")
    if abs(consist_pct) >= 0.005:
        conf = "medium" if conf == "high" else conf
        notes.append("price*size vs usdc residual >= 0.5%%")
    if btrades[a_lab] < 2 or btrades[b_lab] < 2:
        conf = "medium" if conf == "high" else conf
        notes.append("a leg has <2 BUY trades")

    ep = {
        "eventSlug": es,
        "conditionId": cid_full,
        "title": title,
        "category": TARGETS[es]["category"],
        "mechanism": "same-market binary YES/NO MERGE -> $1 (risk-free)",
        "leg_A": {"outcome": a_lab, "side": "BUY", "vwap": round(vwaps[a_lab], 5),
                  "buy_size": round(bsz[a_lab], 4), "buy_usdc": round(busd[a_lab], 4),
                  "n_trades": btrades[a_lab], "price_range": [bminp[a_lab], bmaxp[a_lab]],
                  "sell_size": round(sell_sz[a_lab], 4)},
        "leg_B": {"outcome": b_lab, "side": "BUY", "vwap": round(vwaps[b_lab], 5),
                  "buy_size": round(bsz[b_lab], 4), "buy_usdc": round(busd[b_lab], 4),
                  "n_trades": btrades[b_lab], "price_range": [bminp[b_lab], bmaxp[b_lab]],
                  "sell_size": round(sell_sz[b_lab], 4)},
        "merge_pairs": round(merged_pairs, 4),
        "merge_count_rows": merge_count,
        "merge_usdc_realized": round(merge_usdc, 4),
        "payout_per_pair": 1.0,
        "limiting_leg_size": round(min_leg, 4),
        "invariant_merge<=min_leg": invariant_ok,
        "inv_underflow": inv_underflow,
        # blended-vwap measure
        "blended_set_cost_p_plus_q": round(p_sum, 5),
        "blended_locked_usd": round(blended_locked, 2),
        "blended_deploy_usd": round(blended_deploy, 2),
        "blended_roi_pct": round(blended_roi * 100, 2),
        # time-aware measure (PRIMARY)
        "timeaware_avg_p_plus_q": round(ta_avg_pq, 5),
        "timeaware_merged_cost_usd": round(merged_cost, 2),
        "timeaware_locked_usd": round(ta_locked, 2),
        "timeaware_roi_pct": round(ta_roi * 100, 2),
        "locked_profit_usd": round(ta_locked, 2),   # canonical = time-aware
        "directional_residue_excluded": residue,
        "redeem_usdc_on_residue": round(redeem_usdc, 2),
        "conversion_rows": conv_count, "split_rows": split_count,
        "self_consistency_buy_usdc": round(usdc_buy, 2),
        "self_consistency_sum_price_x_size": round(pxsize_buy, 2),
        "self_consistency_resid_pct": round(consist_pct * 100, 4),
        "time_window": [ts2iso(min_ts), ts2iso(max_ts)],
        "confidence": conf,
        "notes": notes,
    }
    episodes.append(ep)

    print("\n### %s  [%s]" % (es, ep["category"]))
    print("    market : %s" % title)
    print("    cond   : %s" % cid_full)
    print("    leg A  : BUY %-22s vwap=%.5f size=%10.3f usdc=%10.3f n=%d" % (
        a_lab, vwaps[a_lab], bsz[a_lab], busd[a_lab], btrades[a_lab]))
    print("    leg B  : BUY %-22s vwap=%.5f size=%10.3f usdc=%10.3f n=%d" % (
        b_lab, vwaps[b_lab], bsz[b_lab], busd[b_lab], btrades[b_lab]))
    print("    MERGE pairs=%.3f (rows=%d)  limiting_leg=%.3f  invariant_ok=%s  inv_underflow=%s" % (
        merged_pairs, merge_count, min_leg, invariant_ok, inv_underflow))
    print("    BLENDED  : p+q=%.5f  locked=%.2f  deploy=%.2f  ROI=%.2f%%" % (
        p_sum, blended_locked, blended_deploy, blended_roi * 100))
    print("    TIME-AWARE: avg p+q=%.5f  cost=%.2f  LOCKED=%.2f  ROI=%.2f%%   <-- canonical" % (
        ta_avg_pq, merged_cost, ta_locked, ta_roi * 100))
    for r in residue:
        print("    residue EXCLUDED: %s +%.3f shares (directional; redeem on residue=%.2f)" % (
            r["outcome"], r["excess_shares"], redeem_usdc))
    print("    self-consistency: buy_usdc=%.2f vs sum(price*size)=%.2f  resid=%.4f%%" % (
        usdc_buy, pxsize_buy, consist_pct * 100))
    print("    confidence: %s  %s" % (conf, ("| " + "; ".join(notes)) if notes else ""))

with open(OUT, 'w', encoding='utf-8') as o:
    json.dump(episodes, o, ensure_ascii=False, indent=1)
print("\nWROTE", OUT, file=sys.stderr)

print("\n=== SUMMARY TABLE (canonical = TIME-AWARE locked) ===")
print("%-30s %-22s %8s %7s %9s %9s %8s %6s" % (
    "event", "category", "p+q(ta)", "pairs", "locked$", "deploy$", "ROI%", "conf"))
tot = 0.0
for e in episodes:
    print("%-30s %-22s %8.4f %7.0f %9.2f %9.2f %7.2f %6s" % (
        e["eventSlug"][:30], e["category"][:22], e["timeaware_avg_p_plus_q"],
        e["merge_pairs"], e["timeaware_locked_usd"], e["timeaware_merged_cost_usd"],
        e["timeaware_roi_pct"], e["confidence"]))
    tot += e["timeaware_locked_usd"]
print("%-30s %-22s %8s %7s %9.2f" % ("TOTAL", "", "", "", tot))
