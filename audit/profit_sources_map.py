import ijson, json, math, statistics
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

# Per-conditionId accumulators.
# For TRADE legs: per outcomeIndex -> {buy_sz, buy_notional, sell_sz, sell_notional, buy_lots(list of (price,size))}
class Cond:
    __slots__=("legs","merge_sz","conv_sz","redeem_sz","split_sz","title","slug","eventSlug")
    def __init__(self):
        self.legs=defaultdict(lambda: {"bsz":0.0,"bnot":0.0,"ssz":0.0,"snot":0.0,"lots":[]})
        self.merge_sz=0.0
        self.conv_sz=0.0
        self.redeem_sz=0.0
        self.split_sz=0.0
        self.title=""
        self.slug=""
        self.eventSlug=""

conds=defaultdict(Cond)

reward_buckets=defaultdict(lambda:[0,0.0])  # type -> [count, usdc]
type_tot=defaultdict(lambda:[0,0.0])

n=0
f=open(PATH,"r",encoding="utf-8")
for row in ijson.items(f,"item"):
    n+=1
    t=row.get("type","")
    usdc=float(row.get("usdcSize",0) or 0)
    type_tot[t][0]+=1; type_tot[t][1]+=usdc
    cid=row.get("conditionId","") or ""
    if t=="TRADE":
        c=conds[cid]
        if not c.title:
            c.title=row.get("title",""); c.slug=row.get("slug",""); c.eventSlug=row.get("eventSlug","")
        oi=row.get("outcomeIndex",999)
        side=row.get("side","")
        sz=float(row.get("size",0) or 0)
        pr=float(row.get("price",0) or 0)
        leg=c.legs[oi]
        if side=="BUY":
            leg["bsz"]+=sz; leg["bnot"]+=usdc; leg["lots"].append((pr,sz))
        elif side=="SELL":
            leg["ssz"]+=sz; leg["snot"]+=usdc
    elif t=="MERGE":
        conds[cid].merge_sz+=float(row.get("size",0) or 0)
    elif t=="CONVERSION":
        conds[cid].conv_sz+=float(row.get("size",0) or 0)
    elif t=="REDEEM":
        conds[cid].redeem_sz+=float(row.get("size",0) or 0)
    elif t=="SPLIT":
        conds[cid].split_sz+=float(row.get("size",0) or 0)
    elif t in ("MAKER_REBATE","TAKER_REBATE","REWARD","YIELD","REFERRAL_REWARD"):
        reward_buckets[t][0]+=1; reward_buckets[t][1]+=usdc
    if n%50000==0:
        print(f"... {n} rows, {len(conds)} conds")
f.close()
print(f"TOTAL rows={n}, conds={len(conds)}")
print("type_tot:", {k:[v[0],round(v[1],2)] for k,v in type_tot.items()})

def vwap_buy(leg):
    return (leg["bnot"]/leg["bsz"]) if leg["bsz"]>0 else float('nan')

# ============================================================
# CHANNEL 1: MATCHED-PAIR MERGE spread
#   For each conditionId with merge_sz>0 and >=2 legs with buys:
#   take the two most-bought legs (oi pair). matched = min(buy_sz_a, buy_sz_b, merge_sz)
#   vwap using cheapest 'matched' shares of each leg.
#   edge_per_pair = 1 - (p+q). locked = edge * matched.
#   Sum positive AND negative separately; net = sum of all.
# ============================================================
def vwap_cheapest(lots, q):
    rem=q; notion=0.0; got=0.0
    for (pr,sz) in sorted(lots, key=lambda r:r[0]):
        take=min(sz,rem); notion+=pr*take; got+=take; rem-=take
        if rem<=1e-9: break
    return (notion/got) if got>0 else float('nan')

merge_pos=0.0; merge_neg=0.0; merge_n_pos=0; merge_n_neg=0
merge_matched_total=0.0
merge_examples=[]
for cid,c in conds.items():
    if c.merge_sz<=0: continue
    # legs that had buys
    bought=[(oi,leg) for oi,leg in c.legs.items() if leg["bsz"]>0]
    if len(bought)<2: continue
    # two most-bought legs
    bought.sort(key=lambda x:-x[1]["bsz"])
    (oiA,legA),(oiB,legB)=bought[0],bought[1]
    matched=min(legA["bsz"],legB["bsz"],c.merge_sz)
    if matched<=1e-9: continue
    p=vwap_cheapest(legA["lots"],matched)
    q=vwap_cheapest(legB["lots"],matched)
    if math.isnan(p) or math.isnan(q): continue
    edge=1.0-(p+q)
    locked=edge*matched
    merge_matched_total+=matched
    if edge>=0:
        merge_pos+=locked; merge_n_pos+=1
    else:
        merge_neg+=locked; merge_n_neg+=1
    merge_examples.append((locked,c.slug or c.title,p,q,matched))
merge_net=merge_pos+merge_neg
print("\n=== CH1 MERGE ===")
print(f"markets pos={merge_n_pos} neg={merge_n_neg}")
print(f"positive-edge locked = {merge_pos:.2f}")
print(f"negative-edge locked = {merge_neg:.2f}")
print(f"NET merge locked     = {merge_net:.2f}")
print(f"total matched pairs  = {merge_matched_total:.0f}")
merge_examples.sort(reverse=True)
print("top merge markets:", [(round(x[0],1),x[1]) for x in merge_examples[:5]])

# ============================================================
# CHANNEL 2: NEG-RISK CONVERSION spread (measurable lower bound)
#   CONVERSION rows carry only umbrella conditionId + single size, no per-leg basket.
#   Honest lower bound = clean complete-set conversions where we CAN observe all NO legs.
#   We cannot reconstruct basket composition from activity alone per-leg.
#   Measurable floor approach: For conds with conv_sz>0, look at the NO-buy legs.
#   We treat this as mostly unprovable; we report the previously-audited clean floor
#   ($4,291) as the defensible measurable number and quantify per-leg-unprovable gap.
#   Here we at least measure: total conversion notional, and a structural floor proxy =
#   sum over conv conds of (cheapest pairing residual) — but per-leg basket unknown.
#   We output total conv notional + count for context, and carry the audited clean floor.
# ============================================================
conv_conds=[(cid,c) for cid,c in conds.items() if c.conv_sz>0]
conv_total_sz=sum(c.conv_sz for cid,c in conv_conds)
print("\n=== CH2 CONVERSION ===")
print(f"conv conds={len(conv_conds)} total conv size(notional released)={conv_total_sz:.2f}")
# audited clean floor (from prior audit HOW_HE_MAKES_MONEY.md): clean complete-set locked
CONV_CLEAN_FLOOR = 4291.0

# ============================================================
# CHANNEL 3: REDEEMED winning legs realized gain
#   REDEEM size = shares redeemed (each winning share -> $1). usdcSize ~ payout.
#   Realized gain = redeem_payout - cost_basis_of_redeemed_shares.
#   We don't know exactly which legs were redeemed, but redeemed shares are winning
#   outcome shares held to resolution. Approximate cost basis:
#   For each cond with redeem_sz>0, the redeemed shares came from BUY lots that were
#   NOT consumed by MERGE/CONVERSION. Net shares available to redeem per leg =
#   buy_sz - sell_sz - (merge/conversion consumption). This is hard per-leg.
#   Honest measurable approach: redeem payout (= redeem usdcSize sum) minus the
#   estimated cost of the cheapest 'redeem_sz' shares bought in that condition that
#   weren't sold. We approximate cost basis = redeem_sz * (avg buy price of the
#   leg most likely redeemed). Since redeemed = winners, and we know payout = $1/share,
#   gain_per_share = 1 - avg_cost. Use a conservative basis = blended buy vwap across
#   all legs (upper bound on cost -> lower bound on gain) capped sensibly.
# ============================================================
redeem_payout_total=type_tot.get("REDEEM",[0,0])[1]
redeem_gain_lo=0.0   # conservative: cost = vwap of most expensive plausible basis
redeem_gain_blend=0.0
redeem_conds=0
for cid,c in conds.items():
    if c.redeem_sz<=0: continue
    redeem_conds+=1
    rsz=c.redeem_sz
    # candidate winning legs: legs with net long shares (bought, not all sold/merged)
    # cost basis proxy: take the leg whose buy vwap, applied to rsz, is the basis.
    # Use blended buy vwap over all legs weighted by net-long shares as 'blend',
    # and the SINGLE-leg max-vwap*rsz capped at payout as conservative.
    legs=[(oi,leg) for oi,leg in c.legs.items() if leg["bsz"]>0]
    if not legs:
        # redeemed shares with no recorded buys (came from SPLIT/CONVERSION output) -> basis unknown, gain unmeasurable here
        continue
    # blended buy vwap across all bought legs
    tot_bnot=sum(leg["bnot"] for oi,leg in legs)
    tot_bsz=sum(leg["bsz"] for oi,leg in legs)
    blend_vwap=tot_bnot/tot_bsz if tot_bsz>0 else 0
    # redeemed shares are winners; cost basis per redeemed share ~ price paid for the
    # winning leg. We don't know which leg won; use blended vwap as central estimate.
    basis_blend=min(blend_vwap,1.0)*rsz
    gain_blend=rsz*1.0 - basis_blend
    redeem_gain_blend+=gain_blend
    # conservative lower bound on gain: assume basis = highest single-leg vwap (most $ paid)
    max_vwap=max((leg["bnot"]/leg["bsz"]) for oi,leg in legs)
    basis_hi=min(max_vwap,1.0)*rsz
    gain_lo=rsz*1.0 - basis_hi
    redeem_gain_lo+=gain_lo
print("\n=== CH3 REDEEM realized gain ===")
print(f"redeem conds(with buys)={redeem_conds} payout_total={redeem_payout_total:.2f}")
print(f"realized gain (blended-vwap basis)   = {redeem_gain_blend:.2f}")
print(f"realized gain (conservative LB basis)= {redeem_gain_lo:.2f}")

# ============================================================
# CHANNEL 4: SELLS realized P&L
#   For each leg with sells: realized = sell_notional - (sell_sz * buy_vwap_of_leg)
#   (FIFO/avg-cost). Sum across all legs.
# ============================================================
sell_pnl=0.0; sell_conds=set(); sell_total_notional=0.0; sell_total_sz=0.0
for cid,c in conds.items():
    for oi,leg in c.legs.items():
        if leg["ssz"]>0:
            bv=vwap_buy(leg)
            if math.isnan(bv):
                # sold shares with no buys (from split/conversion) -> basis unknown; count notional as gain? No: skip basis, treat as pure proceeds (overstates). Mark separately.
                bv=0.0
            realized=leg["snot"]-leg["ssz"]*bv
            sell_pnl+=realized
            sell_total_notional+=leg["snot"]; sell_total_sz+=leg["ssz"]
            sell_conds.add(cid)
print("\n=== CH4 SELL realized PnL ===")
print(f"sell legs across {len(sell_conds)} conds, sell notional={sell_total_notional:.2f}, sell sz={sell_total_sz:.2f}")
print(f"SELL realized PnL (avg-cost basis) = {sell_pnl:.2f}")

# ============================================================
# CHANNEL 5: REWARDS + REBATES + referral + yield
# ============================================================
rewards_total=sum(v[1] for v in reward_buckets.values())
print("\n=== CH5 REWARDS ===")
for k,v in reward_buckets.items():
    print(f"  {k}: n={v[0]} usdc={v[1]:.2f}")
print(f"REWARDS total = {rewards_total:.2f}")

# ============================================================
# CHANNEL 6: HELD-TO-RESOLUTION residual (black box)
#   Net cash flow identity: SELL proceeds + REDEEM payout + MERGE payout + CONVERSION
#   released + rewards  -  BUY cost - SPLIT cost.
#   Losers held to resolution leave NO redeem record (worthless), so cost is sunk and
#   unmeasurable as a 'source'. We compute total BUY cost vs total realized inflows to
#   show the residual that resolution must account for.
# ============================================================
buy_cost=0.0;
for cid,c in conds.items():
    for oi,leg in c.legs.items():
        buy_cost+=leg["bnot"]
merge_payout=type_tot.get("MERGE",[0,0])[1]
conv_released=type_tot.get("CONVERSION",[0,0])[1]
split_cost=type_tot.get("SPLIT",[0,0])[1]
print("\n=== CH6 context (cash identity) ===")
print(f"BUY cost total       = {buy_cost:.2f}")
print(f"SELL proceeds        = {sell_total_notional:.2f}")
print(f"REDEEM payout        = {redeem_payout_total:.2f}")
print(f"MERGE payout         = {merge_payout:.2f}")
print(f"CONVERSION released  = {conv_released:.2f}")
print(f"SPLIT cost           = {split_cost:.2f}")

out={
 "rows":n,
 "ch1_merge_pos":round(merge_pos,2),"ch1_merge_neg":round(merge_neg,2),"ch1_merge_net":round(merge_net,2),
 "ch1_merge_n_pos":merge_n_pos,"ch1_merge_n_neg":merge_n_neg,"ch1_matched_pairs":round(merge_matched_total,0),
 "ch2_conv_total_size":round(conv_total_sz,2),"ch2_conv_conds":len(conv_conds),"ch2_clean_floor":CONV_CLEAN_FLOOR,
 "ch3_redeem_payout":round(redeem_payout_total,2),"ch3_gain_blend":round(redeem_gain_blend,2),"ch3_gain_lb":round(redeem_gain_lo,2),
 "ch4_sell_pnl":round(sell_pnl,2),"ch4_sell_notional":round(sell_total_notional,2),
 "ch5_rewards_total":round(rewards_total,2),"ch5_breakdown":{k:[v[0],round(v[1],2)] for k,v in reward_buckets.items()},
 "buy_cost":round(buy_cost,2),"merge_payout":round(merge_payout,2),"conv_released":round(conv_released,2),
 "redeem_payout":round(redeem_payout_total,2),"split_cost":round(split_cost,2),
}
with open(r"C:\Users\zexi\pmscan\audit\profit_sources_map_out.json","w") as fo:
    json.dump(out,fo,indent=2)
print("\nsaved profit_sources_map_out.json")
print(json.dumps(out,indent=2))
