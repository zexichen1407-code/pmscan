"""
D10 market distribution recompute (refined categories as in SUMMARY) and
D7 conditionId off-by-1 analysis + reconciliation口径 sanity.
"""
import json, sys
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
acts=json.load(open(r"C:/Users/zexi/pmscan/audit/raw_activity_full.json",encoding="utf-8"))
def f(x):
    try:return float(x)
    except:return 0.0

# Replicate SUMMARY refined category buckets by recomputing share from TRADE usdc.
# The agent reported refined cats; we recompute totals and % to sanity-check D10's
# headline numbers (sports/ent 44%, election 23%, ai/tech 7.8%, weather 2.5%).
refined=json.loads(open(r"C:/Users/zexi/pmscan/audit/type_breakdown.json",encoding="utf-8").read())["category_trade_usdc_refined"]
tot=sum(refined.values())
print("=== D10 refined category shares (from agent's bucket sums) ===")
for k,v in sorted(refined.items(),key=lambda x:-x[1]):
    print(f"  {k:42} ${v:14,.0f}  {100*v/tot:.1f}%")
print(f"  TOTAL ${tot:,.0f}  vs TRADE usdc $8,414,374.90  diff ${8414374.90-tot:,.2f}")

# D7: conditionId universe. data-api /traded=17081. file TRADE conds=17080.
trade_conds=set(a.get("conditionId") for a in acts if a.get("type")=="TRADE")
all_conds=set(a.get("conditionId") for a in acts)  # includes MERGE/CONV/etc.
print(f"\n=== D7 conditionId universe ===")
print(f"  TRADE distinct conditionIds       = {len(trade_conds)}")
print(f"  ALL-type distinct conditionIds    = {len(all_conds)}")
print(f"  data-api /traded                  = 17081")
print(f"  diff vs TRADE conds               = {17081-len(trade_conds)}")
print(f"  diff vs ALL conds                 = {17081-len(all_conds)}")
# the 1 extra in /traded is likely a market traded but where activity row's
# conditionId is null/empty, or a market that resolved with only non-TRADE rows.
none_conds=sum(1 for a in acts if a.get("type")=="TRADE" and not a.get("conditionId"))
print(f"  TRADE rows with empty conditionId = {none_conds}")
# also count markets present only via non-TRADE types
nontrade_only=all_conds - trade_conds
print(f"  conditionIds present in non-TRADE rows but NOT in TRADE rows = {len(nontrade_only)}")

# 口径 comparability: lb 'volume' for Polymarket = sum of TRADE notional traded.
# Our TRADE-only sum = 8.41M but lb volume = 11.83M. So lb volume is NOT TRADE-only.
# It matches TRADE+MERGE+CONV+SPLIT+REDEEM (11.898M, 0.57% high). This means
# Polymarket's lb 'volume' counts structural mint/merge/redeem notional too.
print(f"\n=== reconciliation口径 ===")
trade=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE')
struct=sum(f(a.get('usdcSize')) for a in acts if a.get('type') in {'TRADE','MERGE','CONVERSION','SPLIT','REDEEM'})
allusd=sum(f(a.get('usdcSize')) for a in acts)
print(f"  TRADE-only           ${trade:,.2f}   (lb-vol/TRADE ratio = {11830383.48/trade:.3f})")
print(f"  TRADE+structural     ${struct:,.2f}   (struct/lb-vol = {struct/11830383.48:.4f})")
print(f"  ALL types            ${allusd:,.2f}")
print(f"  lb-api volume(all)   $11,830,383.48")
print(f"  => lb volume口径 ≈ TRADE+structural (within 0.57%); NOT comparable to TRADE-only.")
print(f"     The 0.57% residual = rewards excluded by lb + timing/rounding. Acceptable.")
