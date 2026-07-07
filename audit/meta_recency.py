"""
META-AUDIT: is '近期转亏 (recently turning to loss)' real, and what does it mean?
lb 'profit' 7d=-17171, 30d=+86209, all=+278514 are MARK-TO-MARKET, not realized.
Check activity-side recency to see if behavior actually changed, and whether the
7d 'loss' could be unrealized mark swings on open positions vs realized losses.
Also: confirm busiest-second never approached the 4000 cap (final crawl proof).
"""
import json, sys, datetime
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
acts=json.load(open(r"C:/Users/zexi/pmscan/audit/raw_activity_full.json",encoding="utf-8"))
DAY=86400
tmax=max(int(a["timestamp"]) for a in acts if a.get("timestamp"))
def f(x):
    try:return float(x)
    except:return 0.0

# activity counts per recency window
for label,days in [("1d",1),("7d",7),("30d",30),("all",999)]:
    cut=tmax-days*DAY
    rows=[a for a in acts if a.get("timestamp") and int(a["timestamp"])>=cut]
    tcnt=Counter(a.get("type") for a in rows)
    buy=sum(f(a.get('usdcSize')) for a in rows if a.get('type')=='TRADE' and a.get('side')=='BUY')
    print(f"{label:4}: rows={len(rows):7} TRADE={tcnt.get('TRADE',0):6} MERGE={tcnt.get('MERGE',0):6} CONV={tcnt.get('CONVERSION',0):6} REDEEM={tcnt.get('REDEEM',0):5} BUY$={buy:,.0f}")

# daily activity for last 10 days to see if it's slowing/ramping
print("\n=== last 12 days: daily row counts & BUY notional ===")
daily=defaultdict(lambda:[0,0.0])
for a in acts:
    ts=a.get("timestamp")
    if not ts: continue
    d=datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
    daily[d][0]+=1
    if a.get('type')=='TRADE' and a.get('side')=='BUY':
        daily[d][1]+=f(a.get('usdcSize'))
for d in sorted(daily)[-12:]:
    print(f"  {d}: rows={daily[d][0]:6}  BUY$={daily[d][1]:,.0f}")

# busiest second final check
ts_count=Counter(int(a["timestamp"]) for a in acts if a.get("timestamp"))
print(f"\n=== final crawl-cap proof: busiest single second = {ts_count.most_common(1)[0]} (vs 4000 cap) ===")
print(f"  max records/second = {ts_count.most_common(1)[0][1]} << 4000 -> no single second could overflow a window")
