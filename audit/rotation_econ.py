# -*- coding: utf-8 -*-
"""把周转速度翻译成经济结论:
 - 活跃窗口长度(首笔->末笔 NO buy)
 - 资金加权 平均/中位 持有期 (含残腿)
 - 隐含周转次数, 与 doc 的 ~168 turns 对账
 - 同时在跑的并行事件数(看资金是怎么铺开的)
"""
import sys, io
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

ev=defaultdict(lambda:{"legs":defaultdict(list),"conv":[],"no_buy_usdc":0.0})
allbuy_ts=[]; mints=10**12; maxts=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        try: ts=int(r.get('timestamp'))
        except: continue
        if ts>maxts: maxts=ts
        if ts<mints: mints=ts
        if t=="TRADE" and r.get('side')=="BUY" and r.get('outcome')=="No":
            cid=r.get('conditionId') or ""
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            try: pr=float(r.get('price') or 0)
            except: pr=0.0
            ev[es]["legs"][cid].append((ts,sz,us,pr)); ev[es]["no_buy_usdc"]+=us
            allbuy_ts.append(ts)
        elif t=="CONVERSION":
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            ev[es]["conv"].append((ts,sz))
DATA_END=maxts
negrisk=[(es,e,sum(1 for c,b in e["legs"].items() if b)) for es,e in ev.items()
         if e["conv"] and sum(1 for c,b in e["legs"].items() if b)>=3]
tot=sum(e["no_buy_usdc"] for _,e,_ in negrisk)

# active window of NO buying
allbuy_ts.sort()
win_days=(allbuy_ts[-1]-allbuy_ts[0])/86400
print(f"NO-buy 活跃窗口: {win_days:.1f} 天 ({allbuy_ts[0]} -> {allbuy_ts[-1]})")
print(f"neg-risk 总 NO-buy 量: ${tot:,.0f}")

# capital-weighted holding incl residual
hold=[]; w=[]
for es,e,nlegs in negrisk:
    convs=sorted(e["conv"])
    for cid,b in e["legs"].items():
        if not b: continue
        q=deque(sorted(b))
        for (cts,s) in convs:
            need=s
            while need>1e-9 and q and q[0][0]<=cts:
                ts,sh,us,pr=q[0]; take=min(need,sh)
                hold.append(cts-ts); w.append(take*pr); need-=take
                if take>=sh-1e-9: q.popleft()
                else: q[0]=(ts,sh-take,us,pr)
        while q:
            ts,sh,us,pr=q.popleft()
            hold.append(DATA_END-ts); w.append(sh*pr)   # residual censored at data end
sw=sum(w)
mean_hold=sum(h*wt for h,wt in zip(hold,w))/sw
pairs=sorted(zip(hold,w))
def wp(p):
    target=p*sw; cum=0
    for v,wt in pairs:
        cum+=wt
        if cum>=target: return v
    return pairs[-1][0]
def f(x): return f"{x:.0f}s" if x<90 else (f"{x/60:.1f}m" if x<5400 else (f"{x/3600:.1f}h" if x<172800 else f"{x/86400:.1f}d"))
print(f"\n资金加权 持有期(含残腿, 残腿删失到数据末尾):")
print(f"  中位={f(wp(.5))}  均值={f(mean_hold)}  p90={f(wp(.9))}  p99={f(wp(.99))}")

# 隐含周转: 用 Little's law 视角. 平均在仓资金 ~ 总量 * (avg_hold / window)
avg_inv = tot * (mean_hold/ (allbuy_ts[-1]-allbuy_ts[0]))
print(f"\nLittle's law: 平均在仓NO资金 ≈ 总量×(均持有/窗口) = ${avg_inv:,.0f}")
print(f"  若本金~$50k: 该估计={'<' if avg_inv<50000 else '>'} 本金, 隐含可用本金周转 ~{tot/max(avg_inv,1):.0f} 次/窗口")
print(f"  (doc 自报 ~168 turns over $8.4M/$50k; 这里只算 neg-risk 部分 ${tot:,.0f})")

# 并行度: 每天有多少不同事件在被交易
day_events=defaultdict(set)
for es,e,nlegs in negrisk:
    for cid,b in e["legs"].items():
        for (ts,sz,us,pr) in b:
            day_events[ts//86400].add(es)
import statistics
counts=[len(s) for s in day_events.values()]
print(f"\n并行度: 每天活跃 neg-risk 事件数 中位={statistics.median(counts):.0f} 最大={max(counts)} "
      f"(资金同时铺在多个事件上, 不是单事件串行)")
