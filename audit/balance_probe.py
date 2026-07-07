# -*- coding: utf-8 -*-
"""他到底平不平? 拉出每个事件的全部资金流, 用代币守恒检验"敞口".
代币守恒(若账户最终全部结清/平掉):
  NO 侧:  NO_buy + SPLIT  =  NO_sell + MERGE + NO_burned_by_conv + REDEEM_no
  YES 侧: YES_buy + SPLIT + YES_minted_by_conv = YES_sell + MERGE + REDEEM_yes
他几乎不买YES => 他 merge 用的 YES 只能来自 conversion 铸出(或 split).
关键检验:
  1) conversion 铸出的 YES, 有没有被 merge 吃掉? (merge_sz vs conv 铸YES下界)
  2) 最终未被对冲、留到结算(REDEEM)的有多少? = 真实敞口的可观测部分
  3) USDC 对账: 花出去的 vs 收回来的, 看是否~平(套利) 还是大亏(敞口押错)
注意: conversion 的 usdcSize 是面值(套数), 真实现金=套数*(参与腿数-1), 参与腿数不可观测 => USDC对账只能给区间.
"""
import sys, io
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

NAMED = {
 "fed-decision-in-july-181":"Fed(干净)",
 "colombia-presidential-election":"Colombia(极端倾斜)",
 "2026-nba-champion":"NBA(自报巨亏)",
 "when-will-gpt-5pt6-be-released":"GPT",
 "which-company-has-best-ai-model-end-of-june":"BestAI",
}

def blank():
    return {"no_buy_sh":0.0,"no_buy_u":0.0,"no_sell_sh":0.0,"no_sell_u":0.0,
            "yes_buy_sh":0.0,"yes_buy_u":0.0,"yes_sell_sh":0.0,"yes_sell_u":0.0,
            "split_sz":0.0,"merge_sz":0.0,"conv_sets":0.0,"redeem_sz":0.0,"redeem_u":0.0,
            "legs":defaultdict(float),"conv_n":0,"merge_n":0}
ev=defaultdict(blank)

with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        t=r.get('type'); es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        def f(k):
            try: return float(r.get(k) or 0)
            except: return 0.0
        if t=="TRADE":
            side=r.get('side'); out=r.get('outcome'); sh=f('size'); u=f('usdcSize')
            e=ev[es]
            if out=="No" and side=="BUY": e["no_buy_sh"]+=sh; e["no_buy_u"]+=u; e["legs"][r.get('conditionId') or ""]+=sh
            elif out=="No" and side=="SELL": e["no_sell_sh"]+=sh; e["no_sell_u"]+=u
            elif out=="Yes" and side=="BUY": e["yes_buy_sh"]+=sh; e["yes_buy_u"]+=u
            elif out=="Yes" and side=="SELL": e["yes_sell_sh"]+=sh; e["yes_sell_u"]+=u
        elif t=="SPLIT": ev[es]["split_sz"]+=f('size')
        elif t=="MERGE": ev[es]["merge_sz"]+=f('size'); ev[es]["merge_n"]+=1
        elif t=="CONVERSION": ev[es]["conv_sets"]+=f('size'); ev[es]["conv_n"]+=1
        elif t=="REDEEM": ev[es]["redeem_sz"]+=f('size'); ev[es]["redeem_u"]+=f('usdcSize')

negrisk={es:e for es,e in ev.items()
         if e["conv_sets"]>0 and sum(1 for c,v in e["legs"].items() if v>0)>=3}

def report(es,e,name):
    N=sum(1 for c,v in e["legs"].items() if v>0)
    legs=sorted((v for v in e["legs"].values() if v>0))
    minleg=legs[0]; maxleg=legs[-1]
    C=e["conv_sets"]
    # conversion 铸出YES的下界: 至少 (C-minleg) 套是子集(漏≥1腿), 每套至少铸1个YES => minted_YES >= C-minleg
    minted_yes_lb=max(0.0, C-minleg)
    # YES 的去处: 几乎不买YES, 所以 merge 的YES + 卖出的YES + 赎回的YES = 铸出的YES(+split)
    # merge 能吃掉的YES <= merge_sz ; 卖YES <= yes_sell_sh
    yes_disposed_obs = e["merge_sz"] + e["yes_sell_sh"]   # 可观测的YES消解(不含redeem细分)
    print(f"\n── {name}  [{es[:40]}]  N={N}")
    print(f"   NO  买 {e['no_buy_sh']:>11,.0f}股/${e['no_buy_u']:>10,.0f}   卖 {e['no_sell_sh']:>9,.0f}股")
    print(f"   YES 买 {e['yes_buy_sh']:>11,.0f}股/${e['yes_buy_u']:>10,.0f}   卖 {e['yes_sell_sh']:>9,.0f}股   ← 几乎不买YES")
    print(f"   SPLIT {e['split_sz']:>10,.0f}   MERGE {e['merge_sz']:>11,.0f}({e['merge_n']}次)   "
          f"CONV {C:>11,.0f}套({e['conv_n']}次)   REDEEM {e['redeem_sz']:>10,.0f}")
    print(f"   最薄腿={minleg:,.0f}  最厚腿={maxleg:,.0f}")
    print(f"   → 转换铸出的YES(下界) ≈ C−最薄腿 = {minted_yes_lb:,.0f}")
    print(f"   → 可观测消解YES: merge {e['merge_sz']:,.0f} + 卖YES {e['yes_sell_sh']:,.0f} = {yes_disposed_obs:,.0f}")
    gap=minted_yes_lb - yes_disposed_obs - e['redeem_sz']
    if minted_yes_lb>0:
        cov=100*min(1.0,(yes_disposed_obs+e['redeem_sz'])/minted_yes_lb)
        print(f"   → merge+卖+赎回 覆盖了铸出YES的 {cov:.0f}%  "
              f"{'✓ 基本平(merge把转换的YES吃掉了)' if cov>85 else '✗ 缺口='+format(gap,',.0f')+' = 未对冲(潜在敞口)'}")
    # USDC 对账区间
    out_usd = e['no_buy_u'] + e['yes_buy_u'] + e['split_sz']   # split 1:1 usd
    in_known = e['no_sell_u'] + e['yes_sell_u'] + e['merge_sz'] + e['redeem_u']
    # conversion cash = C*(m-1), m∈[2,N] => [C*1, C*(N-1)]
    lo=in_known + C*1 - out_usd
    hi=in_known + C*(N-1) - out_usd
    print(f"   USDC对账: 花出${out_usd:,.0f}  已知收回${in_known:,.0f}  +转换现金[${C*1:,.0f}~${C*(N-1):,.0f}]")
    print(f"   → 净利区间 [${lo:,.0f} ~ ${hi:,.0f}]  ({'含大额负值=曾押错' if lo<-out_usd*0.3 else '区间整体非大亏'})")

print("="*90)
print("逐事件 资金流 + 代币守恒检验")
for slug,name in NAMED.items():
    if slug in negrisk: report(slug,negrisk[slug],name)

# 聚合: 全样本 merge 能覆盖多少 conversion 铸出的 YES
print("\n"+"="*90)
print("全 2028 事件聚合:")
tot_minted_lb=0.0; tot_merge=0.0; tot_yessell=0.0; tot_redeem=0.0; tot_convsets=0.0
tot_nobuy=0.0; tot_yesbuy=0.0
fully_cov=0
for es,e in negrisk.items():
    legs=sorted((v for v in e["legs"].values() if v>0)); minleg=legs[0]
    minted_lb=max(0.0,e["conv_sets"]-minleg)
    tot_minted_lb+=minted_lb; tot_merge+=e["merge_sz"]; tot_yessell+=e["yes_sell_sh"]
    tot_redeem+=e["redeem_sz"]; tot_convsets+=e["conv_sets"]
    tot_nobuy+=e["no_buy_u"]; tot_yesbuy+=e["yes_buy_u"]
    if e["merge_sz"]+e["yes_sell_sh"]+e["redeem_sz"] >= minted_lb*0.85: fully_cov+=1
print(f"  YES 买入总额 ${tot_yesbuy:,.0f} vs NO 买入总额 ${tot_nobuy:,.0f}  → 买YES占比 {100*tot_yesbuy/(tot_nobuy+1):.2f}%")
print(f"  转换铸出YES(下界)合计: {tot_minted_lb:,.0f}")
print(f"  消解渠道: MERGE {tot_merge:,.0f} + 卖YES {tot_yessell:,.0f} + REDEEM {tot_redeem:,.0f} = {tot_merge+tot_yessell+tot_redeem:,.0f}")
cov=100*(tot_merge+tot_yessell+tot_redeem)/(tot_minted_lb+1)
print(f"  ★ 整体覆盖率 {cov:.0f}%  → {'merge等动作基本能吃掉转换铸出的YES = 倾向于平/真套利' if cov>80 else '存在未对冲缺口'}")
print(f"  事件级: merge+卖+赎回 能覆盖≥85%铸出YES 的事件: {fully_cov}/{len(negrisk)} ({100*fully_cov/len(negrisk):.0f}%)")
