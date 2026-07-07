# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
from collections import defaultdict
RAW = r'C:\Users\zexi\pmscan\audit\raw_activity_full.json'

# Group CONVERSION rows by (eventSlug, timestamp) to see if a single convert tx
# produces one row per leg or one row per event.
groups = defaultdict(list)   # (es,ts) -> list of (conditionId,size)
# Also: distinct conditionIds appearing in conversion rows per event
conv_cids_per_event = defaultdict(set)
no_cids_per_event = defaultdict(set)   # legs that had NO buys
n=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        t=r.get('type'); es=r.get('eventSlug') or ''
        try: ts=int(r.get('timestamp'))
        except: continue
        if t=='CONVERSION':
            cid=r.get('conditionId') or ''
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            groups[(es,ts)].append((cid,sz))
            conv_cids_per_event[es].add(cid)
        elif t=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No':
            no_cids_per_event[es].add(r.get('conditionId') or '')

# How many conversion rows share the same (event,timestamp)? (= one tx -> N rows?)
sizes_of_groups = [len(v) for v in groups.values()]
from collections import Counter
gc = Counter(sizes_of_groups)
print('CONVERSION (event,ts) group cardinality distribution (rows per same-second-same-event):')
for k in sorted(gc)[:12]:
    print(f'   {k} rows in group: {gc[k]} groups')
print('  max group:', max(sizes_of_groups), ' total groups:', len(groups))

# Within a multi-row group, are all sizes equal (same amount across legs => one convert call) ?
eq_groups=0; neq_groups=0; multi=0
examples=[]
for key,v in groups.items():
    if len(v)>1:
        multi+=1
        szs=[s for _,s in v]
        if max(szs)-min(szs)<1e-6: eq_groups+=1
        else:
            neq_groups+=1
            if len(examples)<5: examples.append((key,v))
print(f'\nMulti-row groups: {multi}  all-sizes-equal: {eq_groups}  sizes-differ: {neq_groups}')
print('examples of size-differing groups:')
for key,v in examples: print('  ',key,'->',[(c[:10],round(s,3)) for c,s in v][:8])

# Compare set of conditionIds in CONVERSION rows vs NO-buy legs, for a few big events
print('\n--- per-event: #conversion-cids vs #NO-legs (sample of events with many convs) ---')
ranked = sorted(conv_cids_per_event.items(), key=lambda kv:-len(kv[1]))[:8]
for es,cids in ranked:
    print(f'  {es[:45]:45s} conv_cids={len(cids):3d}  no_legs={len(no_cids_per_event.get(es,set())):3d}  '
          f'conv_cids subset of no_legs? {cids.issubset(no_cids_per_event.get(es,set()))}')
