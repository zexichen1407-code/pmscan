import ijson, json, math
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

legs=defaultdict(lambda: {"bsz":0.0,"bnot":0.0,"ssz":0.0,"snot":0.0})
f=open(PATH,"r",encoding="utf-8")
for row in ijson.items(f,"item"):
    if row.get("type")!="TRADE": continue
    cid=row.get("conditionId","") or ""
    oi=row.get("outcomeIndex",999)
    side=row.get("side","")
    sz=float(row.get("size",0) or 0)
    usdc=float(row.get("usdcSize",0) or 0)
    leg=legs[(cid,oi)]
    if side=="BUY":
        leg["bsz"]+=sz; leg["bnot"]+=usdc
    elif side=="SELL":
        leg["ssz"]+=sz; leg["snot"]+=usdc
f.close()

# SELL pnl decomposition
pnl_withbasis=0.0   # sells where leg had buys >= sells (basis fully known)
pnl_capped=0.0      # sells capped at buy_sz (basis known only for buy_sz portion)
proceeds_nobasis=0.0  # sell shares exceeding buy_sz -> shares from conversion/split output, basis unknown
n_nobasis_legs=0
sell_legs=0
for (cid,oi),leg in legs.items():
    if leg["ssz"]<=0: continue
    sell_legs+=1
    bv=(leg["bnot"]/leg["bsz"]) if leg["bsz"]>0 else None
    if bv is None:
        # all sold shares have NO recorded buys
        proceeds_nobasis+=leg["snot"]; n_nobasis_legs+=1
        continue
    sold=leg["ssz"]; sp=leg["snot"]; svwap=sp/sold
    matched=min(sold,leg["bsz"])
    # realized on matched portion (basis known)
    pnl_capped += matched*(svwap-bv)
    if sold>leg["bsz"]:
        excess=sold-leg["bsz"]
        proceeds_nobasis += excess*svwap
        n_nobasis_legs+=1
    # full avg-cost (overstates if excess)
    pnl_withbasis += sp - sold*bv

print("sell_legs", sell_legs)
print("pnl avg-cost full (basis=0 for no-buy) approx =", round(pnl_withbasis,2), " (excludes no-buy legs entirely here)")
print("pnl on matched-basis portion only           =", round(pnl_capped,2))
print("proceeds with NO known basis (excess+no-buy) =", round(proceeds_nobasis,2), "across", n_nobasis_legs, "legs")
print("=> honest SELL realized PnL (basis-known only) =", round(pnl_capped,2))
