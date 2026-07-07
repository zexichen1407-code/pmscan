# -*- coding: utf-8 -*-
import sys, io, json
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW=r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# 收集 温度类 neg-risk 事件 的 BUY-No 成交
ev=defaultdict(list)  # slug -> list of (ts, cond, price, size, txhash)
n=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        if r.get('type')!="TRADE": continue
        if r.get('side')!="BUY" or r.get('outcome')!="No": continue
        es=r.get('eventSlug') or ""
        if "temperature" not in es.lower(): continue
        try: ts=int(r.get('timestamp'))
        except: continue
        try: pr=float(r.get('price') or 0)
        except: pr=0.0
        try: sz=float(r.get('size') or 0)
        except: sz=0.0
        ev[es].append((ts, (r.get('conditionId') or "")[:10], pr, sz, (r.get('transactionHash') or "")[:10], str(r.get('title') or "")[:1]))
# 选成交最多的温度事件
best=max(ev.items(), key=lambda kv: len(kv[1]))
slug, fills = best
fills.sort()
print(f"温度事件: {slug}  共 {len(fills)} 笔 BUY-No 成交\n")

# 1) 每分钟 触及的不同腿数
bymin=defaultdict(lambda: defaultdict(int))   # minute -> cond -> count
for ts,cond,pr,sz,tx,_ in fills:
    bymin[ts//60][cond]+=1
multi_min=[(m,len(legs),sum(legs.values())) for m,legs in bymin.items()]
multi_min.sort(key=lambda x:-x[1])
print("=== 每分钟 触及不同腿数 top (=他多腿同分钟成交的程度) ===")
print(f"  最多的分钟: 同一分钟摸了 {multi_min[0][1]} 条不同腿 / {multi_min[0][2]} 笔")
ge3=sum(1 for m,L,f in multi_min if L>=3)
print(f"  有 {ge3} 个分钟 同时摸 >=3 条不同腿; 共 {len(bymin)} 个有成交的分钟")

# 2) 看那个最猛的分钟 的逐笔(秒级): 是不是同秒齐发? 价格是否walking(taker) or 定价(maker)?
hot_min = multi_min[0][0]
print(f"\n=== 最猛那一分钟 (ts//60={hot_min}) 逐笔 ===")
sub=[f for f in fills if f[0]//60==hot_min]
sub.sort()
bysec=defaultdict(set); bytx=defaultdict(set)
perleg_prices=defaultdict(list)
for ts,cond,pr,sz,tx,_ in sub:
    bysec[ts].add(cond); bytx[tx].add(cond)
    perleg_prices[cond].append(pr)
for ts,cond,pr,sz,tx,_ in sub[:30]:
    print(f"  ts={ts} sec={ts%60:>2} leg={cond} px={pr:.3f} sz={sz:>7.1f} tx={tx}")
print(f"  ... 该分钟共 {len(sub)} 笔")
print(f"\n  同一秒摸的不同腿: " + ", ".join(f"sec内{len(c)}腿×{sum(1 for s,cs in bysec.items() if len(cs)==len(c))}" for c in [max(bysec.values(),key=len)][:1]))
maxsec=max(bysec.items(), key=lambda kv:len(kv[1]))
print(f"  单秒最多同时摸 {len(maxsec[1])} 条不同腿 (ts={maxsec[0]})")
# txhash: 多腿是否共享一个tx(原子multicall) 还是各自独立tx(并发下单)
multi_leg_tx=sum(1 for tx,cs in bytx.items() if len(cs)>1)
print(f"  跨腿共享同一txhash的: {multi_leg_tx}/{len(bytx)} 个tx (>0=原子多腿; =0=每腿独立tx并发)")

# 3) maker vs taker 线索: 每条腿在该分钟内 价格是否恒定(挂单被吃=maker) or 变化(扫单walking=taker)
print(f"\n=== maker/taker 线索: 各腿该分钟内 价格离散度 ===")
import statistics
nleg=0; const_legs=0
for cond,prs in perleg_prices.items():
    if len(prs)<2: continue
    nleg+=1
    spread=max(prs)-min(prs)
    if spread<=0.005: const_legs+=1
print(f"  有多笔的腿 {nleg} 条; 其中价格几乎恒定(<=0.5分波动)的 {const_legs} 条")
print(f"  恒定占比 {100*const_legs/max(1,nleg):.0f}% —— 高=同一挂价被反复吃(偏maker); 低=walking the book(偏taker)")
