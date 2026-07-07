"""
META-AUDIT: blind spots both prior agents ignored.
 A) GAS: is there ANY gas/fee field? (Polygon gas on 301k ops not in lb PnL?)
 B) REALIZED vs UNREALIZED: lb 'all' profit = mark-to-market. Decompose what
    we CAN see: realized cash flows from activity vs current open value.
 C) CONVERSION mechanics: what does usdcSize mean on a CONVERSION row?
    Sample rows; check price/size/outcome to understand if it's notional or PnL.
 D) Cash-flow PnL reconstruction (independent of lb 'profit'):
    realized_cash = SELL + MERGE + REDEEM + CONVERSION(out?) + rewards
                    - BUY - SPLIT   (+ current open value)
    Compare to lb 'all' profit = 278,513. If wildly different, lb profit is doing
    something we can't see (mark-to-market of open + resolution payouts).
"""
import json, sys
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
acts=json.load(open(r"C:/Users/zexi/pmscan/audit/raw_activity_full.json",encoding="utf-8"))
def f(x):
    try:return float(x)
    except:return 0.0

# A) gas/fee field scan
allkeys=set()
for a in acts[:5000]: allkeys.update(a.keys())
gasish=[k for k in allkeys if any(s in k.lower() for s in ("gas","fee","realiz","pnl","cost","profit","net"))]
print("=== A) GAS/FEE/REALIZED fields present in records ===")
print(f"  all keys: {sorted(allkeys)}")
print(f"  gas/fee/pnl-like keys: {gasish if gasish else 'NONE — no gas, no fee, no realizedPnl anywhere'}")

# B/C) sample CONVERSION + MERGE + REDEEM rows
def sample(t,n=3):
    print(f"\n--- sample {t} rows ---")
    cnt=0
    for a in acts:
        if a.get("type")==t:
            print(f"  usdcSize={a.get('usdcSize')} size={a.get('size')} price={a.get('price')} side={a.get('side')} outcome={a.get('outcome')} outIdx={a.get('outcomeIndex')} title={str(a.get('title'))[:40]}")
            cnt+=1
            if cnt>=n: break
print("\n=== C) mechanics samples ===")
for t in ["CONVERSION","MERGE","REDEEM","SPLIT"]:
    sample(t)

# side distribution within each structural type
print("\n=== side values within structural types ===")
for t in ["CONVERSION","MERGE","REDEEM","SPLIT"]:
    sc=Counter(a.get("side") for a in acts if a.get("type")==t)
    print(f"  {t}: {dict(sc)}")

# D) cash-flow PnL reconstruction
by=defaultdict(float)
for a in acts:
    by[a.get("type")]+=f(a.get("usdcSize"))
buy=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE' and a.get('side')=='BUY')
sell=sum(f(a.get('usdcSize')) for a in acts if a.get('type')=='TRADE' and a.get('side')=='SELL')
MERGE=by['MERGE']; CONV=by['CONVERSION']; REDEEM=by['REDEEM']; SPLIT=by['SPLIT']
rewards=by['REWARD']+by['MAKER_REBATE']+by['TAKER_REBATE']+by['YIELD']
open_val=6275.51  # data-api current value
print("\n=== D) cash-flow PnL reconstruction (independent of lb 'profit') ===")
print(f"  BUY(out cash)    -{buy:,.2f}")
print(f"  SPLIT(out cash)  -{SPLIT:,.2f}   (mint full set: pay $1/set)")
print(f"  SELL(in cash)    +{sell:,.2f}")
print(f"  MERGE(in cash)   +{MERGE:,.2f}   (redeem full set: get $1/set)")
print(f"  REDEEM(in cash)  +{REDEEM:,.2f}   (winning tokens -> $1)")
print(f"  CONVERSION       ?{CONV:,.2f}   (neg-risk convert; direction unclear)")
print(f"  rewards(in)      +{rewards:,.2f}")
print(f"  open value(mark) +{open_val:,.2f}")
# naive: treat MERGE/REDEEM/SELL/rewards as inflow, BUY/SPLIT as outflow, CONV ambiguous
infl = sell+MERGE+REDEEM+rewards+open_val
outf = buy+SPLIT
print(f"\n  WITHOUT conversion: inflow(+open)-outflow = {infl-outf:,.2f}")
print(f"  +CONVERSION as inflow:  {infl+CONV-outf:,.2f}")
print(f"  -CONVERSION as outflow: {infl-CONV-outf:,.2f}")
print(f"  lb-api 'all' profit (target) = 278,513.59")
print("\n  -> if none of these match 278,513, lb profit is NOT a simple activity cash-flow")
print("     sum; usdcSize on structural rows is NOTIONAL not cash PnL. This is the key")
print("     point both prior agents glossed: usdcSize columns DON'T add up to PnL.")
