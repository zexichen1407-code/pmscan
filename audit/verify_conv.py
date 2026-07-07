# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r'C:\Users\zexi\pmscan\audit\raw_activity_full.json'
n=0; conv=0; conv_eq=0; conv_neq=0
samples=[]; sizes=[]; usz=[]; neq=[]
types={}
conv_outcomes={}; conv_sides={}; conv_prices={}
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        t=r.get('type'); types[t]=types.get(t,0)+1
        if t=='CONVERSION':
            conv+=1
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            sizes.append(sz); usz.append(us)
            o=r.get('outcome'); conv_outcomes[o]=conv_outcomes.get(o,0)+1
            sd=r.get('side'); conv_sides[sd]=conv_sides.get(sd,0)+1
            pr=r.get('price'); conv_prices[pr]=conv_prices.get(pr,0)+1
            if abs(sz-us)<1e-6: conv_eq+=1
            else:
                conv_neq+=1
                if len(neq)<8: neq.append((sz,us,r.get('price'),r.get('outcome'),r.get('side'),r.get('eventSlug')))
            if len(samples)<6:
                samples.append({k:r.get(k) for k in ('type','eventSlug','conditionId','outcome','side','size','usdcSize','price','timestamp')})
print('rows total:',n)
print('type counts:',types)
print('CONVERSION rows:',conv,'  size==usdcSize:',conv_eq,'  size!=usdcSize:',conv_neq)
print('sum size:',round(sum(sizes),2),' sum usdcSize:',round(sum(usz),2))
print('conv outcome values:',conv_outcomes)
print('conv side values:',conv_sides)
print('conv price distinct (top):', dict(sorted(conv_prices.items(), key=lambda x:-x[1])[:5]))
print('--- sample CONVERSION rows ---')
for s in samples: print(s)
print('--- size!=usdcSize samples ---')
for s in neq: print(s)
