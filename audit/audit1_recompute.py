"""
INDEPENDENT AUDIT recompute of raw_activity_full.json.
Re-derives D3 (type breakdown), D4 (buy/sell), D5 (rewards),
D7 (reconciliation), D8 (time span) from scratch. Does NOT trust
type_breakdown.json / SUMMARY.json. Also probes duplicate rows under
several dedup keys to test the over/under-collapse claim.
"""
import json, os, sys, datetime
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = r"C:/Users/zexi/pmscan/audit/raw_activity_full.json"
DAY = 86400

print("loading (252MB)...", flush=True)
acts = json.load(open(P, encoding="utf-8"))
N = len(acts)
print(f"records loaded = {N}")

def f(x):
    try: return float(x)
    except: return 0.0

# ---------- D3 type breakdown ----------
typ_cnt = Counter(); typ_usdc = defaultdict(float)
for a in acts:
    t = a.get("type"); typ_cnt[t] += 1; typ_usdc[t] += f(a.get("usdcSize"))

# ---------- D4 buy/sell on TRADE ----------
buy_n=sell_n=0; buy_u=sell_u=0.0; other_side=0
sell_conds=set(); all_trade_conds=set()
for a in acts:
    if a.get("type")=="TRADE":
        cid=a.get("conditionId")
        all_trade_conds.add(cid)
        s=a.get("side")
        if s=="BUY": buy_n+=1; buy_u+=f(a.get("usdcSize"))
        elif s=="SELL":
            sell_n+=1; sell_u+=f(a.get("usdcSize")); sell_conds.add(cid)
        else: other_side+=1

# ---------- D5 rewards ----------
REWARD_TYPES={"REWARD","MAKER_REBATE","TAKER_REBATE","YIELD","REFERRAL_REWARD"}
rew_by_type=defaultdict(float); rew_cnt=Counter()
for a in acts:
    if a.get("type") in REWARD_TYPES:
        rew_by_type[a.get("type")]+=f(a.get("usdcSize")); rew_cnt[a.get("type")]+=1

# agent's reward set excluded REFERRAL_REWARD; compute both
agent_reward_set={"REWARD","MAKER_REBATE","TAKER_REBATE","YIELD"}
rew_total_agentset=sum(v for t,v in rew_by_type.items() if t in agent_reward_set)
rew_total_allreward=sum(rew_by_type.values())

# ---------- D7 reconciliation ----------
total_all=sum(f(a.get("usdcSize")) for a in acts)
struct_types={"TRADE","MERGE","CONVERSION","SPLIT","REDEEM"}
sum_struct=sum(f(a.get("usdcSize")) for a in acts if a.get("type") in struct_types)
sum_trade_only=typ_usdc["TRADE"]

# ---------- D8 time span ----------
tss=[int(a["timestamp"]) for a in acts if a.get("timestamp")]
tmin,tmax=min(tss),max(tss)

# ---------- duplicate analysis ----------
# (a) full-record identity duplicate: serialize sorted-keys json
# (b) agent dedup key
def agent_key(a):
    return (a.get("transactionHash"),a.get("asset"),a.get("type"),
            a.get("timestamp"),a.get("outcomeIndex"),a.get("side"),a.get("usdcSize"))
ak=Counter(agent_key(a) for a in acts)
dup_under_agentkey=sum(c-1 for c in ak.values() if c>1)
# stronger key incl conditionId+price+size to detect over-collapse risk
def strong_key(a):
    return (a.get("transactionHash"),a.get("asset"),a.get("type"),a.get("timestamp"),
            a.get("outcomeIndex"),a.get("side"),a.get("usdcSize"),
            a.get("conditionId"),a.get("price"),a.get("size"))
sk=Counter(strong_key(a) for a in acts)
dup_under_strongkey=sum(c-1 for c in sk.values() if c>1)
# rows that agent_key collapses but strong_key keeps distinct => potential over-collapse
# (these would already be gone from the file; measure residual collisions instead)
collide_agent_not_strong = len(sk)-len(ak)  # if strong has MORE distinct, agentkey over-merges

# field presence sanity
have_side_trade = sum(1 for a in acts if a.get("type")=="TRADE" and a.get("side"))
fields_sample = sorted(acts[0].keys())

print("\n================ D3 TYPE BREAKDOWN (independent) ================")
for t in sorted(typ_cnt,key=lambda x:-typ_usdc[x]):
    print(f"  {str(t):16} n={typ_cnt[t]:7}  usdc=${typ_usdc[t]:16,.2f}")
print(f"  TOTAL n={sum(typ_cnt.values())}  total_usdc=${total_all:,.2f}")

print("\n================ D4 BUY/SELL (independent) ================")
tu=buy_u+sell_u
print(f"  BUY  n={buy_n}  usdc=${buy_u:,.2f}  ({100*buy_u/tu:.2f}% usdc, {100*buy_n/(buy_n+sell_n):.2f}% count)")
print(f"  SELL n={sell_n}  usdc=${sell_u:,.2f}  ({100*sell_u/tu:.2f}% usdc)")
print(f"  side blank/other on TRADE = {other_side}")
print(f"  TRADE distinct conditionIds = {len(all_trade_conds)}")
print(f"  conditionIds with >=1 SELL = {len(sell_conds)}  ({100*len(sell_conds)/len(all_trade_conds):.2f}%)")

print("\n================ D5 REWARDS (independent) ================")
for t in sorted(rew_by_type,key=lambda x:-rew_by_type[x]):
    print(f"  {t:18} n={rew_cnt[t]:4} usdc=${rew_by_type[t]:,.2f}")
print(f"  agent reward set (no REFERRAL) total = ${rew_total_agentset:,.2f}")
print(f"  ALL reward types (incl REFERRAL)     = ${rew_total_allreward:,.2f}")
print(f"  reward / all-PnL(278513.59) = {100*rew_total_agentset/278513.59:.2f}% (agentset)  {100*rew_total_allreward/278513.59:.2f}% (all)")

print("\n================ D7 RECONCILIATION (independent) ================")
print(f"  sum ALL usdcSize                    = ${total_all:,.2f}")
print(f"  sum TRADE only                      = ${sum_trade_only:,.2f}")
print(f"  sum TRADE+MERGE+CONV+SPLIT+REDEEM   = ${sum_struct:,.2f}")
print(f"  lb-api volume all                   = $11,830,383.48")
print(f"  diff (struct - lbvol)               = ${sum_struct-11830383.48:,.2f}  ({100*(sum_struct-11830383.48)/11830383.48:.3f}%)")
print(f"  diff (TRADE only - lbvol)           = ${sum_trade_only-11830383.48:,.2f}")
print(f"  TRADE conditionIds                  = {len(all_trade_conds)}  (data-api traded=17081, diff={17081-len(all_trade_conds)})")

print("\n================ D8 TIME SPAN (independent) ================")
print(f"  min={tmin} {datetime.datetime.utcfromtimestamp(tmin)}  max={tmax} {datetime.datetime.utcfromtimestamp(tmax)}")
print(f"  span days = {(tmax-tmin)/DAY:.2f}")

print("\n================ DUP / DEDUP STRESS ================")
print(f"  rows                              = {N}")
print(f"  distinct under agent_key          = {len(ak)}  (residual dups under agent_key = {dup_under_agentkey})")
print(f"  distinct under strong_key(+cond/price/size) = {len(sk)}  (residual dups = {dup_under_strongkey})")
print(f"  strong_distinct - agent_distinct  = {collide_agent_not_strong}  (>0 => agent_key OVER-merges that many)")
print(f"  TRADE rows with side present      = {have_side_trade}")
print(f"  sample record fields              = {fields_sample}")

out={
 "records":N,"total_all_usdc":round(total_all,2),
 "type_breakdown":{t:{"count":typ_cnt[t],"usdc":round(typ_usdc[t],2)} for t in typ_cnt},
 "buy_sell":{"buy_n":buy_n,"buy_usdc":round(buy_u,2),"sell_n":sell_n,"sell_usdc":round(sell_u,2),
   "buy_usdc_pct":round(100*buy_u/tu,2),"buy_count_pct":round(100*buy_n/(buy_n+sell_n),2),
   "side_blank":other_side,"trade_conds":len(all_trade_conds),"sell_conds":len(sell_conds),
   "sell_cond_pct":round(100*len(sell_conds)/len(all_trade_conds),2)},
 "rewards":{t:{"count":rew_cnt[t],"usdc":round(rew_by_type[t],2)} for t in rew_by_type},
 "rewards_total_agentset":round(rew_total_agentset,2),"rewards_total_all":round(rew_total_allreward,2),
 "reconciliation":{"sum_all":round(total_all,2),"sum_trade":round(sum_trade_only,2),
   "sum_struct":round(sum_struct,2),"lb_vol":11830383.48,"trade_conds":len(all_trade_conds)},
 "time_span":{"min":tmin,"max":tmax,"days":round((tmax-tmin)/DAY,2)},
 "dedup":{"rows":N,"distinct_agent_key":len(ak),"residual_dup_agent":dup_under_agentkey,
   "distinct_strong_key":len(sk),"residual_dup_strong":dup_under_strongkey,
   "agent_overmerge":collide_agent_not_strong},
}
json.dump(out,open(r"C:/Users/zexi/pmscan/audit/audit1_recompute_out.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("\nwrote audit1_recompute_out.json")
