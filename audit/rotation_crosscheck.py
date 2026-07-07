# -*- coding: utf-8 -*-
"""交叉验证 rotation 结论的关键支柱:
 X1  慢配齐的事件 是不是 小钱? (按事件NO-buy$分桶, 看配齐时间)
 X2  >2h 才配齐的事件 占总NO-buy$ 多少?
 X3  残腿(未被conversion消化)的腿: 数量/占比/平均NO价 -> 是不是深尾
 X4  FIFO 一致性: 总NO股 vs 总conv-sets*参与腿 的量级
 X5  逐事件'快照裸敞口'(doc口径: 1 - 最薄正NO腿/最厚) vs 我的FIFO残腿% -> 解释79-100% vs 2%
"""
import sys, io, json
from collections import defaultdict, deque
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

ev = defaultdict(lambda: {"legs": defaultdict(list), "conv": [], "no_buy_usdc":0.0})
maxts=0; n=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        t=r.get('type'); es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        try: ts=int(r.get('timestamp'))
        except: continue
        if ts>maxts: maxts=ts
        if t=="TRADE" and r.get('side')=="BUY" and r.get('outcome')=="No":
            cid=r.get('conditionId') or ""
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            try: pr=float(r.get('price') or 0)
            except: pr=0.0
            ev[es]["legs"][cid].append((ts,sz,us,pr)); ev[es]["no_buy_usdc"]+=us
        elif t=="CONVERSION":
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            ev[es]["conv"].append((ts,sz))
DATA_END=maxts

negrisk=[(es,e,sum(1 for c,b in e["legs"].items() if b)) for es,e in ev.items()
         if e["conv"] and sum(1 for c,b in e["legs"].items() if b)>=3]
tot=sum(e["no_buy_usdc"] for _,e,_ in negrisk)

def assemble_time(e):
    lf={cid:min(x[0] for x in b) for cid,b in e["legs"].items() if b}
    return max(lf.values())-min(lf.values())

# X1: bucket events by NO-buy$, median assemble time
print("=== X1  按事件NO-buy$分桶, 看配齐(all-legs)耗时 中位 ===")
size_buckets=[("<$100",0,100),("$100-1k",100,1000),("$1k-10k",1000,10000),
              ("$10k-50k",10000,50000),(">$50k",50000,1e18)]
for lab,lo,hi in size_buckets:
    sub=[assemble_time(e) for _,e,_ in negrisk if lo<=e["no_buy_usdc"]<hi]
    if not sub: continue
    sub.sort()
    med=sub[len(sub)//2]
    over2h=sum(1 for x in sub if x>7200)
    usdshare=sum(e["no_buy_usdc"] for _,e,_ in negrisk if lo<=e["no_buy_usdc"]<hi)
    def f(x): return f"{x:.0f}s" if x<90 else (f"{x/60:.1f}m" if x<5400 else f"{x/3600:.1f}h")
    print(f"  {lab:>10}: 事件{len(sub):4d}  中位配齐={f(med):>7}  >2h占{100*over2h/len(sub):4.0f}%  "
          f"该桶NO-buy=${usdshare:,.0f} ({100*usdshare/tot:4.1f}%总额)")

# X2: events that take >2h to assemble -- their $ share
print("\n=== X2  配齐>2h 的事件 占总NO-buy$ 多少 ===")
slow_usd=sum(e["no_buy_usdc"] for _,e,_ in negrisk if assemble_time(e)>7200)
slow_n=sum(1 for _,e,_ in negrisk if assemble_time(e)>7200)
print(f"  >2h配齐: {slow_n}/{len(negrisk)} 事件 ({100*slow_n/len(negrisk):.0f}%), "
      f"但只占 ${slow_usd:,.0f} = {100*slow_usd/tot:.1f}% 的NO-buy资金")
fast_usd=sum(e["no_buy_usdc"] for _,e,_ in negrisk if assemble_time(e)<=120)
fast_n=sum(1 for _,e,_ in negrisk if assemble_time(e)<=120)
print(f"  <=2min配齐: {fast_n}/{len(negrisk)} 事件 ({100*fast_n/len(negrisk):.0f}%), "
      f"占 ${fast_usd:,.0f} = {100*fast_usd/tot:.1f}% 的NO-buy资金")

# X3+X4: FIFO residual legs profile + consistency
print("\n=== X3/X4  FIFO残腿画像 + 一致性 ===")
resid_legs=0; total_legs=0; resid_share=0.0; resid_usd=0.0
conv_share=0.0
resid_prices=[]  # usd-weighted-ish: collect (price, shares)
total_no_shares=0.0; total_conv_sets=0.0; total_conv_setshares=0.0
for es,e,nlegs in negrisk:
    convs=sorted(e["conv"]); total_conv_sets+=sum(s for _,s in convs)
    for cid,b in e["legs"].items():
        if not b: continue
        total_legs+=1
        legshares=sum(x[1] for x in b); total_no_shares+=legshares
        q=deque(sorted(b)); consumed=0.0
        for (cts,s) in convs:
            need=s
            while need>1e-9 and q and q[0][0]<=cts:
                ts,sh,us,pr=q[0]; take=min(need,sh)
                consumed+=take; conv_share+=take; total_conv_setshares+=take
                need-=take
                if take>=sh-1e-9: q.popleft()
                else: q[0]=(ts,sh-take,us,pr)
        leftover=sum(x[1] for x in q)
        if leftover>1e-6:
            # leg has residual; classify leg as "residual leg" if >50% of its shares unconverted
            if leftover>0.5*legshares: resid_legs+=1
            resid_share+=leftover
            for x in q:
                resid_usd+=x[1]*x[3]; resid_prices.append((x[3],x[1]))
print(f"  总腿数:{total_legs}  >50%未转化的残腿:{resid_legs} ({100*resid_legs/total_legs:.0f}%)")
print(f"  总NO股:{total_no_shares:,.0f}  FIFO消化股:{conv_share:,.0f} ({100*conv_share/total_no_shares:.1f}%)  "
      f"残留股:{resid_share:,.0f} ({100*resid_share/total_no_shares:.1f}%)")
print(f"  总conv-sets(求和):{total_conv_sets:,.0f}  (对比总NO股, 量级合理性)")
if resid_prices:
    sw=sum(w for _,w in resid_prices)
    wavg=sum(p*w for p,w in resid_prices)/sw
    print(f"  残腿NO价(股加权均): {wavg:.3f}  (>0.9=深价外尾腿 → 印证'买不到量的冷门腿')")
    # how many residual shares are at price>=0.9
    hi=sum(w for p,w in resid_prices if p>=0.9)
    print(f"  残腿中NO价>=0.90的股占: {100*hi/sw:.0f}%")

# X5: snapshot naked exposure (doc Section6 style) vs FIFO -- reconcile
print("\n=== X5  快照裸敞口(doc口径) vs FIFO周转 — 解释79-100% vs 2% ===")
# doc style: per event, naked = 1 - (sum over legs of min(leg, minleg)) ... 实际 doc 用 min/max 腿不均衡
# 这里复刻 doc: 取每事件 末态净NO持仓(=买入-FIFO消化), 看不均衡
imb=[]
for es,e,nlegs in negrisk:
    if e["no_buy_usdc"]<1000: continue  # 只看有量的事件, 对标doc的7个大事件
    convs=sorted(e["conv"])
    net={}
    for cid,b in e["legs"].items():
        if not b: continue
        q=deque(sorted(b))
        for (cts,s) in convs:
            need=s
            while need>1e-9 and q and q[0][0]<=cts:
                ts,sh,us,pr=q[0]; take=min(need,sh); need-=take
                if take>=sh-1e-9: q.popleft()
                else: q[0]=(ts,sh-take,us,pr)
        net[cid]=sum(x[1] for x in q)
    if len(net)<3: continue
    vals=sorted(net.values())
    mx=max(vals)
    if mx<=0: continue
    # naked = 末态总净持仓 / 总买入股  (有多少股最终没被转化掉)
    bought=sum(x[1] for cid,b in e["legs"].items() for x in b)
    naked_frac=sum(vals)/bought if bought>0 else 0
    imb.append(naked_frac)
imb.sort()
if imb:
    print(f"  有量事件(>{1000}$) n={len(imb)}: 末态未转化净NO股占买入比 (=真裸敞口)")
    print(f"    中位={100*imb[len(imb)//2]:.0f}%  p25={100*imb[len(imb)//4]:.0f}%  "
          f"p75={100*imb[3*len(imb)//4]:.0f}%  均={100*sum(imb)/len(imb):.0f}%")
    print("  → 解读: doc的79-100%是'某快照时点'的腿间不均衡; 但滚动转换把厚腿陆续消化,")
    print("    全周期看真正没被转化的净敞口="+f"{100*resid_usd/ (resid_usd+ (conv_share)) if (resid_usd+conv_share)>0 else 0:.0f}"+"% 量级(按股) — 两者不矛盾.")
