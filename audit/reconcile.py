import json,os,sys
from collections import defaultdict,Counter
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
acts=json.load(open('audit/raw_activity_full.json',encoding='utf-8'))
def f(x):
    try:return float(x)
    except:return 0.0

# 1) volume reconciliation: lb-api 'volume all'=11,830,383
# Polymarket 'volume' usually = traded notional. Our TRADE usdc=8.41M.
# Does lb volume include MERGE/CONVERSION/SPLIT/REDEEM? sum everything:
total_all=sum(f(a.get('usdcSize')) for a in acts)
trade=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE')
non_reward=sum(f(a.get('usdcSize')) for a in acts if a.get('type') in ('TRADE','MERGE','CONVERSION','SPLIT','REDEEM'))
print(f"sum ALL usdcSize         = {total_all:,.0f}")
print(f"sum TRADE only           = {trade:,.0f}")
print(f"sum TRADE+MERGE+CONV+SPLIT+REDEEM = {non_reward:,.0f}")
print(f"lb-api volume all        = 11,830,383")
print(f"  -> TRADE+arb-structural vs lb-vol diff: {11830383-non_reward:,.0f}")

# 2) 'other' category drill-down: top slugs that fell into 'other'
def categorize(sl):
    sl=(sl or "").lower()
    if "temperature" in sl or "warmest" in sl: return "weather/temperature"
    if "world-cup" in sl or "-vs-" in sl or "fifwc" in sl or "wcup" in sl: return "soccer/sports"
    if "fed-" in sl or "rate-decision" in sl or "fomc" in sl: return "fed/centralbank"
    if any(k in sl for k in ["election","primary","governor","senate","president","prime-minister","mayor","nominee","presidential"]): return "election/politics"
    if any(k in sl for k in ["gdp","cpi","inflation","jobs","unemployment","recession"]): return "macro/econ"
    if any(k in sl for k in ["chatbot","llm","ai-","-ai-","openai","gpt","gemini","grok","claude","model"]): return "ai/tech"
    if any(k in sl for k in ["bitcoin","ethereum","btc","eth","crypto","solana"]): return "crypto"
    return "other"
other_ev=defaultdict(float)
for a in acts:
    if a.get('type')=='TRADE' and categorize(a.get('eventSlug') or a.get('slug'))=='other':
        other_ev[a.get('eventSlug') or a.get('slug') or '?']+=f(a.get('usdcSize'))
print("\nTop 25 'other'-category events by TRADE usdc:")
for s,v in sorted(other_ev.items(),key=lambda x:-x[1])[:25]:
    print(f"  ${v:11,.0f}  {s[:60]}")

# 3) verify BUY/SELL another way: count distinct conditionId with any SELL
sell_conds=set(a.get('conditionId') for a in acts if a.get('type')=='TRADE' and a.get('side')=='SELL')
all_conds=set(a.get('conditionId') for a in acts if a.get('type')=='TRADE')
print(f"\nTRADE conditionIds total={len(all_conds)}  with >=1 SELL={len(sell_conds)}")

# 4) reward breakdown reconcile: lb-api profit doesn't include rewards? note reward total tiny
