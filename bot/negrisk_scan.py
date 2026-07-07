# -*- coding: utf-8 -*-
"""
negrisk_scan.py — neg-risk 多选项盘 做市机会扫描器 (只读, 不下单)

逻辑(来自对 0xp3nny 的逆向):
  在一个 N 选项 neg-risk 事件里, 各选项的 YES 价应 Σ=1。
  Σ(各腿 YES 最优卖价) − 1 = "超额抽水"(overround) = 我们作为 maker
  在每条腿 NO 上贴着最优买价常驻挂单、若全部成交后每套能锁的毛边际。
  推导: 在每条腿 NO 贴盘口挂买价 p_i = 1 − YES_ask_i, Σp_i = N − Σ(YES_ask);
        集齐转换返还 (N−1); 每套毛边际 = (N−1) − Σp_i = Σ(YES_ask) − 1。
  Σ(YES 最优买价) − 1 > 0  => 当下就有 taker 套利(直接吃单, 罕见)。

筛选目标: overround 为正且够厚、有成交流量(能被吃到)、有 maker 返佣的盘。
注意: 用 gamma 的每腿 bestBid/bestAsk(=YES 侧)做快速排序; 实盘报价再用 /book 校真。
"""
import sys, os, io, json, urllib.request, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

GAMMA = "https://gamma-api.polymarket.com"
MIN_VOL24 = float(os.environ.get("MIN_VOL24", "5000"))   # 至少有点流量才可能被吃到
PAGES = int(os.environ.get("PAGES", "6"))                 # gamma 翻几页(每页100)
OUT = os.path.join(os.path.dirname(__file__), "negrisk_candidates.json")

def get(url, tries=3):
    last=None
    for _ in range(tries):
        try:
            req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last=e; time.sleep(0.4)
    raise last

def jload(x, d):
    try: return json.loads(x) if isinstance(x,str) else (x if x is not None else d)
    except: return d

def fnum(x):
    try:
        v=float(x); return v
    except: return None

def scan_event(e):
    """返回该事件的做市机会指标, 不合格返回 None"""
    mkts = e.get("markets") or []
    legs=[]
    sum_yes_ask=0.0; sum_yes_bid=0.0; sum_yes_mid=0.0
    spreads=[]
    n_accept=0; n_total=len(mkts)
    rewards_legs=0
    for m in mkts:
        outs = jload(m.get("outcomes"), [])
        toks = jload(m.get("clobTokenIds"), [])
        if len(outs)!=2 or len(toks)!=2: continue
        # 确认 outcome[1] 是 No (绝大多数如此); 否则按名字定位
        no_idx = 1 if str(outs[1]).lower()=="no" else (0 if str(outs[0]).lower()=="no" else 1)
        no_tok = toks[no_idx]
        ya = fnum(m.get("bestAsk"))   # gamma bestBid/bestAsk = YES(outcome0)侧; 若 no_idx==0 需翻转
        yb = fnum(m.get("bestBid"))
        if no_idx==0:  # 罕见: outcomes 顺序反了, gamma价是No侧 -> 转成Yes侧
            ya, yb = (1-yb if yb is not None else None), (1-ya if ya is not None else None)
        accepting = bool(m.get("acceptingOrders")) and bool(m.get("enableOrderBook"))
        # maker 返佣
        rmin = fnum(m.get("rewardsMinSize")); rmax = fnum(m.get("rewardsMaxSpread"))
        has_reward = (rmin and rmin>0) or (rmax and rmax>0)
        if accepting and ya is not None and yb is not None and 0<ya<1 and ya>=yb:
            n_accept+=1
            mid=(ya+yb)/2.0; sp=ya-yb
            sum_yes_ask+=ya; sum_yes_bid+=yb; sum_yes_mid+=mid; spreads.append(sp)
            if has_reward: rewards_legs+=1
            legs.append({
                "q": str(m.get("question") or m.get("groupItemTitle") or "")[:40],
                "no_token": no_tok, "cond": m.get("conditionId"),
                "yes_ask": ya, "yes_bid": yb, "yes_mid": round(mid,4), "spread": round(sp,4),
                "no_bid_at_mid": round(1-mid,4),       # 我们挂在中价的 NO 买价目标
                "tick": fnum(m.get("orderPriceMinTickSize")),
                "min_size": fnum(m.get("orderMinSize")),
                "rew_min_size": rmin, "rew_max_spread": rmax,
                "vol24": fnum(m.get("volume24hr")) or 0,
            })
    if n_accept<3: return None
    edge_mid  = sum_yes_mid - 1.0          # ★真实 maker 边际: 各腿挂中价, 每套毛利
    edge_ask  = sum_yes_ask - 1.0          # 上界(贴盘口被动挂, 易被陈旧宽价差夸大)
    taker_arb = sum_yes_bid - 1.0          # >0 = 当下直接吃单就套利
    avg_sp = sum(spreads)/len(spreads) if spreads else None
    max_sp = max(spreads) if spreads else None
    return {
        "title": e.get("title"), "slug": e.get("slug"),
        "negRiskMarketID": e.get("negRiskMarketID"),
        "N_tradeable": n_accept, "N_total": n_total,
        "vol24": fnum(e.get("volume24hr")) or 0,
        "liquidity": fnum(e.get("liquidity")) or 0,
        "sum_yes_mid": round(sum_yes_mid,4), "sum_yes_ask": round(sum_yes_ask,4), "sum_yes_bid": round(sum_yes_bid,4),
        "maker_edge_per_set": round(edge_mid,4),      # ★ 真实(中价) Σ(YES mid)-1
        "edge_ask_upper": round(edge_ask,4),
        "taker_arb_per_set": round(taker_arb,4),
        "edge_per_leg": round(edge_mid/n_accept,5) if n_accept else 0,
        "avg_spread": round(avg_sp,4) if avg_sp is not None else None,
        "max_spread": round(max_sp,4) if max_sp is not None else None,
        "reward_legs": rewards_legs,
        "legs": legs,
    }

def main():
    print(f"扫描 neg-risk 做市机会  (min vol24=${MIN_VOL24:,.0f}, {PAGES}页)\n")
    seen=set(); results=[]
    for pg in range(PAGES):
        off=pg*100
        try:
            evs=get(f"{GAMMA}/events?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
        except Exception as ex:
            print("拉取失败:", ex); break
        if not evs: break
        for e in evs:
            if e.get("id") in seen: continue
            seen.add(e.get("id"))
            if not e.get("negRisk"): continue
            if (fnum(e.get("volume24hr")) or 0) < MIN_VOL24: continue
            r=scan_event(e)
            if r: results.append(r)
        if len(evs)<100: break

    # 排序: 真实(中价)边际为正 且 价差不太宽(报价可信) 的, 按 每腿边际 × log(量)
    import math
    def real(r):  # 真实可做: 中价边际>0 且 平均价差不离谱(否则是陈旧宽价差幻觉)
        return r["maker_edge_per_set"]>0 and (r["avg_spread"] or 1) <= 0.06
    def score(r):
        if not real(r): return -1
        return r["edge_per_leg"]*math.log10(max(10,r["vol24"]))
    results.sort(key=score, reverse=True)

    pos=[r for r in results if r["maker_edge_per_set"]>0]
    realn=[r for r in results if real(r)]
    print(f"扫到 {len(results)} 个 neg-risk 事件; 中价边际>0 的 {len(pos)} 个; "
          f"其中价差够紧(可信真实边际)的 {len(realn)} 个\n")
    print(f"{'标题':<32}{'N':>4}{'量24h':>10}{'中价边际':>9}{'每腿':>7}{'均价差':>7}{'返佣':>6}{'taker':>7}")
    for r in realn[:25]:
        flag = "★" if r["taker_arb_per_set"]>0 else " "
        print(f"{flag}{str(r['title'])[:31]:<31}{r['N_tradeable']:>4}{r['vol24']:>10,.0f}"
              f"{r['maker_edge_per_set']*100:>8.1f}¢{r['edge_per_leg']*100:>6.2f}¢"
              f"{(r['avg_spread'] or 0)*100:>6.1f}¢{r['reward_legs']:>3}/{r['N_tradeable']:<2}"
              f"{r['taker_arb_per_set']*100:>6.1f}¢")

    json.dump(results, open(OUT,"w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n详情(每腿 NO token/中价挂价)写入 {OUT}")
    print("说明: 中价边际=Σ(YES中价)−1=各腿挂中价做市的每套真实毛利(已剔除陈旧宽价差幻觉)。")
    print("      均价差大=报价不可信(薄腿没人挂),已用 avg_spread<=6¢ 过滤。★=当下taker直接套利。")
    print("      下一步: 报价引擎对选中事件按每腿 no_bid_at_mid 挂 maker 单 + 纸面跑成交率。")

if __name__=="__main__":
    main()
