# -*- coding: utf-8 -*-
"""
arb_episodes.py
1) Select a DIVERSE top-15 from arb_event_scored.json preferring cleanly
   computable, demonstrable locked-profit episodes.
2) Stream raw_activity_full.json again to collect ALL raw lifecycle rows
   (MERGE/CONVERSION/SPLIT/REDEEM) + per-leg TRADE detail for the chosen events.
3) Write audit/episodes/ep_{idx}.json (idx 0..14) and episodes_manifest.json.
"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
SCORED = r"C:\Users\zexi\pmscan\audit\arb_event_scored.json"
EPDIR = r"C:\Users\zexi\pmscan\audit\episodes"
MAN = r"C:\Users\zexi\pmscan\audit\episodes_manifest.json"
os.makedirs(EPDIR, exist_ok=True)

scored = json.load(open(SCORED, encoding='utf-8'))
by_slug = {r["eventSlug"]: r for r in scored}

def fnum(x):
    try: return float(x)
    except: return 0.0

def isots(ts):
    import datetime
    try: return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%SZ")
    except: return None

# ---------- SELECTION ----------
# Score "computability": merge events with positive locked, or clean full-set
# conversions with positive per_set. Rank within category, then diversify.
def computable_locked(r):
    if r["pattern"]=="yes+no merge":
        return r["merge_locked_measured"], "merge MEASURED"
    if r["pattern"]=="neg-risk NO conversion":
        if r["conv_clean"] and r["conv_per_set"] is not None and r["conv_per_set"]>0:
            return (r["conv_locked"] or 0.0), "conv MEASURED full-set"
        if r["conv_per_set"] is not None and r["conv_per_set"]>0 and r["conv_locked"]:
            return r["conv_locked"], "conv INFERRED partial"
    if r["pattern"]=="complete-set dutch book":
        return r["redeem_usdc"], "dutch redeem MEASURED-realized"
    return 0.0, "n/a"

cand=[]
for r in scored:
    lk, basis = computable_locked(r)
    rr=dict(r); rr["_lk"]=lk; rr["_lkbasis"]=basis
    cand.append(rr)

# require some real arb activity volume
def vol(r): return max(r["merge_usdc"], r["conv_usdc"], r["redeem_usdc"], r["buy_usdc"])

# Buckets we want represented (diverse)
wanted = ["sports","election/politics","AI-model","weather","macro/Fed","company/crypto"]

# Build per-category ranked lists, prefer positive computable locked & decent volume
def good(r):
    return r["_lk"]>0 and vol(r)>1500
ranked = sorted([r for r in cand if good(r)], key=lambda r:-r["_lk"])

picked=[]
picked_slugs=set()

# 1) Guarantee diversity: best from each wanted category, varied patterns
#    Try to include at least one merge-pattern and one dutch-book and several conversion.
def take(pred, n=1):
    cnt=0
    for r in ranked:
        if cnt>=n: break
        if r["eventSlug"] in picked_slugs: continue
        if pred(r):
            picked.append(r); picked_slugs.add(r["eventSlug"]); cnt+=1
    return cnt

# at least one strong merge episode (sports exact-score) and crypto-merge
take(lambda r: r["pattern"]=="yes+no merge" and r["category"]=="sports", 2)
take(lambda r: r["pattern"]=="yes+no merge" and r["category"]=="company/crypto", 1)
# one dutch book (sports more-markets)
take(lambda r: r["pattern"]=="complete-set dutch book", 1)
# AI-model conversion
take(lambda r: r["category"]=="AI-model", 1)
# weather
take(lambda r: r["category"]=="weather", 1)
# macro/Fed conversion
take(lambda r: r["category"]=="macro/Fed", 1)
# election/politics conversion (clean full-set if possible)
take(lambda r: r["category"]=="election/politics" and r["conv_clean"], 1)
take(lambda r: r["category"]=="election/politics", 1)
# company/crypto conversion (largest-company / valuation)
take(lambda r: r["category"]=="company/crypto" and r["pattern"]=="neg-risk NO conversion", 1)

# 2) fill remainder with highest computable locked, keeping category spread
from collections import Counter
catcount=Counter(r["category"] for r in picked)
for r in ranked:
    if len(picked)>=15: break
    if r["eventSlug"] in picked_slugs: continue
    # soft cap 4 per category to stay diverse
    if catcount[r["category"]]>=4: continue
    picked.append(r); picked_slugs.add(r["eventSlug"]); catcount[r["category"]]+=1

# if still <15 relax the cap
for r in ranked:
    if len(picked)>=15: break
    if r["eventSlug"] in picked_slugs: continue
    picked.append(r); picked_slugs.add(r["eventSlug"])

picked = picked[:15]
print("SELECTED", len(picked), "episodes:")
for i,r in enumerate(picked):
    print(f" [{i:2d}] {r['category']:16s} {r['pattern']:24s} lk={r['_lk']:10,.2f} {r['eventSlug'][:46]}")

target_slugs = set(r["eventSlug"] for r in picked)
json.dump({"picked":[r["eventSlug"] for r in picked]}, open(r"C:\Users\zexi\pmscan\audit\_picked.json","w",encoding='utf-8'))

# ---------- COLLECT RAW ROWS for target events ----------
raw = {s: {"trades":{}, "lifecycle":[]} for s in target_slugs}
# trades keyed by (conditionId, outcome, side) -> aggregation; lifecycle = raw rows
def leg_key(cid,out,side): return cid+"|"+out+"|"+side

n=0
with open(RAW,'rb') as fh:
    for rec in ijson.items(fh,'item'):
        n+=1
        es=rec.get('eventSlug') or rec.get('slug') or rec.get('conditionId')
        if es not in raw: continue
        t=rec.get('type')
        if t=="TRADE":
            cid=rec.get('conditionId') or ""
            out=rec.get('outcome') or ("idx"+str(rec.get('outcomeIndex')))
            side=rec.get('side') or ""
            k=leg_key(cid,out,side)
            d=raw[es]["trades"].setdefault(k,{
                "conditionId":cid,"title":rec.get('title') or "","outcome":out,"side":side,
                "trade_count":0,"total_size":0.0,"total_usdc":0.0,"min_ts":None,"max_ts":None})
            d["trade_count"]+=1
            d["total_size"]+=fnum(rec.get('size'))
            d["total_usdc"]+=fnum(rec.get('usdcSize'))
            ts=rec.get('timestamp')
            try: ts=int(ts)
            except: ts=None
            if ts is not None:
                d["min_ts"]=ts if d["min_ts"] is None else min(d["min_ts"],ts)
                d["max_ts"]=ts if d["max_ts"] is None else max(d["max_ts"],ts)
        elif t in ("MERGE","SPLIT","CONVERSION","REDEEM"):
            raw[es]["lifecycle"].append({
                "type":t,"timestamp":rec.get('timestamp'),"ts_iso":isots(rec.get('timestamp')),
                "conditionId":rec.get('conditionId') or "","title":rec.get('title') or "",
                "size":fnum(rec.get('size')),"usdcSize":fnum(rec.get('usdcSize')),
                "transactionHash":rec.get('transactionHash')})
        if n%50000==0: print("...raw pass",n,file=sys.stderr)
print("raw pass done rows",n,file=sys.stderr)

# ---------- WRITE DETAIL FILES ----------
def time_window(rows_minmax):
    lo=min([m for m in rows_minmax if m is not None], default=None)
    hi=max([m for m in rows_minmax if m is not None], default=None)
    return lo,hi

manifest=[]
for idx,r in enumerate(picked):
    es=r["eventSlug"]
    rawe=raw[es]
    legs=[]
    tsvals=[]
    for k,d in rawe["trades"].items():
        vw=d["total_usdc"]/d["total_size"] if d["total_size"]>0 else None
        legs.append({
            "conditionId":d["conditionId"],"submarket_title":d["title"],
            "outcome":d["outcome"],"side":d["side"],"trade_count":d["trade_count"],
            "size_weighted_avg_price":(round(vw,5) if vw is not None else None),
            "total_size":round(d["total_size"],4),"total_usdc":round(d["total_usdc"],2),
            "ts_start":isots(d["min_ts"]),"ts_end":isots(d["max_ts"])})
        tsvals+= [d["min_ts"],d["max_ts"]]
    legs.sort(key=lambda x:(x["submarket_title"],x["outcome"],x["side"]))

    # lifecycle counts
    lc=rawe["lifecycle"]
    lc_count={"MERGE":0,"SPLIT":0,"CONVERSION":0,"REDEEM":0}
    lc_usdc={"MERGE":0.0,"SPLIT":0.0,"CONVERSION":0.0,"REDEEM":0.0}
    for x in lc:
        lc_count[x["type"]]+=1; lc_usdc[x["type"]]+=x["usdcSize"]
        if x["timestamp"]:
            try: tsvals.append(int(x["timestamp"]))
            except: pass
    lo,hi=time_window(tsvals)

    # arithmetic block per pattern
    arith={}
    if r["pattern"]=="yes+no merge":
        arith={
            "identity":"YES+NO MERGE: buy both legs of a binary at p,q; if p+q<1 MERGE pair->$1. locked=(1-p-q)*merge_size",
            "measured":True,
            "per_conditionId_positive_legs":r["merge_pos_detail"],
            "merge_locked_total_measured":r["merge_locked_measured"],
            "total_merge_size":r["merge_size"],"total_merge_usdc_realized":r["merge_usdc"],
            "note":"MEASURED. Only conditionIds where BOTH binary legs were bought (p_sum<1) are credited; single-leg-buy merges excluded from locked (other leg cost not isolable in-window)."
        }
        est=r["merge_locked_measured"]
    elif r["pattern"]=="neg-risk NO conversion":
        arith={
            "identity":"NEG-RISK NO CONVERSION: buy 1 NO on each of N outcomes; CONVERSION of full set returns (N-1) USDC. locked_per_set=(N-1)-sum_i(vwap_No_i); locked=locked_per_set*sets",
            "N_outcomes":r["n_submarkets"],
            "sum_No_vwap":r["sum_no_vwap"],"legs_with_No":r["legs_with_no"],
            "No_vwaps_by_submarket":r["no_vwaps"],
            "locked_per_set_measured":r["conv_per_set"],
            "sets_used":r["conv_sets"],
            "clean_full_set":r["conv_clean"],
            "conv_locked_estimate":r["conv_locked"],
            "total_conversion_usdc":r["conv_usdc"],"conversion_count":r["conv_count"],
            "basis":r["conv_basis"],
            "measured": bool(r["conv_clean"]),
            "note":("MEASURED full-set: sum_No_vwap<(N-1), sets=conv_size validated by conv_size~=sum_No_size/N." if r["conv_clean"]
                    else "INFERRED: partial/uneven k-of-N conversion; per-set spread MEASURED from prices but set count is a proxy (=conv_size). Merge channel (merge_usdc=%.2f) also realizes some of this event."%r["merge_usdc"])
        }
        est=r["conv_locked"]
    elif r["pattern"]=="complete-set dutch book":
        # compute basket: best-bought leg vwap per cond
        arith={
            "identity":"COMPLETE-SET DUTCH BOOK: buy ~1 favored share per submarket; exactly one resolves $1, realized at REDEEM. locked=(1-sum favored vwaps)*set_size",
            "N_outcomes":r["n_submarkets"],
            "redeem_usdc_realized_measured":r["redeem_usdc"],"redeem_count":r["redeem_count"],
            "basket_buy_usdc":r["buy_usdc"],"basket_sell_usdc":r["sell_usdc"],
            "net_buy_cost":round(r["buy_usdc"]-r["sell_usdc"],2),
            "implied_locked_inferred":round(r["redeem_usdc"]-(r["buy_usdc"]-r["sell_usdc"]),2),
            "measured":False,
            "note":"REDEEM usdc is MEASURED realized. implied_locked = redeem - net_buy_cost is INFERRED (window may not capture full basket lifecycle / partial positions)."
        }
        est=r["redeem_usdc"]
    else:
        arith={"identity":"directional/none","measured":False,"note":"no clean arb lock channel"}
        est=None

    ep={
        "idx":idx,
        "eventSlug":es,
        "event_title_dominant":r["sample_title"],
        "category":r["category"],
        "arb_pattern":r["pattern"],
        "n_submarkets":r["n_submarkets"],
        "time_window":{"start":isots(lo),"end":isots(hi)},
        "event_channel_totals":{
            "trade_buy_usdc":r["buy_usdc"],"trade_sell_usdc":r["sell_usdc"],"trade_count":r["trade_count"],
            "merge_usdc":r["merge_usdc"],"merge_count":r["merge_count"],"merge_size":r["merge_size"],
            "conversion_usdc":r["conv_usdc"],"conversion_count":r["conv_count"],
            "split_usdc":r["split_usdc"],"split_count":r["split_count"],
            "redeem_usdc":r["redeem_usdc"],"redeem_count":r["redeem_count"],
        },
        "per_leg_breakdown":legs,
        "lifecycle_rows":{
            "summary_counts":lc_count,
            "summary_usdc":{k:round(v,2) for k,v in lc_usdc.items()},
            "rows":lc[:5000],  # cap to keep file sane; counts above are full
            "rows_truncated": len(lc)>5000,
            "total_lifecycle_rows": len(lc),
        },
        "computed_spread":arith,
        "est_locked_spread_usd":(round(est,2) if isinstance(est,(int,float)) else None),
        "data_basis":r["_lkbasis"],
    }
    path=os.path.join(EPDIR,f"ep_{idx}.json")
    json.dump(ep, open(path,'w',encoding='utf-8'), ensure_ascii=False, indent=1)

    manifest.append({
        "idx":idx,"event_slug":es,"title":r["sample_title"],"category":r["category"],
        "n_submarkets":r["n_submarkets"],"arb_pattern":r["pattern"],
        "buy_usdc":r["buy_usdc"],"merge_count":r["merge_count"],"conversion_count":r["conv_count"],
        "redeem_usdc":r["redeem_usdc"],
        "time_window":(isots(lo) or "")+" .. "+(isots(hi) or ""),
        "est_locked_spread_usd":(round(est,2) if isinstance(est,(int,float)) else 0.0),
        "detail_file":path})

json.dump(manifest, open(MAN,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print("\nWROTE",len(manifest),"detail files +",MAN, file=sys.stderr)
print(json.dumps(manifest, ensure_ascii=False, indent=1))
