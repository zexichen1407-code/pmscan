"""
D2 ROI recompute, D9 identity, profit-sign logic, SPLIT/MERGE mechanics,
current-value vs 99%-buy self-consistency, and reward last7d/last30d recompute.
"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
acts=json.load(open(r"C:/Users/zexi/pmscan/audit/raw_activity_full.json",encoding="utf-8"))
DAY=86400
def f(x):
    try:return float(x)
    except:return 0.0

# ---- D2 ROI = profit/volume ----
prof={"1d":-92.9176540349506,"7d":-17171.121621911945,"30d":86208.89174780372,"all":278513.58949265635}
vol={"1d":44463.359241,"7d":934687.8806470002,"30d":6970942.165357001,"all":11830383.483070001}
agentroi={"1d":-0.20897578505330414,"7d":-1.837096850985799,"30d":1.2366892408924286,"all":2.354222835559187}
print("=== D2 ROI recompute (profit/volume*100) ===")
for w in ["1d","7d","30d","all"]:
    r=100*prof[w]/vol[w]
    print(f"  {w:4} recomputed={r:.6f}%  agent={agentroi[w]:.6f}%  match={abs(r-agentroi[w])<1e-9}")

# ---- D9 identity ----
ids=set()
for a in acts:
    ids.add((a.get("name"),a.get("pseudonym"),a.get("bio")))
print("\n=== D9 identity (distinct name/pseudonym/bio in activity) ===")
for x in list(ids)[:10]: print("  ",x)
print(f"  distinct identity tuples: {len(ids)}")

# ---- reward last7d / last30d recompute ----
tmax=max(int(a["timestamp"]) for a in acts if a.get("timestamp"))
RT={"REWARD","MAKER_REBATE","TAKER_REBATE","YIELD"}
r7=r30=0.0
for a in acts:
    if a.get("type") in RT:
        ts=int(a["timestamp"]); u=f(a.get("usdcSize"))
        if ts>=tmax-7*DAY: r7+=u
        if ts>=tmax-30*DAY: r30+=u
print(f"\n=== reward windows (anchor tmax={tmax}) ===")
print(f"  last7d={r7:.2f} (agent 2644.64)  last30d={r30:.2f} (agent 5318.22)")

# ---- SPLIT vs MERGE mechanics ----
splits=[a for a in acts if a.get("type")=="SPLIT"]
merges=[a for a in acts if a.get("type")=="MERGE"]
print(f"\n=== SPLIT/MERGE mechanics ===")
print(f"  SPLIT n={len(splits)} usdc=${sum(f(a.get('usdcSize')) for a in splits):,.2f}  avg ${sum(f(a.get('usdcSize')) for a in splits)/len(splits):,.0f}")
print(f"  MERGE n={len(merges)} usdc=${sum(f(a.get('usdcSize')) for a in merges):,.2f}  avg ${sum(f(a.get('usdcSize')) for a in merges)/len(merges):,.0f}")
print(f"  SPLIT distinct conditionIds: {len(set(a.get('conditionId') for a in splits))}")
print(f"  MERGE distinct conditionIds: {len(set(a.get('conditionId') for a in merges))}")
# Mechanic note: SPLIT = mint full set from USDC; MERGE = redeem full set to USDC.
# An arber who builds complementary sets by BUYING legs (not splitting) then MERGEs
# to capture <$1 basket would show many MERGE, few SPLIT. Consistent.

# ---- current value vs 99% buy ----
# open exposure: BUY usdc - SELL usdc - MERGE - REDEEM (rough net cost basis remaining)
buy=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE' and a.get('side')=='BUY')
sell=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE' and a.get('side')=='SELL')
merge=sum(f(a.get('usdcSize')) for a in merges)
conv=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='CONVERSION')
redeem=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='REDEEM')
print(f"\n=== position flow (rough) ===")
print(f"  BUY ${buy:,.0f}  SELL ${sell:,.0f}  MERGE(out) ${merge:,.0f}  CONVERSION ${conv:,.0f}  REDEEM(out) ${redeem:,.0f}")
print(f"  current_value(API)=$6,275.51  (open mark-to-market). Tiny vs $8.3M BUY because")
print(f"  positions are continuously closed via MERGE/REDEEM/resolution, not held.")
print(f"  => a 99%-buy wallet with ~$6k residual value is consistent with high-velocity")
print(f"     buy-then-merge/redeem arb (capital recycled, little left open).")

# ---- profit sign logic ----
print(f"\n=== profit sign self-consistency ===")
print(f"  1d={prof['1d']:.0f} 7d={prof['7d']:.0f} (NEG) ; 30d={prof['30d']:.0f} all={prof['all']:.0f} (POS)")
print(f"  7d loss(-17171) within 30d gain(+86209): last 7d is a drawdown inside a")
print(f"  positive 30d. Consistent: recent week down, month/lifetime up.")
print(f"  Note all(+278514) > 30d(+86209) => most lifetime profit earned BEFORE last 30d.")
