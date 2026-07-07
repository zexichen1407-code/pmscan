# -*- coding: utf-8 -*-
import sys, io, pickle, statistics
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
PKL=r"C:\Users\zexi\pmscan\audit\negrisk_trades.pkl"
d=pickle.load(open(PKL,'rb'))

# ---- 3) TAKER vs MAKER: within a burst-second, per-leg price dispersion ----
# Most legs appear once per second. To test maker/taker we look per leg across the
# whole event: does the leg get filled at a constant price (resting maker repeatedly
# hit) or does price walk (taker sweeping the book)?
print("="*90)
print("【3】TAKER vs MAKER  每条腿 价格离散度 (跨该事件该腿全部成交)")
print(f"{'事件':<42}{'腿数':>5}{'多笔腿':>7}{'恒定<=0.5c':>11}{'walk>2c':>9}")
for slug,rows in d.items():
    fills=[r for r in rows if r.get('side')=='BUY' and r.get('outcome')=='No']
    perleg=defaultdict(list)
    for r in fills: perleg[r['cid']].append(float(r['price']))
    nleg=len(perleg); multi=0; const=0; walk=0
    for cid,prs in perleg.items():
        if len(prs)<2: continue
        multi+=1
        spread=max(prs)-min(prs)
        if spread<=0.005: const+=1
        if spread>0.02: walk+=1
    print(f"{slug[:42]:<42}{nleg:>5}{multi:>7}{const:>11}{walk:>9}")

# Within a single hot second across the whole dataset: are the multiple fills on the
# SAME leg in the SAME second at constant or walking price?
print()
print("同秒同腿多笔的价格行走 (taker扫单证据):")
sameleg_sec_spreads=[]
for slug,rows in d.items():
    fills=[r for r in rows if r.get('side')=='BUY' and r.get('outcome')=='No']
    bucket=defaultdict(list)  # (sec,leg)->prices
    for r in fills:
        bucket[(int(r['ts']),r['cid'])].append(float(r['price']))
    for k,prs in bucket.items():
        if len(prs)>=2:
            sameleg_sec_spreads.append(max(prs)-min(prs))
if sameleg_sec_spreads:
    s=sameleg_sec_spreads
    print(f"  同秒同腿>=2笔的桶: {len(s)}  价差 mean={statistics.mean(s)*100:.2f}c median={statistics.median(s)*100:.2f}c max={max(s)*100:.2f}c")
    walked=sum(1 for x in s if x>0.005)
    print(f"  其中价差>0.5c(说明同秒内还在走价=taker扫): {walked}/{len(s)} ({100*walked/len(s):.0f}%)")

# ---- 4) ARB CONDITION: Sum over legs of NO VWAP  vs  (N-1) ----
print()
print("="*90)
print("【4】ARB 条件  Sum(NO_VWAP) vs (N-1)   <(N-1)=锁定无风险套利")
print(f"{'事件':<42}{'N':>4}{'Sum(NO_vwap)':>14}{'N-1':>7}{'裕度(N-1-Sum)':>14}{'锁?':>5}")
arb_examples=[]
for slug,rows in d.items():
    fills=[r for r in rows if r.get('side')=='BUY' and r.get('outcome')=='No']
    leg=defaultdict(lambda:[0.0,0.0])  # cid -> [usd, sh]
    for r in fills:
        leg[r['cid']][0]+=float(r['usdc']); leg[r['cid']][1]+=float(r['size'])
    vwaps=[u/s for u,s in leg.values() if s>0]
    N=len(vwaps); S=sum(vwaps)
    locked = S < (N-1)
    print(f"{slug[:42]:<42}{N:>4}{S:>14.4f}{N-1:>7}{(N-1)-S:>14.4f}{'  YES' if locked else '   no':>5}")
    arb_examples.append((slug,N,S,(N-1)-S,locked))

print()
print("锁定例子(裕度=锁住的每套无风险利润, 单位 USDC/套):")
for slug,N,S,margin,locked in sorted(arb_examples,key=lambda x:-x[3])[:7]:
    if locked:
        print(f"  {slug[:46]:<46} N={N} Sum(NO)={S:.4f} < N-1={N-1} → 每套锁 {margin:.4f}$ ({100*margin/(N-1) if N>1 else 0:.2f}% of stake)")
