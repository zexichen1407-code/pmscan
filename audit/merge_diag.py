import ijson, math
from collections import defaultdict
PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
class C:
    __slots__=("legs","merge_sz")
    def __init__(self):
        self.legs=defaultdict(lambda:{"bsz":0.0,"bnot":0.0,"lots":[]}); self.merge_sz=0.0
conds=defaultdict(C)
f=open(PATH,"r",encoding="utf-8")
for row in ijson.items(f,"item"):
    t=row.get("type",""); cid=row.get("conditionId","") or ""
    if t=="TRADE":
        if row.get("side","")!="BUY": continue
        c=conds[cid]; oi=row.get("outcomeIndex",999)
        sz=float(row.get("size",0) or 0); usdc=float(row.get("usdcSize",0) or 0); pr=float(row.get("price",0) or 0)
        c.legs[oi]["bsz"]+=sz; c.legs[oi]["bnot"]+=usdc; c.legs[oi]["lots"].append((pr,sz))
    elif t=="MERGE":
        conds[cid].merge_sz+=float(row.get("size",0) or 0)
f.close()

def vwap_cheap(lots,q):
    rem=q;n=0.0;g=0.0
    for pr,sz in sorted(lots):
        tk=min(sz,rem);n+=pr*tk;g+=tk;rem-=tk
        if rem<=1e-9:break
    return n/g if g>0 else float('nan')

# Variant A: full-leg vwap (not cheapest) -- stricter/honest avg cost
# Variant B: cheapest-matched (generous)
for name,cheap in [("full-leg-vwap",False),("cheapest-matched",True)]:
    pos=neg=0.0; npos=nneg=0
    for cid,c in conds.items():
        if c.merge_sz<=0: continue
        bought=sorted([(oi,l) for oi,l in c.legs.items() if l["bsz"]>0],key=lambda x:-x[1]["bsz"])
        if len(bought)<2: continue
        (a,la),(b,lb)=bought[0],bought[1]
        m=min(la["bsz"],lb["bsz"],c.merge_sz)
        if m<=1e-9: continue
        if cheap:
            p=vwap_cheap(la["lots"],m); q=vwap_cheap(lb["lots"],m)
        else:
            p=la["bnot"]/la["bsz"]; q=lb["bnot"]/lb["bsz"]
        if math.isnan(p) or math.isnan(q): continue
        edge=1-(p+q); locked=edge*m
        if edge>=0: pos+=locked;npos+=1
        else: neg+=locked;nneg+=1
    print(f"[{name}] pos={pos:.0f}({npos}) neg={neg:.0f}({nneg}) NET={pos+neg:.0f}")
