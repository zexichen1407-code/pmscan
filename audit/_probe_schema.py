# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

want = {"TRADE":2, "CONVERSION":3, "MERGE":2, "REDEEM":2, "SPLIT":2}
got = {k:0 for k in want}
seen_types = {}
examples = []
n=0
with open(RAW,'rb') as fh:
    for rec in ijson.items(fh,'item'):
        n+=1
        t = rec.get('type','?')
        seen_types[t] = seen_types.get(t,0)+1
        if t in want and got[t] < want[t]:
            got[t]+=1
            examples.append((t, {k: rec.get(k) for k in
                ['type','conditionId','eventSlug','slug','outcome','outcomeIndex',
                 'side','timestamp','size','usdcSize','price','title','transactionHash','negRiskMarketId','negRisk']}))
        if n >= 400000 and all(got[k]>=want[k] for k in want):
            break
print("rows scanned:", n)
print("type counts (within scanned):", seen_types)
print()
for t, ex in examples:
    print("===", t, "===")
    for k,v in ex.items():
        print(f"   {k}: {v}")
    print()
