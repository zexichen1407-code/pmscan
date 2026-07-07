import ijson, json, math
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

class C:
    __slots__=("legs","merge_sz","conv_sz","redeem_sz","redeem_usdc","split_sz","slug")
    def __init__(self):
        self.legs=defaultdict(lambda:{"bsz":0.0,"bnot":0.0,"ssz":0.0})
        self.merge_sz=0.0;self.conv_sz=0.0;self.redeem_sz=0.0;self.redeem_usdc=0.0;self.split_sz=0.0;self.slug=""
conds=defaultdict(C)
f=open(PATH,"r",encoding="utf-8")
for row in ijson.items(f,"item"):
    t=row.get("type",""); cid=row.get("conditionId","") or ""
    c=conds[cid]
    if t=="TRADE":
        if not c.slug: c.slug=row.get("slug","")
        oi=row.get("outcomeIndex",999); side=row.get("side","")
        sz=float(row.get("size",0) or 0); usdc=float(row.get("usdcSize",0) or 0)
        if side=="BUY": c.legs[oi]["bsz"]+=sz; c.legs[oi]["bnot"]+=usdc
        elif side=="SELL": c.legs[oi]["ssz"]+=sz
    elif t=="MERGE": c.merge_sz+=float(row.get("size",0) or 0)
    elif t=="CONVERSION": c.conv_sz+=float(row.get("size",0) or 0)
    elif t=="REDEEM": c.redeem_sz+=float(row.get("size",0) or 0); c.redeem_usdc+=float(row.get("usdcSize",0) or 0)
    elif t=="SPLIT": c.split_sz+=float(row.get("size",0) or 0)
f.close()

# REDEEM honest model:
# Redeemed shares are WINNING-outcome shares held to resolution, paid $1 each.
# Cost basis = price he paid for those winning shares. But:
#  - We don't know which outcome won, only redeem_sz total.
#  - Many redeemed shares came from CONVERSION/SPLIT/MERGE leftovers (no buy basis).
# Honest measurable gain requires basis. Two regimes:
#  (A) "clean directional redeem": cond has NO conversion, NO merge, NO split -> redeemed
#      shares trace to TRADE buys only. Here gain = redeem_usdc - cost of redeemed shares.
#      cost basis = take redeem_sz cheapest? No: redeemed = the winning leg specifically.
#      We can bound: redeemed shares <= net long shares. Use blended buy vwap of legs that
#      still have net-long position (bsz-ssz>0) weighted, as central; conservative = max leg vwap.
#  (B) conds with conversion/merge/split -> redeemed shares' basis entangled with structural
#      ops (collateral, not cash) -> NOT cleanly measurable. Exclude from floor.

clean_gain_blend=0.0; clean_gain_lb=0.0; clean_payout=0.0; clean_n=0
entangled_payout=0.0; entangled_n=0; nobuy_payout=0.0
for cid,c in conds.items():
    if c.redeem_sz<=0: continue
    structural = (c.conv_sz>0) or (c.merge_sz>0) or (c.split_sz>0)
    legs=[(oi,l) for oi,l in c.legs.items() if l["bsz"]>0]
    if not legs:
        nobuy_payout+=c.redeem_usdc; continue
    if structural:
        entangled_payout+=c.redeem_usdc; entangled_n+=1; continue
    # clean directional redeem
    clean_n+=1; clean_payout+=c.redeem_usdc
    rsz=c.redeem_sz
    tot_bnot=sum(l["bnot"] for oi,l in legs); tot_bsz=sum(l["bsz"] for oi,l in legs)
    blend=tot_bnot/tot_bsz if tot_bsz>0 else 0
    clean_gain_blend += c.redeem_usdc - min(blend,1.0)*rsz
    maxv=max(l["bnot"]/l["bsz"] for oi,l in legs)
    clean_gain_lb += c.redeem_usdc - min(maxv,1.0)*rsz

print("=== REDEEM regimes ===")
print(f"CLEAN directional redeem conds={clean_n} payout={clean_payout:.2f}")
print(f"  realized gain blended-vwap basis = {clean_gain_blend:.2f}")
print(f"  realized gain conservative LB     = {clean_gain_lb:.2f}")
print(f"ENTANGLED (has conv/merge/split) conds={entangled_n} payout={entangled_payout:.2f}  <- basis not cleanly measurable")
print(f"NO-BUY-basis redeem payout = {nobuy_payout:.2f}  <- shares from split/conv output, basis unknown")
print(f"TOTAL redeem payout = {clean_payout+entangled_payout+nobuy_payout:.2f}")
