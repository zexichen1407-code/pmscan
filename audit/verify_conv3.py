# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
from collections import defaultdict
RAW = r'C:\Users\zexi\pmscan\audit\raw_activity_full.json'

# For neg-risk events: relate per-event SUM(conversion size) to per-event NO-buy shares.
# On-chain: converting _amount sets of m NO legs returns _amount*(m-1) USDC and _amount each
#   complement YES. The data-api row's size==usdcSize. If size were "USDC returned" it would
#   equal amount*(m-1). If size were "sets converted (=amount)" it would equal amount.
# Test: per event, does total_conv_size track total_NO_buy_shares / m  (sets), or  *(m-1)?

ev = defaultdict(lambda: {"no_shares":0.0,"no_usdc":0.0,"conv_size":0.0,"nlegs":set(),
                          "all_cids":set(),"conv_cids":set()})
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); es=r.get('eventSlug') or ''
        cid=r.get('conditionId') or ''
        if cid: ev[es]["all_cids"].add(cid)
        if t=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No':
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["no_shares"]+=sz; ev[es]["no_usdc"]+=us; ev[es]["nlegs"].add(cid)
        elif t=='CONVERSION':
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            ev[es]["conv_size"]+=sz; ev[es]["conv_cids"].add(cid)

negrisk=[(es,e) for es,e in ev.items() if e["conv_size"]>0 and len(e["nlegs"])>=3]
print('neg-risk events:',len(negrisk))

# conv_cid membership: in all_cids of event? in NO-legs?
in_all=0; in_no=0; tot=0
for es,e in negrisk:
    for c in e["conv_cids"]:
        tot+=1
        if c in e["all_cids"]: in_all+=1
        if c in e["nlegs"]: in_no+=1
print(f'conv conditionIds: total={tot}  appear among event all-traded-cids: {in_all} ({100*in_all/tot:.0f}%)  '
      f'among NO-buy legs: {in_no} ({100*in_no/tot:.0f}%)')

# Aggregate totals
tot_no_sh=sum(e["no_shares"] for _,e in negrisk)
tot_no_usd=sum(e["no_usdc"] for _,e in negrisk)
tot_conv=sum(e["conv_size"] for _,e in negrisk)
print(f'\nTotals over neg-risk events:')
print(f'  NO-buy shares: {tot_no_sh:,.0f}   NO-buy usdc: ${tot_no_usd:,.0f}')
print(f'  CONVERSION size(=usdcSize) sum: {tot_conv:,.0f}')
print(f'  ratio conv_size / NO_shares = {tot_conv/tot_no_sh:.3f}')
print(f'  (if size=sets and he buys ~m NO per set, NO_shares ~= m*sets, ratio ~ 1/m;')
print(f'   if size=USDC returned = sets*(m-1), ratio ~ (m-1)/m )')

# per-event ratio of conv_size to no_shares, and to (no_shares - max_leg) [=sets*(m-1) proxy]
import statistics
ratios=[]
for es,e in negrisk:
    if e["no_shares"]>0:
        ratios.append(e["conv_size"]/e["no_shares"])
ratios.sort()
print(f'\nper-event conv_size/no_shares: median={ratios[len(ratios)//2]:.3f} '
      f'p25={ratios[len(ratios)//4]:.3f} p75={ratios[3*len(ratios)//4]:.3f}')
