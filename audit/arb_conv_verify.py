# -*- coding: utf-8 -*-
"""
Verify conv_size semantics: is conv_size == number of sets (per-leg token count)
or == total NO tokens burned (N * sets)?

Test: for each pure-conversion event (no merge, no redeem), compare conv_size
to (a) min NO-buy leg size, (b) sum NO-buy leg sizes / N.
If conv_size ~= min-leg-size ~= sum/N, then conv_size == sets.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
with open(AGG,'r',encoding='utf-8') as f: agg=json.load(f)

rows=[]
for slug,e in agg.items():
    N=len(e["conds"])
    if e["conv_size"]<=0 or N<2: continue
    no_sizes=[]
    for cid,c in e["conds"].items():
        nob=c["out"].get("No",{}).get("BUY")
        if nob and nob["size"]>0: no_sizes.append(nob["size"])
    if len(no_sizes)<N: continue  # need NO on every leg
    minno=min(no_sizes); sumno=sum(no_sizes)
    rows.append((slug,N,e["conv_size"],minno,sumno,sumno/N))

rows.sort(key=lambda r:-r[2])
print("%-44s %3s %12s %12s %12s %12s %6s %6s"%("slug","N","conv_size","min_no","sum_no","sum/N","cs/min","cs/(s/N)"))
for slug,N,cs,minno,sumno,sn in rows[:20]:
    print("%-44s %3d %12.1f %12.1f %12.1f %12.1f %6.2f %6.2f"%(
        slug[:44],N,cs,minno,sumno,sn, cs/minno if minno else 0, cs/sn if sn else 0))
