# -*- coding: utf-8 -*-
import sys, io, json, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def get(url, tries=3):
    last=None
    for _ in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last=e
    raise last

print(">>> 测试网络 + gamma events 端点 ...")
try:
    evs = get("https://gamma-api.polymarket.com/events?closed=false&active=true&limit=60&order=volume24hr&ascending=false")
except Exception as e:
    print("网络/接口失败:", repr(e)); sys.exit(1)
print(f"拿到 {len(evs)} 个事件\n")

# 找 neg-risk / 多选项事件
negrisk=[]
for e in evs:
    nr = e.get("negRisk")
    mkts = e.get("markets") or []
    if nr or len(mkts)>2:
        negrisk.append(e)

print(f"其中 negRisk=true 或 >2 子市场的: {len(negrisk)}\n")
print("event 顶层可用字段:", sorted(evs[0].keys()))
if negrisk:
    e=negrisk[0]
    print("\n===== 样例多选项事件 =====")
    print("title:", e.get("title"))
    print("slug:", e.get("slug"))
    print("negRisk:", e.get("negRisk"), " negRiskMarketID:", e.get("negRiskMarketID"))
    print("volume24hr:", e.get("volume24hr"), " liquidity:", e.get("liquidity"))
    mkts=e.get("markets") or []
    print("子市场数 N =", len(mkts))
    if mkts:
        m=mkts[0]
        print("\n--- 单条子市场(腿)可用字段 ---")
        print(sorted(m.keys()))
        print("\n--- 前3条腿摘要(看 NO token / outcomes / 价格 / 盘口) ---")
        for m in mkts[:3]:
            try: outs=json.loads(m.get("outcomes") or "[]")
            except: outs=m.get("outcomes")
            try: toks=json.loads(m.get("clobTokenIds") or "[]")
            except: toks=m.get("clobTokenIds")
            try: prs=json.loads(m.get("outcomePrices") or "[]")
            except: prs=m.get("outcomePrices")
            print(f"  腿 q={str(m.get('question'))[:45]!r}")
            print(f"     outcomes={outs}  prices={prs}")
            print(f"     clobTokenIds={toks}")
            print(f"     bestBid={m.get('bestBid')} bestAsk={m.get('bestAsk')} spread={m.get('spread')} "
                  f"accepting={m.get('acceptingOrders')} negRisk={m.get('negRisk')} condId={str(m.get('conditionId'))[:14]}")
    # 统计 N 分布
    from collections import Counter
    cc=Counter(len(x.get("markets") or []) for x in negrisk)
    print("\nN(子市场数) 分布:", dict(sorted(cc.items())))
