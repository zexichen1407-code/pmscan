# -*- coding: utf-8 -*-
import sys, io
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW=r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
SLUG="highest-temperature-in-london-on-may-23-2026"

fills=[]; legset=set()
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        if r.get('type')!="TRADE" or r.get('side')!="BUY" or r.get('outcome')!="No": continue
        if (r.get('eventSlug') or "")!=SLUG: continue
        try: ts=int(r.get('timestamp')); pr=float(r.get('price') or 0); sz=float(r.get('size') or 0)
        except: continue
        cond=(r.get('conditionId') or "")[:10]
        fills.append((ts,cond,pr,sz)); legset.add(cond)
N=len(legset)
fills.sort()
print(f"事件 {SLUG}")
print(f"他买过 NO 的不同腿(桶) N = {N};  N-1(满套价值) = {N-1};  共 {len(fills)} 笔\n")

# 按分钟分组, 看每次"扫"覆盖多少腿 + 这批的 Σ(NO价) vs (腿数-1) 和 vs (N-1)
bymin=defaultdict(list)
for ts,cond,pr,sz in fills: bymin[ts//60].append((cond,pr,sz))
print("每分钟扫单: 覆盖腿数 / Σ(NO均价) / 与(腿数-1)比 / 该批是否当下可套利")
mins=sorted(bymin)
sweeps=[]
for m in mins:
    rows=bymin[m]
    # 每腿取均价
    legpx=defaultdict(list)
    for cond,pr,sz in rows: legpx[cond].append(pr)
    legs=len(legpx)
    sigma=sum(sum(v)/len(v) for v in legpx.values())
    if legs>=5:
        sweeps.append((m,legs,sigma))
        edge_local = (legs-1)-sigma     # 若只看这批腿, 集齐这几腿转换返还(legs-1)
        print(f"  分钟{m}: {legs:>2}腿  Σ(NO)={sigma:.3f}  (腿数-1)={legs-1}  局部毛边际={edge_local*100:+.1f}¢  {'套利' if edge_local>0 else '亏'}")

# 全事件: 他每条腿的 VWAP, Σ over all N legs vs N-1 (这才是完整套利判定)
legvw=defaultdict(lambda:[0.0,0.0])  # cond -> [usd, shares]
for ts,cond,pr,sz in fills:
    legvw[cond][0]+=pr*sz; legvw[cond][1]+=sz
sigma_all=sum(u/s for u,s in legvw.values() if s>0)
print(f"\n全事件 Σ(各腿NO的VWAP) = {sigma_all:.3f}  vs  N-1 = {N-1}")
print(f"  完整篮子毛边际/套 = (N-1)-Σ = {((N-1)-sigma_all)*100:+.1f}¢  {'→ 整体套利(Σ<N-1)' if sigma_all<N-1 else '→ 整体Σ>=N-1(单扫亏, 要靠逢低补/返佣)'}")

# 第一次扫 vs 后续: 价格是否走低(逢低补腿=他文档说的摊薄)
if len(sweeps)>=2:
    first=sweeps[0]; 
    print(f"\n首扫: 分钟{first[0]} {first[1]}腿 Σ={first[2]:.3f}")
    print(f"末扫: 分钟{sweeps[-1][0]} {sweeps[-1][1]}腿 Σ={sweeps[-1][2]:.3f}")
