# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
from collections import defaultdict
RAW = r'C:\Users\zexi\pmscan\audit\raw_activity_full.json'

ev = defaultdict(lambda: {"no_shares":0.0,"conv_size":0.0,"nlegs":set(),
                          "no_usdc":0.0})
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); es=r.get('eventSlug') or ''
        if t=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No':
            cid=r.get('conditionId') or ''
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["no_shares"]+=sz; ev[es]["nlegs"].add(cid); ev[es]["no_usdc"]+=us
        elif t=='CONVERSION':
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            ev[es]["conv_size"]+=sz

negrisk=[(es,e,len(e["nlegs"])) for es,e in ev.items() if e["conv_size"]>0 and len(e["nlegs"])>=3]
nlegs_list=sorted(n for _,_,n in negrisk)
print('neg-risk events:',len(negrisk))
print('legs per event: median=%d  mean=%.1f  p25=%d p75=%d max=%d' % (
    nlegs_list[len(nlegs_list)//2], sum(nlegs_list)/len(nlegs_list),
    nlegs_list[len(nlegs_list)//4], nlegs_list[3*len(nlegs_list)//4], nlegs_list[-1]))

# Hypothesis A: size = sets converted.  Then NO_shares_consumed ~= sets * (legs touched per set).
#   If he buys ~1 NO per leg per set, NO_shares ~= sets * nlegs  => conv_size/NO_shares ~= 1/nlegs
# Check correlation: per event, compare conv_size to no_shares/nlegs
import statistics
err_A=[]   # |conv_size - no_shares/nlegs| / conv_size   (Hyp A: size=sets)
err_B=[]   # Hyp B: size = USDC returned = sets*(nlegs-1) ~= no_shares*(nlegs-1)/nlegs
for es,e,nl in negrisk:
    if e["conv_size"]>0 and nl>1:
        predA = e["no_shares"]/nl
        predB = e["no_shares"]*(nl-1)/nl
        err_A.append(abs(e["conv_size"]-predA)/e["conv_size"])
        err_B.append(abs(e["conv_size"]-predB)/e["conv_size"])
err_A.sort(); err_B.sort()
print('\nHypothesis A (size = SETS converted): predict conv_size ~= NO_shares / nlegs')
print('   median rel.error = %.2f' % err_A[len(err_A)//2])
print('Hypothesis B (size = USDC RETURNED = sets*(nlegs-1)): predict conv_size ~= NO_shares*(nlegs-1)/nlegs')
print('   median rel.error = %.2f' % err_B[len(err_B)//2])

# Also: implied USDC returned under Hyp A = conv_size * (nlegs-1).  Compare to NO_usdc spent.
tot_ret_A=sum(e["conv_size"]*(nl-1) for _,e,nl in negrisk)
tot_no_usd=sum(e["no_usdc"] for _,e,_ in negrisk)
tot_conv=sum(e["conv_size"] for _,e,_ in negrisk)
print('\nIf size=sets: implied USDC returned = sum(conv_size*(nlegs-1)) = ${:,.0f}'.format(tot_ret_A))
print('   vs NO-buy usdc spent = ${:,.0f}   (ratio {:.2f})'.format(tot_no_usd, tot_ret_A/tot_no_usd))
print('If size=USDC: total face = sum(conv_size) = ${:,.0f}  (ratio {:.2f})'.format(
    tot_conv, tot_conv/tot_no_usd))
