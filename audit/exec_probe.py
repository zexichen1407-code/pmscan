# -*- coding: utf-8 -*-
import sys, io
from collections import defaultdict, Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# 1) rebate evidence: maker vs taker
reb=Counter(); reb_usd=defaultdict(float)
# 2) same-timestamp multi-leg firing: per (event,ts) how many distinct NO-buy legs
ev_ts_legs=defaultdict(set)         # (es,ts)->set(cid)
ev_ts_fills=defaultdict(int)        # (es,ts)->count of NO-buy fills
no_buy_total=0
# 3) per NO-buy fill size distribution already known; here just confirm clip via count
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type')
        if t in ("MAKER_REBATE","TAKER_REBATE","REWARD","REFERRAL_REWARD"):
            reb[t]+=1
            try: reb_usd[t]+=float(r.get('usdcSize') or r.get('size') or 0)
            except: pass
        elif t=="TRADE" and r.get('side')=="BUY" and r.get('outcome')=="No":
            es=r.get('eventSlug') or r.get('slug') or ""
            try: ts=int(r.get('timestamp'))
            except: continue
            cid=r.get('conditionId') or ""
            ev_ts_legs[(es,ts)].add(cid)
            ev_ts_fills[(es,ts)]+=1
            no_buy_total+=1

print("=== 1) maker/taker 返佣证据 (谁更多 => 他主要是哪种角色) ===")
for k in ("MAKER_REBATE","TAKER_REBATE","REWARD","REFERRAL_REWARD"):
    print(f"  {k:16s}: {reb[k]:>6} 次  ${reb_usd[k]:>12,.2f}")
mk=reb["MAKER_REBATE"]+reb["REWARD"]; tk=reb["TAKER_REBATE"]
print(f"  → maker类(MAKER_REBATE+REWARD)={mk}  taker类(TAKER_REBATE)={tk}  "
      f"maker占 {100*mk/(mk+tk+1):.0f}%")
print(f"  → maker返佣总额 ${reb_usd['MAKER_REBATE']+reb_usd['REWARD']:,.0f}  taker返佣 ${reb_usd['TAKER_REBATE']:,.0f}")

print("\n=== 2) '同一秒把多条腿一起打出去' 的程度 ===")
multi=Counter()
for k,legs in ev_ts_legs.items():
    multi[len(legs)]+=1
total_ts=sum(multi.values())
print(f"  总(事件,秒)桶数: {total_ts:,}  总NO-buy fills: {no_buy_total:,}")
# 多少比例的'秒'里他同时碰了>=3条不同腿
ge2=sum(v for k,v in multi.items() if k>=2)
ge3=sum(v for k,v in multi.items() if k>=3)
ge5=sum(v for k,v in multi.items() if k>=5)
print(f"  同一秒内碰>=2条不同腿的秒: {100*ge2/total_ts:.0f}%   >=3条: {100*ge3/total_ts:.0f}%   >=5条: {100*ge5/total_ts:.0f}%")
print(f"  单秒同时碰的腿数分布(top): " + ", ".join(f"{k}腿×{v}" for k,v in sorted(multi.items(),key=lambda x:-x[1])[:8]))
mx=max(multi); 
print(f"  单秒最多同时碰 {mx} 条不同腿")
# 多少fills落在'同秒>=3腿'的脉冲里
fills_in_multi=sum(ev_ts_fills[k] for k,legs in ev_ts_legs.items() if len(legs)>=3)
print(f"  落在'同秒>=3腿齐发'脉冲里的 NO-buy fills 占: {100*fills_in_multi/no_buy_total:.0f}%")
