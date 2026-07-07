"""
META-AUDIT independent recompute (THIRD implementation).
Deliberately different from audit1_recompute.py:
  - STREAMING parse via ijson (not json.load full-load)
  - Decimal arithmetic (not float) for money sums
  - independent field discovery (what columns actually exist? gas? fee? price?)
  - reproduces D3/D4/D5/D7/D8 + dedup, plus NEW probes the prior agents skipped:
      * field universe (is there any gas/fee/realizedPnl field at all?)
      * usdcSize sign distribution (are MERGE/CONVERSION usdc positive? what does it represent?)
      * timestamp-sliced activity volume per rolling window vs lb-api window volume
"""
import ijson, json, sys, datetime
from collections import defaultdict, Counter
from decimal import Decimal, getcontext
getcontext().prec = 40
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

P = r"C:/Users/zexi/pmscan/audit/raw_activity_full.json"
DAY = 86400
NOW_MAX = 1782377918  # max ts in data; use as "now" anchor for rolling-window slicing

D0 = Decimal(0)
typ_cnt = Counter()
typ_usdc = defaultdict(lambda: D0)
buy_n=sell_n=other_side=0
buy_u=sell_u=D0
sell_conds=set(); all_trade_conds=set()
rew_usdc=defaultdict(lambda: D0); rew_cnt=Counter()
total_all=D0
tmin=None; tmax=None
field_universe=Counter()       # which keys appear, how often
side_values=Counter()
# dedup: hash a tuple, but use a DIFFERENT key construction order than audit (still same semantic key)
seen_agentkey=set(); dup_agent=0
# stronger structural-uniqueness probe done via full-line distinctness on a hashed digest
import hashlib
seen_fullhash=set(); dup_fullrow=0
N=0
# rolling-window activity volume (structural usdc) sliced by timestamp from the SAME activity file
WINS={"1d":1,"7d":7,"30d":30}
win_struct=defaultdict(lambda: D0)
struct_types={"TRADE","MERGE","CONVERSION","SPLIT","REDEEM"}
trade_usdc_in_win=defaultdict(lambda: D0)  # TRADE-only per window

def D(x):
    try: return Decimal(str(x))
    except: return D0

with open(P, "rb") as fh:
    for a in ijson.items(fh, "item"):
        N+=1
        for k in a.keys(): field_universe[k]+=1
        t=a.get("type")
        u=D(a.get("usdcSize"))
        typ_cnt[t]+=1; typ_usdc[t]+=u; total_all+=u
        ts=a.get("timestamp")
        if ts is not None:
            ts=int(ts)
            tmin = ts if tmin is None else min(tmin,ts)
            tmax = ts if tmax is None else max(tmax,ts)
        if t=="TRADE":
            cid=a.get("conditionId"); all_trade_conds.add(cid)
            s=a.get("side"); side_values[s]+=1
            if s=="BUY": buy_n+=1; buy_u+=u
            elif s=="SELL": sell_n+=1; sell_u+=u; sell_conds.add(cid)
            else: other_side+=1
        if t in {"REWARD","MAKER_REBATE","TAKER_REBATE","YIELD","REFERRAL_REWARD"}:
            rew_usdc[t]+=u; rew_cnt[t]+=1
        # dedup keys
        ak=(a.get("transactionHash"),a.get("asset"),a.get("type"),a.get("timestamp"),
            a.get("outcomeIndex"),a.get("side"),a.get("usdcSize"))
        if ak in seen_agentkey: dup_agent+=1
        else: seen_agentkey.add(ak)
        fh_digest=hashlib.md5(json.dumps(a,sort_keys=True,default=str).encode()).hexdigest()
        if fh_digest in seen_fullhash: dup_fullrow+=1
        else: seen_fullhash.add(fh_digest)
        # rolling-window slice using NOW_MAX as anchor
        if ts is not None and t in struct_types:
            age=NOW_MAX-ts
            for w,days in WINS.items():
                if age <= days*DAY:
                    win_struct[w]+=u
                    if t=="TRADE": trade_usdc_in_win[w]+=u

def money(x): return f"${x:,.2f}"

print(f"records (streamed) = {N}")
print("\n=== FIELD UNIVERSE (key -> #rows present) ===")
for k,c in field_universe.most_common():
    print(f"  {k:22} {c}")

print("\n=== D3 TYPE BREAKDOWN (Decimal, streamed) ===")
for t in sorted(typ_cnt,key=lambda x:-typ_usdc[x]):
    print(f"  {str(t):16} n={typ_cnt[t]:7}  usdc={money(typ_usdc[t])}")
print(f"  TOTAL n={sum(typ_cnt.values())}  usdc={money(total_all)}")

print("\n=== D4 BUY/SELL ===")
tu=buy_u+sell_u
print(f"  BUY n={buy_n} usdc={money(buy_u)} ({(100*buy_u/tu):.2f}% usdc, {100*buy_n/(buy_n+sell_n):.2f}% cnt)")
print(f"  SELL n={sell_n} usdc={money(sell_u)} ({(100*sell_u/tu):.2f}% usdc)")
print(f"  TRADE side values = {dict(side_values)}  other/blank={other_side}")
print(f"  TRADE distinct conds={len(all_trade_conds)}  sell-conds={len(sell_conds)} ({100*len(sell_conds)/len(all_trade_conds):.2f}%)")

print("\n=== D5 REWARDS ===")
rew_agent=sum(v for t,v in rew_usdc.items() if t!="REFERRAL_REWARD")
rew_all=sum(rew_usdc.values())
for t in sorted(rew_usdc,key=lambda x:-rew_usdc[x]):
    print(f"  {t:18} n={rew_cnt[t]:4} usdc={money(rew_usdc[t])}")
print(f"  agentset(no REFERRAL)={money(rew_agent)}  all={money(rew_all)}")
print(f"  reward/allPnL(278513.59): agentset={100*rew_agent/Decimal('278513.59'):.2f}%  all={100*rew_all/Decimal('278513.59'):.2f}%")

print("\n=== D7 RECONCILIATION ===")
sum_struct=sum(typ_usdc[t] for t in struct_types)
print(f"  sum ALL usdcSize = {money(total_all)}")
print(f"  TRADE only       = {money(typ_usdc['TRADE'])}")
print(f"  struct sum       = {money(sum_struct)}")
LB=Decimal('11830383.48')
print(f"  lb vol all       = {money(LB)}")
print(f"  struct - lb      = {money(sum_struct-LB)} ({100*(sum_struct-LB)/LB:.3f}%)")
print(f"  lb / TRADE-only  = {LB/typ_usdc['TRADE']:.4f}")
print(f"  TRADE conds={len(all_trade_conds)} vs data-api traded=17081 diff={17081-len(all_trade_conds)}")

print("\n=== D8 TIME SPAN ===")
print(f"  min={tmin} {datetime.datetime.utcfromtimestamp(tmin)}")
print(f"  max={tmax} {datetime.datetime.utcfromtimestamp(tmax)}")
print(f"  span={(tmax-tmin)/DAY:.2f} d")

print("\n=== DEDUP (streamed, independent) ===")
print(f"  rows={N} distinct_agentkey={len(seen_agentkey)} residual_dup_agent={dup_agent}")
print(f"  distinct_fullrow_hash={len(seen_fullhash)} residual_dup_fullrow={dup_fullrow}")

print("\n=== NEW: rolling-window activity volume (sliced from activity, anchored at max ts) ===")
print("   compares activity-derived struct/TRADE volume to lb-api WINDOW volume")
lbwin={"1d":Decimal('44463.359241'),"7d":Decimal('934687.8806470002'),"30d":Decimal('6970942.165357001')}
for w in ["1d","7d","30d"]:
    print(f"  {w}: activity struct={money(win_struct[w])}  TRADE-only={money(trade_usdc_in_win[w])}  | lb-api vol={money(lbwin[w])}")

out={
 "impl":"ijson-streaming+Decimal (third independent)",
 "records":N,"total_all":str(total_all.quantize(Decimal('0.01'))),
 "type_breakdown":{t:{"count":typ_cnt[t],"usdc":str(typ_usdc[t].quantize(Decimal('0.01')))} for t in typ_cnt},
 "buy_sell":{"buy_n":buy_n,"buy_usdc":str(buy_u.quantize(Decimal('0.01'))),"sell_n":sell_n,
   "sell_usdc":str(sell_u.quantize(Decimal('0.01'))),
   "buy_usdc_pct":float(round(100*buy_u/tu,2)),"buy_count_pct":round(100*buy_n/(buy_n+sell_n),2),
   "trade_conds":len(all_trade_conds),"sell_conds":len(sell_conds)},
 "rewards":{t:{"count":rew_cnt[t],"usdc":str(rew_usdc[t].quantize(Decimal('0.01')))} for t in rew_usdc},
 "rewards_agentset":str(rew_agent.quantize(Decimal('0.01'))),"rewards_all":str(rew_all.quantize(Decimal('0.01'))),
 "reconciliation":{"sum_all":str(total_all.quantize(Decimal('0.01'))),"trade_only":str(typ_usdc['TRADE'].quantize(Decimal('0.01'))),
   "struct":str(sum_struct.quantize(Decimal('0.01'))),"lb_vol":str(LB),"trade_conds":len(all_trade_conds)},
 "time_span":{"min":tmin,"max":tmax,"days":round((tmax-tmin)/DAY,2)},
 "dedup":{"rows":N,"distinct_agentkey":len(seen_agentkey),"residual_dup_agent":dup_agent,
   "distinct_fullrow":len(seen_fullhash),"residual_dup_fullrow":dup_fullrow},
 "field_universe":dict(field_universe),
 "rolling_window_activity_vol":{w:{"struct":str(win_struct[w].quantize(Decimal('0.01'))),
    "trade_only":str(trade_usdc_in_win[w].quantize(Decimal('0.01')))} for w in WINS},
}
json.dump(out,open(r"C:/Users/zexi/pmscan/audit/meta_recompute_out.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("\nwrote meta_recompute_out.json")
