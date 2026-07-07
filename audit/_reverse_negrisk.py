# -*- coding: utf-8 -*-
"""Reverse 0xp3nny neg-risk: arb threshold at entry + build-order from RAW per-trade rows."""
import ijson, io, sys, json, datetime
from decimal import Decimal
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"  # 0xp3nny

NEG = {
 "when-will-gpt-5pt6-be-released":          ("ep_4","GPT-5.6",6),
 "highest-temperature-in-seoul-on-june-9-2026":("ep_5","Seoul temp",7),
 "fed-decision-in-july-181":                ("ep_6","Fed July",5),
 "daegu-mayoral-election-winner":           ("ep_7","Daegu mayor",2),
 "colombia-presidential-election":          ("ep_8","Colombia",8),
 "spacex-closing-market-cap-end-of-ipo-month-20260606222757973":("ep_9","SpaceX",8),
 "2026-nba-champion":                       ("ep_10","NBA champ",14),
 "elon-musk-of-tweets-june-22-june-24":     ("ep_13","Elon Jun22-24",7),
 "elon-musk-of-tweets-june-1-june-3":       ("ep_14","Elon Jun1-3",8),
}

def f(x):
    try: return float(x)
    except: return 0.0
def iso(ts):
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%SZ")
    except: return None

# per event: list of his trades (only TRADE rows). Keep raw price per trade.
trades = {s: [] for s in NEG}
nrows=0; nhit=0
with open(RAW,'rb') as fh:
    for rec in ijson.items(fh,'item'):
        nrows+=1
        if rec.get('proxyWallet') != WALLET:   # single-wallet expected but guard
            continue
        es = rec.get('eventSlug') or rec.get('slug') or ""
        if es not in NEG: continue
        if rec.get('type') != "TRADE": continue
        nhit+=1
        trades[es].append({
            "ts": int(rec.get('timestamp')) if rec.get('timestamp') is not None else None,
            "cid": rec.get('conditionId') or "",
            "title": rec.get('title') or "",
            "outcome": rec.get('outcome') or "",
            "side": rec.get('side') or "",
            "price": f(rec.get('price')),
            "size": f(rec.get('size')),
            "usdc": f(rec.get('usdcSize')),
        })
        if nrows % 2000000==0: print(f"...{nrows} rows scanned",file=sys.stderr)
print(f"scanned {nrows} rows, kept {nhit} of-interest TRADE rows",file=sys.stderr)

OUT={}
for es,(ep,name,N) in NEG.items():
    T=sorted(trades[es], key=lambda r:(r["ts"] if r["ts"] is not None else 0))
    # effective NO price per trade:
    #   ("No","BUY")  -> NO bought at price p
    #   ("Yes","SELL")-> selling YES at price q == acquiring NO at (1-q)
    #   ("No","SELL") -> reducing NO (ignore for basket build, note as adjust)
    #   ("Yes","BUY") -> long YES (against; rare, note)
    # We track per-submarket (cid) cumulative NO-equivalent size & cost, and first NO-entry.
    per_cid = {}   # cid -> dict
    order=[]       # first NO-entry events in time order
    seen=set()
    no_build_trades=[]  # chronological NO-building trades w/ eff price
    for r in T:
        side=r["side"]; out=r["outcome"]
        is_no_build=False; effp=None
        if out=="No" and side=="BUY":
            is_no_build=True; effp=r["price"]
        elif out=="Yes" and side=="SELL":
            is_no_build=True; effp=(1.0-r["price"])  # NO-equiv entry price
        if is_no_build and effp is not None and r["size"]>0:
            cid=r["cid"]
            d=per_cid.setdefault(cid,{"title":r["title"],"size":0.0,"cost":0.0,
                                      "first_ts":r["ts"],"first_price":effp,"first_path":out+"/"+side,"ntr":0})
            d["size"]+=r["size"]; d["cost"]+=effp*r["size"]; d["ntr"]+=1
            no_build_trades.append({"ts":r["ts"],"cid":cid,"effp":round(effp,4),
                                    "size":round(r["size"],2),"path":out+"/"+side,
                                    "title":r["title"]})
            if cid not in seen:
                seen.add(cid)
                order.append({"cid":cid,"title":r["title"],"first_ts":r["ts"],
                              "first_eff_price":round(effp,4),"path":out+"/"+side,
                              "first_size":round(r["size"],2)})
    # cumulative basket cost using per-cid running VWAP at the time each NEW leg is added
    # marginal_at_completion = (N-1) - sum(first-trade eff price of each cid) -> snapshot entry threshold
    # also compute marginal using each cid's full-window NO-equiv VWAP
    for cid,d in per_cid.items():
        d["vwap"]=d["cost"]/d["size"] if d["size"]>0 else None

    legs_covered=len(per_cid)
    sum_first = sum(o["first_eff_price"] for o in order)            # threshold at first-touch of each leg
    sum_vwap  = sum((per_cid[c]["vwap"] or 0) for c in per_cid)     # full-window basket cost
    marg_first = (N-1) - sum_first
    marg_vwap  = (N-1) - sum_vwap

    # "marginal at the moment he STARTS building" = after he has at least put first share on
    # each leg. Reconstruct running marginal as he completes the basket: after k-th distinct
    # leg first-entered, partial sum; report the marginal once basket first complete.
    running=[]
    s=0.0
    for i,o in enumerate(order):
        s+=o["first_eff_price"]
        running.append({"k":i+1,"cid_title":o["title"][:42],
                        "eff_price":o["first_eff_price"],
                        "partial_sum_NO":round(s,4),
                        "marginal_if_NminusK": round((i+1-1)-s,4)})  # (k-1)-partial, mid-build
    # price-order direction of the sweep:
    fav_first = None
    if len(order)>=2:
        # high price (~favorite NO, longshot YES) vs low price
        first_two=[o["first_eff_price"] for o in order[:max(2,N//3)]]
        last_two =[o["first_eff_price"] for o in order[-max(2,N//3):]]
        fav_first = "HIGH-price-NO(longshot outcome) FIRST" if (sum(first_two)/len(first_two))>(sum(last_two)/len(last_two)) else "LOW-price-NO(favorite outcome) FIRST"

    OUT[es]={
        "ep":ep,"name":name,"N":N,
        "n_no_build_trades":len(no_build_trades),
        "legs_covered_NO":legs_covered,
        "covered_all_N": legs_covered==N,
        "window":{"first":iso(order[0]["first_ts"]) if order else None,
                  "last":iso(no_build_trades[-1]["ts"]) if no_build_trades else None},
        "sum_first_entry_NO":round(sum_first,4),
        "marginal_at_first_entries":round(marg_first,4),   # (N-1)-Σ(first-touch NO price)
        "sum_window_vwap_NO":round(sum_vwap,4),
        "marginal_window_vwap":round(marg_vwap,4),         # (N-1)-Σ(full-window NO vwap)
        "sum_YES_equiv_implied": round(N - sum_vwap,4),    # Σ(1-noVWAP)=Σ YES-equiv; >1 surplus check is via marg
        "build_order": order,         # time-ordered first NO entry per leg w/ price
        "running_build": running,
        "sweep_direction": fav_first,
    }
    # console summary
    print(f"\n===== {ep} {name} N={N} =====")
    print(f"  NO-build trades: {len(no_build_trades)}  legs covered: {legs_covered}/{N}  all? {legs_covered==N}")
    print(f"  Σ(first-entry NO) = {sum_first:.4f}  -> marginal@firstentry (N-1)-Σ = {marg_first:.4f}")
    print(f"  Σ(window-vwap NO) = {sum_vwap:.4f}  -> marginal@windowvwap = {marg_vwap:.4f}")
    print(f"  sweep: {fav_first}")
    print(f"  BUILD ORDER (time-sorted, eff NO entry price):")
    for o in order:
        print(f"    {iso(o['first_ts'])}  p={o['first_eff_price']:.4f}  [{o['path']:8s}] {o['title'][:54]}")

json.dump(OUT, open(r"C:\Users\zexi\pmscan\audit\_reverse_negrisk_out.json","w",encoding='utf-8'),
          ensure_ascii=False, indent=1)
print("\nWROTE _reverse_negrisk_out.json", file=sys.stderr)
