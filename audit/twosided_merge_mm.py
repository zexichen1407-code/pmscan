import ijson, pickle, os
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
OUT  = r"C:\Users\zexi\pmscan\audit\twosided_stream.pkl"

# Per conditionId aggregation. We keep it lean to survive 252MB / ~millions of rows.
# For each cond:
#   buy0_sz, buy0_notional  (outcomeIndex 0 BUY: sum size, sum price*size)
#   buy1_sz, buy1_notional  (outcomeIndex 1 BUY)
#   has_merge (bool)
#   fills: list of (ts, oi, side, price, size)   -- only TRADE fills; needed for cadence + skew + volatility
cond = defaultdict(lambda: {
    "b0s":0.0,"b0n":0.0,"b1s":0.0,"b1n":0.0,
    "s0s":0.0,"s1s":0.0,           # sells per side (for inventory)
    "merge":0, "title":"",
    "fills":[],                    # (ts, oi, side, price, size)
})

n=0
with open(PATH,"r",encoding="utf-8") as f:
    for row in ijson.items(f,"item"):
        n+=1
        t = row.get("type","")
        cid = row.get("conditionId","") or ""
        if not cid:
            continue
        c = cond[cid]
        if not c["title"] and row.get("title"):
            c["title"]=row.get("title","")
        if t=="MERGE":
            c["merge"]+=1
        elif t=="TRADE":
            try: oi=int(row.get("outcomeIndex",-1))
            except: oi=-1
            side=row.get("side","") or ""
            try: pr=float(row.get("price",0) or 0)
            except: pr=0.0
            try: sz=float(row.get("size",0) or 0)
            except: sz=0.0
            try: ts=int(row.get("timestamp",0) or 0)
            except: ts=0
            if oi not in (0,1):
                continue
            if side=="BUY":
                if oi==0:
                    c["b0s"]+=sz; c["b0n"]+=pr*sz
                else:
                    c["b1s"]+=sz; c["b1n"]+=pr*sz
            elif side=="SELL":
                if oi==0: c["s0s"]+=sz
                else: c["s1s"]+=sz
            c["fills"].append((ts,oi,side,pr,sz))

print("total rows:", n)
print("total conds:", len(cond))

# Filter to genuine two-sided merge-arb: BOUGHT BOTH sides AND has MERGE
sel = {}
for cid,c in cond.items():
    if c["b0s"]>0 and c["b1s"]>0 and c["merge"]>0:
        sel[cid]=c
print("two-sided + merge conds:", len(sel))

with open(OUT,"wb") as out:
    pickle.dump(sel, out)
print("saved", OUT, os.path.getsize(OUT))
