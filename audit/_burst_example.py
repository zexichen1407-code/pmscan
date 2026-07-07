# -*- coding: utf-8 -*-
import sys, io, pickle
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
d=pickle.load(open(r"C:\Users\zexi\pmscan\audit\negrisk_trades.pkl",'rb'))
# find the widest same-second fan-out (14 legs) and print its per-leg tx+price
rows=d['2026-nba-champion']
fills=[r for r in rows if r['side']=='BUY' and r['outcome']=='No']
bysec=defaultdict(list)
for r in fills: bysec[int(r['ts'])].append(r)
sec=max(bysec, key=lambda s: len({r['cid'] for r in bysec[s]}))
sub=bysec[sec]
legs={r['cid'] for r in sub}
txs={r['tx'] for r in sub}
print(f"NBA 最宽同秒爆发 ts={sec} : {len(sub)}笔, {len(legs)}条不同腿, {len(txs)}个不同tx")
seen=set()
for r in sorted(sub,key=lambda x:x['price']):
    if r['cid'] in seen: continue
    seen.add(r['cid'])
    print(f"  leg={r['cid'][:10]} px={r['price']:.3f} sz={r['size']:.0f} tx={r['tx'][:14]} {r['title'][:32]}")
