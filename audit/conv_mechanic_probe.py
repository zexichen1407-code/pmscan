# -*- coding: utf-8 -*-
"""铁判据: 他到底是"满套转(8个全配平)"还是"转子集(6/8)"?
守恒约束(无假设): 一辈子能做的干净满套转换数 <= 最薄腿总买入股数(min_leg).
 若 总转换套数 C > min_leg => 至少 (C - min_leg) 套是排除了最薄腿的子集转换 = 铁证子集转换.
 must_subset_frac = max(0, C - min_leg)/C  = 至少这么多比例的转换不是满套.
对 7 个 doc 事件逐个 + 全 2028 事件聚合 + 几个事件逐腿明细."""
import sys, io
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

NAMED = {
 "fed-decision-in-july-181":"Fed(干净)",
 "colombia-presidential-election":"Colombia(极端倾斜)",
 "2026-nba-champion":"NBA(巨亏)",
 "when-will-gpt-5pt6-be-released":"GPT",
 "which-company-has-best-ai-model-end-of-june":"BestAI($439k)",
}

ev=defaultdict(lambda:{"legs":defaultdict(lambda:{"sh":0.0,"usd":0.0,"first":None}),
                       "conv_sets":0.0,"conv_usd":0.0,"conv_n":0,"conv_first":None,
                       "no_usd":0.0,"first_buy":None,"allN_touch":None})
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        try: ts=int(r.get('timestamp'))
        except: continue
        if t=="TRADE" and r.get('side')=="BUY" and r.get('outcome')=="No":
            cid=r.get('conditionId') or ""
            try: sh=float(r.get('size') or 0)
            except: sh=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            L=ev[es]["legs"][cid]; L["sh"]+=sh; L["usd"]+=us
            if L["first"] is None or ts<L["first"]: L["first"]=ts
            ev[es]["no_usd"]+=us
            if ev[es]["first_buy"] is None or ts<ev[es]["first_buy"]: ev[es]["first_buy"]=ts
        elif t=="CONVERSION":
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            ev[es]["conv_sets"]+=sz; ev[es]["conv_usd"]+=us; ev[es]["conv_n"]+=1
            if ev[es]["conv_first"] is None or ts<ev[es]["conv_first"]: ev[es]["conv_first"]=ts

negrisk={es:e for es,e in ev.items()
         if e["conv_sets"]>0 and sum(1 for c,L in e["legs"].items() if L["sh"]>0)>=3}

def stat(es,e):
    legs=[L["sh"] for L in e["legs"].values() if L["sh"]>0]
    legs.sort()
    N=len(legs); mn=legs[0]; mx=legs[-1]; med=legs[N//2]; tot=sum(legs)
    C=e["conv_sets"]
    must_sub=max(0.0,C-mn)
    frac=must_sub/C if C>0 else 0
    return N,mn,med,mx,tot,C,frac

print("="*100)
print("七个 doc 事件 逐个铁判据:")
print(f"{'事件':<22}{'N':>3}{'最薄腿':>10}{'中位腿':>10}{'最厚腿':>11}{'总买股':>11}{'转换套数C':>11}{'C/最薄':>9}{'C/最厚':>8}{'必是子集%':>9}")
for slug,name in NAMED.items():
    if slug not in negrisk:
        print(f"{name:<22} 不在样本"); continue
    N,mn,med,mx,tot,C,frac=stat(slug,negrisk[slug])
    print(f"{name:<22}{N:>3}{mn:>10.0f}{med:>10.0f}{mx:>11.0f}{tot:>11.0f}{C:>11.0f}"
          f"{C/mn if mn else 0:>9.0f}{C/mx if mx else 0:>8.2f}{100*frac:>8.0f}%")

print("\n"+"="*100)
print("全 2028 事件聚合 (铁判据, 无假设):")
fracs=[]; cw_must=0.0; cw_tot=0.0; n_clean=0; n_subset=0
big=[]
for es,e in negrisk.items():
    N,mn,med,mx,tot,C,frac=stat(es,e)
    fracs.append(frac)
    # capital weight by conversion volume (sets)
    cw_must += max(0.0,C-mn); cw_tot += C
    if C<=mn*1.05: n_clean+=1
    else: n_subset+=1
    big.append((e["no_usd"],es,N,mn,mx,C,frac))
fracs.sort()
print(f"  事件级: 能干净满套(C<=最薄腿)的: {n_clean}/{len(negrisk)} ({100*n_clean/len(negrisk):.0f}%)  "
      f"铁定有子集转换(C>最薄腿)的: {n_subset}/{len(negrisk)} ({100*n_subset/len(negrisk):.0f}%)")
print(f"  must_subset_frac 分布: 中位={100*fracs[len(fracs)//2]:.0f}%  p25={100*fracs[len(fracs)//4]:.0f}%  p75={100*fracs[3*len(fracs)//4]:.0f}%")
print(f"  ★ 按转换量加权: 全部转换套数里, 铁定排除了最薄腿(子集)的占 {100*cw_must/cw_tot:.0f}%")
print(f"  (这是下界: 实际子集转换只会更多, 因为排除任意腿都算)")

print("\n  按事件资金排序 Top12 (看大钱事件是不是也子集转):")
big.sort(reverse=True)
print(f"  {'NO买入$':>11}{'N':>4}{'最薄腿':>9}{'最厚腿':>10}{'C':>10}{'必子集%':>9}  事件")
for no_usd,es,N,mn,mx,C,frac in big[:12]:
    print(f"  {no_usd:>11,.0f}{N:>4}{mn:>9.0f}{mx:>10.0f}{C:>10.0f}{100*frac:>8.0f}%  {es[:40]}")

# 逐腿明细: Fed vs Colombia
print("\n"+"="*100)
for slug in ["fed-decision-in-july-181","colombia-presidential-election"]:
    if slug not in negrisk: continue
    e=negrisk[slug]
    print(f"\n逐腿明细 [{NAMED.get(slug,slug)}]  转换{e['conv_n']}次/共{e['conv_sets']:.0f}套  NO买入${e['no_usd']:,.0f}")
    rows=sorted(([L['sh'],cid,L['usd']] for cid,L in e['legs'].items() if L['sh']>0),reverse=True)
    for sh,cid,usd in rows:
        print(f"   腿 NO股={sh:>10.0f}  ${usd:>9,.0f}  vwap={usd/sh if sh else 0:.3f}  {cid[:20]}")
    mn=min(r[0] for r in rows)
    print(f"   → 最薄腿={mn:.0f}股, 但他转了{e['conv_sets']:.0f}套 → 最薄腿最多参与{mn:.0f}套, "
          f"其余{max(0,e['conv_sets']-mn):.0f}套({100*max(0,e['conv_sets']-mn)/e['conv_sets']:.0f}%)铁定没带最薄腿")
