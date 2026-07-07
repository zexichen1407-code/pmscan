import json,os,sys
from collections import defaultdict,Counter
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
acts=json.load(open('audit/raw_activity_full.json',encoding='utf-8'))
def f(x):
    try:return float(x)
    except:return 0.0
def cat(sl):
    s=(sl or "").lower()
    if "temperature" in s or "warmest" in s or "highest-temp" in s: return "weather/temperature"
    if any(k in s for k in ["fifwc","world-cup","-vs-","nba","french-open","stanley-cup","nhl","f1-","iihf","champions-league","champion","playoff","msi","iem-","major-2026","big-game","drivers-champion","constructors","uefa","eurovision","grossing-movie","album-sales","mrbeast","views-of"]): return "sports/entertainment/winner-markets"
    if any(k in s for k in ["fed-","rate-decision","fomc","ecb-interest","interest-rate"]): return "fed/centralbank"
    if any(k in s for k in ["election","primary","governor","senate","presidential","president","prime-minister","mayor","nominee","leader-out-of-power","next-uk","next-leader"]): return "election/politics"
    if any(k in s for k in ["gdp","cpi","inflation","jobs","unemployment","recession","ipo","market-cap","largest-company","2nd-largest"]): return "macro/markets/company"
    if any(k in s for k in ["ai-model","chatbot","llm","openai","gpt","gemini","grok","claude","best-ai"]): return "ai/tech"
    if any(k in s for k in ["bitcoin","ethereum","btc","eth","crypto","solana","spacex"]): return "crypto/tech-co"
    if any(k in s for k in ["iran","putin","trump","diplomatic","meet-next"]): return "geopolitics"
    return "other"
catu=defaultdict(float); catc=Counter()
for a in acts:
    if a.get('type')=='TRADE':
        c=cat(a.get('eventSlug') or a.get('slug')); u=f(a.get('usdcSize'))
        catu[c]+=u; catc[c]+=1
tot=sum(catu.values())
print("Refined TRADE category distribution:")
for k,v in sorted(catu.items(),key=lambda x:-x[1]):
    print(f"  {k:38} ${v:12,.0f} ({100*v/tot:5.1f}%)  n={catc[k]}")

# patch into type_breakdown.json
tb=json.load(open('audit/type_breakdown.json',encoding='utf-8'))
tb['category_trade_usdc_refined']={k:round(v,2) for k,v in catu.items()}
tb['category_trade_count_refined']={k:catc[k] for k in catc}
tb['reconciliation']={
 "sum_all_usdcSize":round(sum(f(a.get('usdcSize')) for a in acts),2),
 "sum_trade_plus_structural":round(sum(f(a.get('usdcSize')) for a in acts if a.get('type') in ('TRADE','MERGE','CONVERSION','SPLIT','REDEEM')),2),
 "lb_api_volume_all":11830383.48,
 "trade_conditionIds":len(set(a.get('conditionId') for a in acts if a.get('type')=='TRADE')),
 "trade_conditionIds_with_sell":len(set(a.get('conditionId') for a in acts if a.get('type')=='TRADE' and a.get('side')=='SELL')),
 "data_api_traded_count":17081,
}
json.dump(tb,open('audit/type_breakdown.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print("\nupdated type_breakdown.json with refined categories + reconciliation")
