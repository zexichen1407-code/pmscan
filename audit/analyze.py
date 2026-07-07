"""
Analyzer: runs on raw_activity_full.json (full-history) if present, else raw_activity.json.
Computes every breakdown the report needs and dumps machine-readable JSON.
All math re-derived here independently (not trusting p3nny_calc.py).
"""
import json, os, sys, datetime
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT=os.path.dirname(os.path.abspath(__file__))
DAY=86400

def load(n):
    p=os.path.join(OUT,n)
    return json.load(open(p,encoding="utf-8")) if os.path.exists(p) else None

full=load("raw_activity_full.json")
sample=load("raw_activity.json")
acts = full if full else sample
src  = "raw_activity_full.json" if full else "raw_activity.json"
print(f"== analyzing {src}: {len(acts)} records ==")

def f(x):
    try: return float(x)
    except: return 0.0

# ---- type breakdown ----
typ_cnt=Counter(); typ_usdc=defaultdict(float)
for a in acts:
    t=a.get("type"); typ_cnt[t]+=1; typ_usdc[t]+=f(a.get("usdcSize"))

# ---- TRADE buy/sell ----
buy_n=sell_n=0; buy_u=sell_u=0.0; other_side=0
for a in acts:
    if a.get("type")=="TRADE":
        s=a.get("side")
        if s=="BUY": buy_n+=1; buy_u+=f(a.get("usdcSize"))
        elif s=="SELL": sell_n+=1; sell_u+=f(a.get("usdcSize"))
        else: other_side+=1

# ---- time span ----
tss=[int(a["timestamp"]) for a in acts if a.get("timestamp")]
tmin,tmax=(min(tss),max(tss)) if tss else (None,None)

# ---- rewards ----
REWARD_TYPES={"REWARD","MAKER_REBATE","TAKER_REBATE","YIELD"}
rew_by_type=defaultdict(float); rew_cnt=Counter()
for a in acts:
    if a.get("type") in REWARD_TYPES:
        rew_by_type[a.get("type")]+=f(a.get("usdcSize")); rew_cnt[a.get("type")]+=1
rew_total=sum(rew_by_type.values())
rew_7d=rew_30d=0.0
if tmax:
    for a in acts:
        if a.get("type") in REWARD_TYPES:
            ts=int(a["timestamp"]); u=f(a.get("usdcSize"))
            if ts>=tmax-7*DAY: rew_7d+=u
            if ts>=tmax-30*DAY: rew_30d+=u

# ---- market / category breakdown on TRADE usdc ----
ev_usdc=defaultdict(float); ev_cnt=Counter()
cat_usdc=defaultdict(float)
def categorize(sl):
    sl=(sl or "").lower()
    if "temperature" in sl or "highest-temperature" in sl or "warmest" in sl: return "weather/temperature"
    if "world-cup" in sl or "-vs-" in sl or "wcup" in sl: return "soccer/sports"
    if "fed-" in sl or "rate-decision" in sl or "interest-rate" in sl or "fomc" in sl: return "fed/centralbank"
    if any(k in sl for k in ["election","primary","governor","senate","president","prime-minister","mayor","nominee"]): return "election/politics"
    if any(k in sl for k in ["gdp","cpi","inflation","jobs","unemployment","recession"]): return "macro/econ"
    if any(k in sl for k in ["chatbot","llm","ai-","-ai-","openai","gpt","gemini","grok","claude","model"]): return "ai/tech"
    if any(k in sl for k in ["bitcoin","ethereum","btc","eth","crypto","solana"]): return "crypto"
    return "other"
for a in acts:
    if a.get("type")=="TRADE":
        sl=a.get("eventSlug") or a.get("slug") or "?"
        u=f(a.get("usdcSize"))
        ev_usdc[sl]+=u; ev_cnt[sl]+=1
        cat_usdc[categorize(a.get("eventSlug") or a.get("slug"))]+=u

# ---- MERGE / CONVERSION / SPLIT evidence ----
arb_evidence={}
for t in ["MERGE","CONVERSION","SPLIT","REDEEM"]:
    rows=[a for a in acts if a.get("type")==t]
    arb_evidence[t]={"count":len(rows),"usdc":round(sum(f(a.get('usdcSize')) for a in rows),2)}

# ---- identity ----
ident=None
for a in acts:
    if a.get("pseudonym") or a.get("name"):
        ident={"pseudonym":a.get("pseudonym"),"name":a.get("name"),
               "profileImage":a.get("profileImage"),"bio":a.get("bio")}
        break

# ---- print ----
print("\n-- TYPE breakdown (count / usdcSize) --")
for t in sorted(typ_cnt,key=lambda x:-typ_usdc[x]):
    print(f"  {str(t):14} {typ_cnt[t]:6}  ${typ_usdc[t]:16,.2f}")
print(f"\n-- TRADE side --")
tot_u=buy_u+sell_u
print(f"  BUY  n={buy_n:6} ${buy_u:,.2f}  ({100*buy_u/tot_u:.1f}% of trade usdc)" if tot_u else "  no trades")
print(f"  SELL n={sell_n:6} ${sell_u:,.2f}  ({100*sell_u/tot_u:.1f}%)" if tot_u else "")
print(f"  BUY count share: {100*buy_n/(buy_n+sell_n):.1f}%" if (buy_n+sell_n) else "")
print(f"  side blank/other: {other_side}")
print(f"\n-- time span --")
if tmin:
    print(f"  {tmin}..{tmax}  {datetime.datetime.utcfromtimestamp(tmin)} .. {datetime.datetime.utcfromtimestamp(tmax)}  ({(tmax-tmin)/DAY:.1f}d)")
print(f"\n-- rewards --")
for t in rew_by_type: print(f"  {t:14} n={rew_cnt[t]:4} ${rew_by_type[t]:,.2f}")
print(f"  reward TOTAL (in pull): ${rew_total:,.2f}   7d ${rew_7d:,.2f}  30d ${rew_30d:,.2f}")
print(f"\n-- arb evidence (MERGE/CONVERSION/SPLIT/REDEEM) --")
for t,v in arb_evidence.items(): print(f"  {t:12} count={v['count']:5} usdc=${v['usdc']:,.2f}")
print(f"\n-- category (TRADE usdc) --")
tcat=sum(cat_usdc.values())
for k,v in sorted(cat_usdc.items(),key=lambda x:-x[1]):
    print(f"  {k:22} ${v:14,.0f} ({100*v/tcat:.1f}%)")
print(f"\n-- top 15 events by TRADE usdc --")
for s,v in sorted(ev_usdc.items(),key=lambda x:-x[1])[:15]:
    print(f"  ${v:12,.0f} n={ev_cnt[s]:4}  {s[:55]}")
print(f"\n-- identity --", ident)

# dump machine-readable
out={
 "source_file":src,"records":len(acts),
 "type_breakdown":{t:{"count":typ_cnt[t],"usdc":round(typ_usdc[t],2)} for t in typ_cnt},
 "trade_side":{"buy_n":buy_n,"buy_usdc":round(buy_u,2),"sell_n":sell_n,"sell_usdc":round(sell_u,2),
               "buy_usdc_pct":round(100*buy_u/tot_u,2) if tot_u else None,
               "buy_count_pct":round(100*buy_n/(buy_n+sell_n),2) if (buy_n+sell_n) else None,
               "side_blank":other_side},
 "time_span":{"min":tmin,"max":tmax,"min_iso":str(datetime.datetime.utcfromtimestamp(tmin)) if tmin else None,
              "max_iso":str(datetime.datetime.utcfromtimestamp(tmax)) if tmax else None,
              "days":round((tmax-tmin)/DAY,2) if tmin else None},
 "rewards":{"by_type":{t:{"count":rew_cnt[t],"usdc":round(rew_by_type[t],2)} for t in rew_by_type},
            "total":round(rew_total,2),"last7d":round(rew_7d,2),"last30d":round(rew_30d,2)},
 "arb_evidence":arb_evidence,
 "category_trade_usdc":{k:round(v,2) for k,v in cat_usdc.items()},
 "top_events":[{"slug":s,"usdc":round(v,2),"count":ev_cnt[s]} for s,v in sorted(ev_usdc.items(),key=lambda x:-x[1])[:30]],
 "identity":ident,
}
json.dump(out,open(os.path.join(OUT,"type_breakdown.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("\ndumped type_breakdown.json")
