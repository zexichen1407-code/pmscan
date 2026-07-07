# -*- coding: utf-8 -*-
"""Diagnostics: explain negative lags + characterize merge burst structure + redeem context."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson, json, statistics
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

split_by_cond=defaultdict(int)
merge_by_cond=defaultdict(list)
buy_leg=defaultdict(lambda:{0:[],1:[]})

with open(PATH,"r",encoding="utf-8") as f:
    for row in ijson.items(f,"item"):
        t=row.get("type","")
        if t not in ("TRADE","MERGE","SPLIT"): continue
        cid=row.get("conditionId","") or ""
        ts=row.get("timestamp",0) or 0
        try: ts=int(ts)
        except: ts=0
        sz=row.get("size",0) or 0
        try: sz=float(sz)
        except: sz=0.0
        if t=="SPLIT":
            split_by_cond[cid]+=1
        elif t=="MERGE":
            merge_by_cond[cid].append((ts,sz))
        else:
            if (row.get("side","") or "")!="BUY": continue
            oi=row.get("outcomeIndex",999)
            try: oi=int(oi)
            except: oi=999
            if oi in (0,1): buy_leg[cid][oi].append((ts,sz))

neg_conds=0; neg_with_split=0; neg_multiday=0; total=0
for cid,mlst in merge_by_cond.items():
    legs=buy_leg.get(cid)
    if not legs: continue
    b0=sorted(legs[0]); b1=sorted(legs[1])
    if not b0 or not b1: continue
    total+=1
    pair_ready=max(b0[0][0],b1[0][0])
    first_merge=min(t for t,_ in mlst)
    if first_merge-pair_ready<0:
        neg_conds+=1
        if split_by_cond.get(cid,0)>0: neg_with_split+=1
        mts=[t for t,_ in mlst]
        if max(mts)-min(mts) > 86400: neg_multiday+=1

print(f"total both-leg+merge conds: {total}")
print(f"negative-first-lag conds: {neg_conds}")
print(f"  of which have SPLIT on same cond: {neg_with_split}")
print(f"  of which merges span >1 day (re-entry episodes): {neg_multiday}")
print()

same_ts_groups=[]
for cid,mlst in merge_by_cond.items():
    by_ts=defaultdict(int)
    for ts,_ in mlst:
        by_ts[ts]+=1
    for ts,c in by_ts.items():
        same_ts_groups.append(c)
multi=[c for c in same_ts_groups if c>1]
print("=== merge 'same-timestamp burst' structure ===")
print(f"distinct (cond,ts) merge groups: {len(same_ts_groups)}")
print(f"  groups with >1 merge at same ts: {len(multi)} ({100*len(multi)/len(same_ts_groups):.1f}%)")
print(f"  merges in a same-ts burst: {sum(multi)}/{sum(same_ts_groups)} = {100*sum(multi)/sum(same_ts_groups):.1f}%")
print(f"  max merges in one same-ts group: {max(same_ts_groups)}  median group size: {statistics.median(same_ts_groups)}")
print()

all_ts=sorted(t for lst in merge_by_cond.values() for t,_ in lst)
gaps=[all_ts[i]-all_ts[i-1] for i in range(1,len(all_ts))]
print("=== global consecutive-merge cadence ===")
for thr in (0,1,5,60,300):
    n=sum(1 for g in gaps if g<=thr)
    print(f"  gap <= {thr}s: {n}/{len(gaps)} = {100*n/len(gaps):.1f}%")
