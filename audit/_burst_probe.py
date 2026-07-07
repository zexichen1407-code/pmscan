# -*- coding: utf-8 -*-
import sys, io, pickle, statistics
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
PKL=r"C:\Users\zexi\pmscan\audit\negrisk_trades.pkl"
d=pickle.load(open(PKL,'rb'))

print("事件清单:", {k:len(v) for k,v in d.items()})
print()

# Global accumulators
GLOB_same_sec_legcount=[]      # distribution of distinct legs per (event,second) where multi-leg
GLOB_sec_buckets_total=0
GLOB_sec_ge2=0; GLOB_sec_ge3=0
GLOB_sec_max=0
# atomic vs concurrent: for each second-bucket with >=2 legs, do those legs share a single tx?
ATOMIC_secs=0; CONCURRENT_secs=0
# tx fan-out: does any single tx carry >1 distinct leg?
tx_multileg=0; tx_total=0

for slug,rows in d.items():
    # keep only BUY No
    fills=[r for r in rows if r.get('side')=='BUY' and r.get('outcome')=='No']
    if not fills: continue
    bysec=defaultdict(lambda: defaultdict(list))  # sec -> leg -> [(price,size,tx)]
    bytx=defaultdict(set)
    for r in fills:
        sec=int(r['ts']); leg=r['cid'][:10]
        bysec[sec][leg].append((float(r['price']),float(r['size']),r['tx']))
        bytx[r['tx']].add(leg)
    for tx,legs in bytx.items():
        tx_total+=1
        if len(legs)>1: tx_multileg+=1
    for sec,legs in bysec.items():
        nleg=len(legs)
        GLOB_sec_buckets_total+=1
        if nleg>=2:
            GLOB_sec_ge2+=1
            GLOB_same_sec_legcount.append(nleg)
            # atomic? collect all tx across legs this second
            txset=set()
            for leg,items in legs.items():
                for px,sz,tx in items: txset.add(tx)
            if len(txset)==1:
                ATOMIC_secs+=1
            else:
                CONCURRENT_secs+=1
        if nleg>=3: GLOB_sec_ge3+=1
        GLOB_sec_max=max(GLOB_sec_max,nleg)

print("="*90)
print("【1+2】SAME-SECOND FAN-OUT  &  ATOMIC vs CONCURRENT  (聚合全部事件)")
print(f"  有成交的 (event,second) 桶总数: {GLOB_sec_buckets_total}")
print(f"  同一秒摸 >=2 条不同腿的桶: {GLOB_sec_ge2}")
print(f"  同一秒摸 >=3 条不同腿的桶: {GLOB_sec_ge3}")
print(f"  单秒最多同时摸不同腿: {GLOB_sec_max}")
if GLOB_same_sec_legcount:
    c=GLOB_same_sec_legcount
    print(f"  多腿秒桶的腿数分布: n={len(c)} mean={statistics.mean(c):.2f} median={statistics.median(c)} max={max(c)}")
    dist=defaultdict(int)
    for x in c: dist[x]+=1
    print("  腿数->桶数: "+", ".join(f"{k}腿:{dist[k]}" for k in sorted(dist)))
print()
print(f"  ★ 原子(多腿同秒共享1个tx): {ATOMIC_secs} 桶")
print(f"  ★ 并发(多腿同秒各自独立tx): {CONCURRENT_secs} 桶")
tot=ATOMIC_secs+CONCURRENT_secs
print(f"  ★ 比例: 并发独立tx占 {100*CONCURRENT_secs/max(1,tot):.1f}% ; 原子占 {100*ATOMIC_secs/max(1,tot):.1f}%")
print(f"  ★ 跨腿单tx (一个tx带>1条腿) : {tx_multileg}/{tx_total} ({100*tx_multileg/max(1,tx_total):.2f}%)  (>0=原子multicall; ~0=独立下单)")
