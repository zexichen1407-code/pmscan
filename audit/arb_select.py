# -*- coding: utf-8 -*-
"""
arb_select.py
Recompute locked-spread with CORRECTED identities, then select a diverse top-15.

Identities (ACTIVITY ONLY):

MERGE channel (MEASURED):
  For each conditionId where MERGE occurred AND both binary legs were BOUGHT:
     spread_c = merge_size_c * (1 - vwap_buyA - vwap_buyB)
  event merge_locked = sum of positive per-cond spreads (we only credit conds
  where both legs visible & p_sum<1). This is the demonstrable locked profit
  from the YES+NO -> $1 merge.

CONVERSION channel (neg-risk NO conversion):
  N = number of submarkets (binary outcomes) in the event.
  For each outcome we observe a NO BUY vwap. Per full set:
     spread_per_set = (N-1) - sum_i(vwap_No_i)        [MEASURED from prices]
  sets:
     - "clean full-set": every outcome has a NO buy AND conv_size ~= sum_no/N
       (ratio in [0.8,1.25]) -> sets = conv_size  (MEASURED)
       locked = spread_per_set * sets
     - otherwise "partial/uneven k-of-N" -> spread_per_set still MEASURED but
       sets ambiguous; report locked as INFERRED using sets=conv_size as an
       upper-ish proxy and flag it.

DUTCH BOOK / REDEEM channel:
  N submarkets, buy ~1 share of the favored leg each; exactly one redeems $1.
  Per redeemed set: profit = 1 - sum_i(vwap_favleg_i)/... not cleanly a set.
  We report redeem_usdc (MEASURED realized) and the basket buy cost; locked
  inferred = redeem_usdc - (buy_usdc - sell_usdc) is NOT reliable -> we report
  redeem_usdc as realized and mark spread inferred.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
OUT = r"C:\Users\zexi\pmscan\audit\arb_event_scored.json"
with open(AGG,'r',encoding='utf-8') as f: agg=json.load(f)

def categorize(es, title):
    s=(es or "").lower(); t=(title or "").lower()
    if any(k in s for k in ["election","president","senate","governor","gubernatorial","mayoral","primary","nominee","parliament","by-election","provincial","province"]):
        return "election/politics"
    if any(k in s for k in ["fed-","fed_","fomc","cpi","inflation","gdp","jobs-report","recession","interest-rate","rate-"]) or "fed " in t or "interest rate" in t:
        return "macro/Fed"
    if any(k in s for k in ["which-company-has-best-ai","ai-model","best-ai-model","-ai-model","llm","gpt","openai","anthropic","gemini","grok","arena"]) or "ai model" in t:
        return "AI-model"
    if any(k in s for k in ["temperature","-temp-","highest-temp","weather","rain","snow"]) or "temperature" in t or "highest temp" in t:
        return "weather"
    if any(k in s for k in ["bitcoin","ethereum","-btc-","-eth-","crypto","largest-company","company-end-of","valuation","acquisition","ipo","market-cap","price-on","above-on"]) or "acquisition" in t or "largest" in t or "company" in t:
        return "company/crypto"
    if any(k in s for k in ["fifwc","nba","nfl","mlb","nhl","epl","ucl","uefa","champions-league","champion","stanley-cup","-vs-","wch","atp","cs2","ufc","exact-score","more-markets","playoffs","conference"]) or "exact score" in t or "vs." in t:
        return "sports"
    return "other"

scored=[]
for es,e in agg.items():
    conds=e["conds"]; N=len(conds)
    # --- merge channel ---
    merge_locked=0.0; merge_pos_detail=[]
    for cid,c in conds.items():
        if c["merge_size"]<=0: continue
        buys={}
        for lab,od in c["out"].items():
            b=od.get("BUY")
            if b and b["size"]>0: buys[lab]=b["usdc"]/b["size"]
        if len(buys)>=2:
            vs=sorted(buys.values()); psum=vs[0]+vs[1]
            sp=(1.0-psum)*c["merge_size"]
            if sp>0:
                merge_locked+=sp
                merge_pos_detail.append({"cid":cid,"title":c["title"],"merge_size":round(c["merge_size"],4),
                    "buy_vwaps":{k:round(v,5) for k,v in buys.items()},"p_sum":round(psum,5),"spread":round(sp,2)})
    # --- conversion channel ---
    no_vwaps={}; no_sizes=[]; sum_no_vwap=0.0; legs_with_no=0
    for cid,c in conds.items():
        nob=c["out"].get("No",{}).get("BUY")
        if nob and nob["size"]>0:
            v=nob["usdc"]/nob["size"]; no_vwaps[c["title"]]=round(v,5)
            sum_no_vwap+=v; legs_with_no+=1; no_sizes.append(nob["size"])
    conv_locked=None; conv_basis=None; conv_per_set=None; conv_sets=None; conv_clean=False
    if e["conv_size"]>0 and N>=2 and legs_with_no>=2:
        per_set=(N-1)-sum_no_vwap if legs_with_no==N else None
        if legs_with_no==N:
            sumno=sum(no_sizes); sn=sumno/N if N else 0
            ratio = e["conv_size"]/sn if sn>0 else 0
            conv_per_set=per_set
            if 0.8<=ratio<=1.25:
                conv_clean=True
                conv_sets=e["conv_size"]
                conv_locked=per_set*conv_sets
                conv_basis="MEASURED full-set: ((N-1)-sum_No_vwap)*sets, sets=conv_size (conv_size~sum_no/N, ratio=%.2f)"%ratio
            else:
                conv_sets=e["conv_size"]
                conv_locked=per_set*conv_sets
                conv_basis="INFERRED partial/uneven k-of-N: per_set MEASURED=((N-1)-sum_No_vwap)=%.4f; sets=conv_size proxy (cs/(sum_no/N)=%.2f)"%(per_set,ratio)
        else:
            conv_basis="INFERRED: NO buys missing on %d/%d outcomes; per-set not computable"%(N-legs_with_no,N)
    # --- redeem channel ---
    redeem_usdc=e["redeem_usdc"]

    # dominant pattern by realized channel usdc
    ch={"yes+no merge":e["merge_usdc"], "neg-risk NO conversion":e["conv_usdc"], "redeem":redeem_usdc}
    dom=max(ch,key=lambda k:ch[k]); domv=ch[dom]
    if domv<=0:
        pattern="trade-only / hold-to-redeem" if (e["trade_buy_usdc"]>0) else "other"
    elif dom=="redeem":
        pattern="complete-set dutch book" if N>=3 else "hold-to-redeem"
    else:
        pattern=dom

    sample_title=max(e["titles"],key=lambda k:e["titles"][k]) if e["titles"] else ""
    # best locked estimate for chosen pattern
    if pattern=="yes+no merge":
        locked=merge_locked; basis="MEASURED merge: sum_c merge_size*(1-vwapA-vwapB), positive conds only"
    elif pattern=="neg-risk NO conversion":
        locked=conv_locked; basis=conv_basis
    elif pattern=="complete-set dutch book":
        locked=None; basis="realized via REDEEM (MEASURED redeem_usdc=%.2f); per-set spread inferred"%redeem_usdc
    else:
        locked=None; basis="directional/none"

    scored.append({
        "eventSlug":es,"sample_title":sample_title,"category":categorize(es,sample_title),
        "n_submarkets":N,"pattern":pattern,
        "merge_usdc":round(e["merge_usdc"],2),"merge_count":e["merge_count"],"merge_size":round(e["merge_size"],2),
        "merge_locked_measured":round(merge_locked,2),
        "conv_usdc":round(e["conv_usdc"],2),"conv_count":e["conv_count"],"conv_size":round(e["conv_size"],2),
        "conv_per_set":(round(conv_per_set,5) if conv_per_set is not None else None),
        "conv_sets":(round(conv_sets,2) if conv_sets is not None else None),
        "conv_clean":conv_clean,"conv_locked":(round(conv_locked,2) if conv_locked is not None else None),
        "conv_basis":conv_basis,"sum_no_vwap":round(sum_no_vwap,5),"legs_with_no":legs_with_no,
        "no_vwaps":no_vwaps,
        "redeem_usdc":round(redeem_usdc,2),"redeem_count":e["redeem_count"],
        "split_usdc":round(e["split_usdc"],2),"split_count":e["split_count"],
        "buy_usdc":round(e["trade_buy_usdc"],2),"sell_usdc":round(e["trade_sell_usdc"],2),"trade_count":e["trade_count"],
        "locked_estimate":(round(locked,2) if isinstance(locked,(int,float)) else None),
        "locked_basis":basis,
        "min_ts":e["min_ts"],"max_ts":e["max_ts"],
        "merge_pos_detail":merge_pos_detail,
    })

with open(OUT,'w',encoding='utf-8') as o: json.dump(scored,o,ensure_ascii=False)

# global pattern + usdc breakdown (realized channel usdc)
from collections import defaultdict
pc=defaultdict(int); pu=defaultdict(float); plk=defaultdict(float)
for r in scored:
    pc[r["pattern"]]+=1
    if r["pattern"]=="yes+no merge":
        pu[r["pattern"]]+=r["merge_usdc"]; plk[r["pattern"]]+=r["merge_locked_measured"]
    elif r["pattern"]=="neg-risk NO conversion":
        pu[r["pattern"]]+=r["conv_usdc"]
        if r["conv_locked"]: plk[r["pattern"]]+=r["conv_locked"]
    else:
        pu[r["pattern"]]+=r["redeem_usdc"]
print("=== PATTERN BREAKDOWN ===")
for p in sorted(pc,key=lambda k:-pu[k]):
    print("%-32s events=%5d channel_usdc=%14,.2f est_locked=%12,.2f"%(p,pc[p],pu[p],plk[p]))
print("WROTE",OUT,file=sys.stderr)
