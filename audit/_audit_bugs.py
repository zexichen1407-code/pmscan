# -*- coding: utf-8 -*-
# Audit specific potential bugs in user scripts.
import sys, io, json
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
def to_f(x):
    try: return float(x)
    except: return 0.0
def to_i(x):
    try: return int(x)
    except: return None

# Collect with usdcSize too, to compare $ definitions and check conversion usdc.
events = defaultdict(lambda: {"no": defaultdict(list), "conv": [], "conv_usdc":0.0, "conv_size":0.0, "no_buy_usdc":0.0})
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); ts=to_i(r.get('timestamp'))
        if ts is None: continue
        es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or "__noevent__"
        if t=="TRADE" and r.get('side')=="BUY" and r.get('outcome')=="No":
            events[es]["no"][r.get('conditionId') or ""].append((ts,to_f(r.get('size')),to_f(r.get('price'))))
            events[es]["no_buy_usdc"]+=to_f(r.get('usdcSize'))
        elif t=="CONVERSION":
            events[es]["conv"].append((ts,to_f(r.get('size'))))
            events[es]["conv_usdc"]+=to_f(r.get('usdcSize'))
            events[es]["conv_size"]+=to_f(r.get('size'))
negrisk=[(es,e,sum(1 for c,l in e["no"].items() if l)) for es,e in events.items()
         if e["conv"] and sum(1 for c,l in e["no"].items() if l)>=3]

# BUG CHECK 1: conv_usdc vs conv_size. Claim text said usdcSize==size for CONVERSION.
cu=sum(e["conv_usdc"] for _,e,_ in negrisk); cs=sum(e["conv_size"] for _,e,_ in negrisk)
print(f"CONVERSION over negrisk: sum size(sets)={cs:,.0f}  sum usdcSize={cu:,.0f}  (prompt claims usdcSize==size)")
# inspect a few conversion rows raw
import ijson as ij
shown=0
with open(RAW,'rb') as fh:
    for r in ij.items(fh,'item'):
        if r.get('type')=="CONVERSION":
            print("  CONV raw: size=",r.get('size')," usdcSize=",r.get('usdcSize')," price=",r.get('price'))
            shown+=1
            if shown>=6: break

# BUG CHECK 2: does any single CONVERSION consume from a leg whose available shares < s,
#   i.e. is total consumed across legs ever > s*N? Check max over-consumption.
#   Also: total consumed shares should be ~ sum over conv of s*(legs that had enough). Compare to sum(s)*avgN.
tot_consumed=0.0
with_residual_legs=0; tot_legs=0
for es,e,nlegs in negrisk:
    convs=sorted(e["conv"])
    for cid,l in e["no"].items():
        if not l: continue
        tot_legs+=1
        q=deque(sorted(l)); start=sum(x[1] for x in l)
        for (cts,s) in convs:
            need=s
            while need>1e-9 and q and q[0][0]<=cts:
                bts,bsh,bpr=q[0]; take=min(need,bsh); tot_consumed+=take; need-=take
                if take>=bsh-1e-9: q.popleft()
                else: q[0]=(bts,bsh-take,bpr)
        if sum(x[1] for x in q)>1e-6: with_residual_legs+=1
print(f"\nFIFO: total consumed shares={tot_consumed:,.0f}  legs={tot_legs}  legs w/ residual={with_residual_legs}")
print(f"  sum(conv sets)={cs:,.0f}; if every conv fully consumed s from each of {sum(nl for _,_,nl in negrisk)/len(negrisk):.1f} avg legs => upper bound {cs* (sum(nl for _,_,nl in negrisk)/len(negrisk)):,.0f}")

# BUG CHECK 3: rotation_analysis line 300 -> M3_round_fill_span uses 'dist.__self__ if False else None'
#   => harmless (always None) but dead/confusing. Note it.

# BUG CHECK 4: M2 includes events where first conversion is BEFORE first NO buy (neg gap).
neg=0
for es,e,nlegs in negrisk:
    t0=min(min(x[0] for x in l) for l in e["no"].values() if l)
    fc=min(c[0] for c in e["conv"])
    if fc<t0: neg+=1
print(f"\nM2 events with first-conv before first-NO-buy (neg gap): {neg}")
