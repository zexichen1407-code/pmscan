# -*- coding: utf-8 -*-
"""
资金周转 / 配齐时间分析  (回答: "平均多少分钟配齐所有 NO 再 conversion, 资金利用率够不够")

口径:
  - 只看 neg-risk 事件 = 至少有 1 次 CONVERSION 的 eventSlug
  - NO 腿 = side==BUY 且 outcome=="No", 每个 conditionId 一条腿
  - 资金回笼通道 = CONVERSION (neg-risk 主通道) + MERGE + REDEEM

四组指标:
  M1  逐事件: 首笔 NO -> 全部 N 腿都至少买到 1 次 (字面"配齐所有 No")
  M2  逐事件: 首笔 NO -> 首次 CONVERSION (开始回本)
  M3  脉冲分轮(>1h 算新一轮): 每轮 首笔 -> 该轮全 N 腿触及
  M4  ★资金持有期 FIFO★: 每一份买入的 NO 股, 多久后被 conversion 消化 (= 真·周转时间);
      未被消化的 = 裸残腿, 持有到结算. 按 股数 & 美元(股×price) 双口径.
"""
import sys, io, json, pickle, os
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
DATA_END = None  # 数据末尾时间戳, 用于残腿持有期 (取扫到的最大 ts)

def pct(s, p):
    if not s: return None
    if len(s)==1: return s[0]
    k=(len(s)-1)*p; f=int(k); c=min(f+1,len(s)-1)
    if f==c: return s[f]
    return s[f]*(c-k)+s[c]*(k-f)

def dist(vals, label, unit="s"):
    if not vals:
        print(f"  {label}: NO DATA"); return {}
    s=sorted(vals)
    d=dict(n=len(s), p10=pct(s,.1), p25=pct(s,.25), median=pct(s,.5),
           p75=pct(s,.75), p90=pct(s,.9), p99=pct(s,.99),
           mean=sum(s)/len(s), min=s[0], max=s[-1])
    def f(x):
        if x is None: return "  -  "
        if unit=="s":
            if x<90: return f"{x:.0f}s"
            if x<5400: return f"{x/60:.1f}m"
            return f"{x/3600:.1f}h"
        return f"{x:.2f}"
    print(f"  {label}: n={d['n']}  p10={f(d['p10'])} p25={f(d['p25'])} "
          f"med={f(d['median'])} p75={f(d['p75'])} p90={f(d['p90'])} "
          f"p99={f(d['p99'])} mean={f(d['mean'])} max={f(d['max'])}")
    return d

# ---------------- PASS: collect per-event ----------------
# event -> { legs: {cid: [(ts,shares,usdc,price)]}, conv: [(ts,size)], merge_usdc, redeem_usdc, conv_usdc }
ev = defaultdict(lambda: {"legs": defaultdict(list), "conv": [], "no_buy_usdc":0.0,
                          "no_buy_shares":0.0, "conv_usdc":0.0, "conv_size":0.0,
                          "merge_usdc":0.0, "redeem_usdc":0.0, "yes_buy_usdc":0.0})
maxts=0
n=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        t=r.get('type')
        es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        ts=r.get('timestamp')
        try: ts=int(ts)
        except: continue
        if ts>maxts: maxts=ts
        if t=="TRADE":
            side=r.get('side'); out=r.get('outcome')
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            try: pr=float(r.get('price') or 0)
            except: pr=0.0
            if side=="BUY" and out=="No":
                cid=r.get('conditionId') or ""
                ev[es]["legs"][cid].append((ts,sz,us,pr))
                ev[es]["no_buy_usdc"]+=us; ev[es]["no_buy_shares"]+=sz
            elif side=="BUY" and out=="Yes":
                ev[es]["yes_buy_usdc"]+=us
        elif t=="CONVERSION":
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["conv"].append((ts,sz))
            ev[es]["conv_usdc"]+=us; ev[es]["conv_size"]+=sz
        elif t=="MERGE":
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["merge_usdc"]+=us
        elif t=="REDEEM":
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["redeem_usdc"]+=us
        if n%50000==0: print("...scan",n,file=sys.stderr)
DATA_END=maxts
print("rows scanned:", n, " data_end_ts:", maxts, file=sys.stderr)

# ---------------- filter to neg-risk arb events ----------------
# neg-risk = has >=1 conversion AND >=3 NO-buy legs (multi-outcome)
negrisk=[]
for es,e in ev.items():
    nlegs=sum(1 for cid,b in e["legs"].items() if b)
    if e["conv"] and nlegs>=3:
        negrisk.append((es,e,nlegs))
print(f"\nneg-risk events (>=1 conversion & >=3 NO legs): {len(negrisk)}")
tot_nobuy=sum(e["no_buy_usdc"] for _,e,_ in negrisk)
tot_conv=sum(e["conv_usdc"] for _,e,_ in negrisk)
print(f"  total NO-buy usdc over these: ${tot_nobuy:,.0f}   total conversion usdc: ${tot_conv:,.0f}")
print(f"  total YES-buy usdc over these: ${sum(e['yes_buy_usdc'] for _,e,_ in negrisk):,.0f} (应≈0)")

# =================================================================
# M1: first NO -> all N legs touched (whole-event)
# M2: first NO -> first conversion
# =================================================================
print("\n================ M1/M2  逐事件 (whole-event) ================")
m1=[]; m2=[]; firstconv_before_full=[]
for es,e,nlegs in negrisk:
    leg_first={cid:min(x[0] for x in b) for cid,b in e["legs"].items() if b}
    t0=min(leg_first.values())
    t_alln=max(leg_first.values())          # 全部腿都首次触及
    m1.append(t_alln-t0)
    convts=sorted(c[0] for c in e["conv"])
    if convts:
        m2.append(convts[0]-t0)
        firstconv_before_full.append(1 if convts[0] < t_alln else 0)
print("M1  首笔NO -> 全部N腿都买到(配齐所有No, 至少各1股):")
d_m1=dist(m1,"all-legs-touched")
print("M2  首笔NO -> 首次 conversion(开始回本):")
d_m2=dist(m2,"first-conversion")
if firstconv_before_full:
    fb=sum(firstconv_before_full)
    print(f"  ★ 首次conversion发生在'配齐所有腿'之前的事件: {fb}/{len(firstconv_before_full)} "
          f"= {100*fb/len(firstconv_before_full):.0f}%  (=他不等配齐就开始转)")

# M1 buckets
def buckets(vals, edges, labels):
    cnt=[0]*(len(edges)+1)
    for v in vals:
        placed=False
        for i,e_ in enumerate(edges):
            if v<=e_: cnt[i]+=1; placed=True; break
        if not placed: cnt[-1]+=1
    tot=len(vals)
    for lab,c in zip(labels,cnt):
        print(f"     {lab:>12}: {c:4d}  ({100*c/tot:5.1f}%)")
edges=[120,300,1800,7200]
labs=["<=2min","2-5min","5-30min","30min-2h",">2h"]
print("  M1 分桶 (配齐所有腿耗时):"); buckets(m1,edges,labs)
print("  M2 分桶 (到首次conversion):"); buckets(m2,edges,labs)

# =================================================================
# M3: pulse-segmented rounds (>1h gap = new round)
# within a round: first fill -> all-legs-touched-in-that-round
# =================================================================
print("\n================ M3  脉冲分轮 (>1h 间隔=新一轮) ================")
GAP=3600
round_fill_span=[]   # 每轮 首笔->该轮最后一腿首触
rounds_per_event=[]
rounds_all_legs=0; rounds_total=0
for es,e,nlegs in negrisk:
    # flatten all NO buys with leg id
    allbuys=[]
    for cid,b in e["legs"].items():
        for (ts,sz,us,pr) in b:
            allbuys.append((ts,cid))
    allbuys.sort()
    if not allbuys: continue
    # segment into rounds by gap
    rounds=[]; cur=[allbuys[0]]
    for i in range(1,len(allbuys)):
        if allbuys[i][0]-allbuys[i-1][0] > GAP:
            rounds.append(cur); cur=[]
        cur.append(allbuys[i])
    rounds.append(cur)
    rounds_per_event.append(len(rounds))
    for rd in rounds:
        rounds_total+=1
        t0=rd[0][0]
        legs_in_round={}
        for ts,cid in rd:
            if cid not in legs_in_round: legs_in_round[cid]=ts
        span=max(legs_in_round.values())-t0
        round_fill_span.append(span)
        if len(legs_in_round)==nlegs:  # 该轮摸全了所有腿
            rounds_all_legs+=1
print(f"  总轮数: {rounds_total}  事件均轮数: {sum(rounds_per_event)/len(rounds_per_event):.1f}")
print(f"  单轮摸全所有腿的占比: {rounds_all_legs}/{rounds_total} = {100*rounds_all_legs/rounds_total:.0f}%")
print("  每轮 首笔->该轮最后腿首触 (建腿耗时/轮):")
dist(round_fill_span,"round-fill-span")
print("  分桶:"); buckets(round_fill_span,edges,labs)

# =================================================================
# M4 ★ 资金持有期 FIFO ★  每份买入NO股 -> 被conversion消化的等待时间
# per leg: FIFO match buys (by ts) against cumulative conversion sets (by ts)
# converted shares: holding = conv_ts - buy_ts  (weight shares & usd=shares*price)
# unconverted shares: naked residual -> holding = DATA_END - buy_ts (censored)
# =================================================================
print("\n================ M4  资金持有期 FIFO (真·周转时间) ================")
hold_conv_s=[]; w_shares_conv=[]; w_usd_conv=[]   # 已转化股的持有期(秒) + 权重
hold_resid_s=[]; w_shares_resid=[]; w_usd_resid=[]
usd_conv_total=0.0; usd_resid_total=0.0; shares_conv_total=0.0; shares_resid_total=0.0

for es,e,nlegs in negrisk:
    convs=sorted(e["conv"])  # (ts,size_sets)
    for cid,b in e["legs"].items():
        if not b: continue
        q=deque(sorted(b))   # (ts,shares,usd,price) FIFO by buy ts
        # walk conversions; each conversion of `s` sets consumes up to s shares from this leg
        for (cts,s) in convs:
            need=s
            while need>1e-9 and q and q[0][0]<=cts:
                ts,sh,us,pr = q[0]
                take=min(need, sh)
                frac=take/sh if sh>0 else 0
                hold=cts-ts
                hold_conv_s.append(hold)
                w_shares_conv.append(take)
                w_usd_conv.append(take*pr)
                usd_conv_total+=take*pr; shares_conv_total+=take
                need-=take
                if take>=sh-1e-9:
                    q.popleft()
                else:
                    q[0]=(ts,sh-take,us*(1-frac),pr)
            # if leg dry or only future buys remain, this conversion drew from other legs
        # leftover = naked residual
        while q:
            ts,sh,us,pr=q.popleft()
            hold=DATA_END-ts
            hold_resid_s.append(hold)
            w_shares_resid.append(sh); w_usd_resid.append(sh*pr)
            usd_resid_total+=sh*pr; shares_resid_total+=sh

def wdist(vals, weights, label):
    """share/usd-weighted percentile of holding time"""
    if not vals:
        print(f"  {label}: NO DATA"); return
    pairs=sorted(zip(vals,weights))
    tot=sum(weights)
    if tot<=0:
        print(f"  {label}: zero weight"); return
    def wp(p):
        target=p*tot; cum=0
        for v,w in pairs:
            cum+=w
            if cum>=target: return v
        return pairs[-1][0]
    def f(x):
        if x<90: return f"{x:.0f}s"
        if x<5400: return f"{x/60:.1f}m"
        if x<172800: return f"{x/3600:.1f}h"
        return f"{x/86400:.1f}d"
    print(f"  {label}: Σw={tot:,.0f}  wP10={f(wp(.1))} wP25={f(wp(.25))} "
          f"wMed={f(wp(.5))} wP75={f(wp(.75))} wP90={f(wp(.9))} wMax={f(pairs[-1][0])}")

print(f"已被conversion消化(rotating)的NO:  shares={shares_conv_total:,.0f}  usd=${usd_conv_total:,.0f}")
print(f"未消化裸残腿(到数据末尾未转):       shares={shares_resid_total:,.0f}  usd=${usd_resid_total:,.0f}")
tot_usd=usd_conv_total+usd_resid_total
if tot_usd>0:
    print(f"★ 资金占比: 快速周转 {100*usd_conv_total/tot_usd:.0f}%  vs  裸残腿沉淀 {100*usd_resid_total/tot_usd:.0f}%")
print("\n  [已转化资金] 从买入到被conversion消化的等待时间 (股数加权):")
wdist(hold_conv_s, w_shares_conv, "holding(shares-wt)")
print("  [已转化资金] 同上 (美元=股×price 加权):")
wdist(hold_conv_s, w_usd_conv, "holding(usd-wt)")
print("\n  [裸残腿] 沉淀到结算的持有期 (美元加权, 右删失):")
wdist(hold_resid_s, w_usd_resid, "residual-hold(usd-wt)")

# 已转化资金分桶 (美元加权)
print("\n  已转化资金 持有期 分桶 (美元加权):")
def wbuckets(vals,weights,edges,labels):
    cnt=[0.0]*(len(edges)+1)
    for v,w in zip(vals,weights):
        placed=False
        for i,e_ in enumerate(edges):
            if v<=e_: cnt[i]+=w; placed=True; break
        if not placed: cnt[-1]+=w
    tot=sum(weights)
    for lab,c in zip(labels,cnt):
        print(f"     {lab:>12}: ${c:12,.0f}  ({100*c/tot:5.1f}%)")
wbuckets(hold_conv_s,w_usd_conv,edges,labs)

# =================================================================
# inter-conversion cadence (global & within active events)
# =================================================================
print("\n================ conversion 节奏 ================")
within=[]
for es,e,nlegs in negrisk:
    cts=sorted(c[0] for c in e["conv"])
    for i in range(1,len(cts)): within.append(cts[i]-cts[i-1])
print("  同事件相邻 conversion 间隔:")
dist(within,"inter-conv")

# save
out={
  "n_negrisk_events":len(negrisk),
  "tot_nobuy_usdc":tot_nobuy, "tot_conv_usdc":tot_conv,
  "M1_all_legs_touched":d_m1, "M2_first_conversion":d_m2,
  "M3_round_fill_span":dist.__self__ if False else None,
  "capital_rotating_pct": (100*usd_conv_total/tot_usd) if tot_usd>0 else None,
  "capital_residual_pct": (100*usd_resid_total/tot_usd) if tot_usd>0 else None,
  "usd_conv_total":usd_conv_total, "usd_resid_total":usd_resid_total,
}
json.dump(out, open(r"C:\Users\zexi\pmscan\audit\rotation_out.json","w"), indent=2)
print("\nWROTE rotation_out.json")
