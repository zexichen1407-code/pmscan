# -*- coding: utf-8 -*-
"""
negrisk_quote.py — neg-risk 多选项盘 maker 报价引擎(纯计算, 不下单)

给一个事件的 N 条腿(每腿有 YES bid/ask + tick + 返佣参数), 算每条腿该挂的 NO 买价+量。

定价逻辑(来自逆向):
  - 每腿 NO 中价 = 1 − YES中价。我们在 NO 中价"附近偏下"挂被动买单:
        no_bid = round_tick(1 − yes_mid − margin)
    margin=0 时挂在 NO 中价(=改善 NO 最优买价, 进价差内, 易成交且吃返佣);
    margin>0 往下挂 = 每腿多赚 margin 但成交概率降。
  - 全篮每套毛边际 = (N−1) − Σ(no_bid) = (Σ YES中价 − 1) + N×margin = overround + 总margin。
  - 返佣约束: 挂价要落在距中价 rew_max_spread 内、量 ≥ rew_min_size, 才拿 maker 返佣。
    所以 margin 上限 = rew_max_spread(再下就丢返佣)。
  - 冷门尾腿(YES 极低 = NO≈1): 没人卖 NO, 几乎挂不到; 标记 tail, 小量象征挂或跳过(由循环决定)。

输出每腿: {no_token, no_bid, size, in_reward_band, is_tail, yes_mid}
以及全篮: gross_edge_per_set, edge_ok。
"""
from dataclasses import dataclass

@dataclass
class QParams:
    clip: float = 5.0            # 每腿每单股数(Polymarket 最小约 5 股)
    margin: float = 0.0          # 每腿在 NO 中价下方再让多少(0=贴中价最大成交)
    edge_floor: float = 0.01     # 每套最小可接受毛边际(覆盖 fee+gas+方差)
    fee_buffer: float = 0.0      # 预留 taker/结算费(maker 通常 0, 占位)
    tail_yes: float = 0.03       # YES ≤ 此值的腿算冷门尾腿(NO≥0.97, 难挂到)
    default_tick: float = 0.001

def _round_tick(p, tick):
    tick = tick or 0.001
    return round(round(p / tick) * tick, 6)

def quote_event(legs, p: QParams = QParams()):
    """legs: list of dict, 需有 yes_bid, yes_ask, no_token; 可选 tick, rew_max_spread, rew_min_size"""
    out = []
    sum_bid = 0.0
    n_quotable = 0
    for lg in legs:
        yb = lg.get("yes_bid"); ya = lg.get("yes_ask")
        if yb is None or ya is None or not (0 < ya <= 1) or ya < yb:
            continue
        tick = lg.get("tick") or p.default_tick
        rmax = lg.get("rew_max_spread")
        rmin = lg.get("rew_min_size") or 0
        yes_mid = (yb + ya) / 2.0
        no_mid = 1.0 - yes_mid
        is_tail = yes_mid <= p.tail_yes
        # 期望让利 margin, 但不超过返佣带(否则丢返佣)
        eff_margin = p.margin
        if rmax:
            eff_margin = min(eff_margin, max(0.0, rmax - tick))
        no_bid = _round_tick(no_mid - eff_margin, tick)
        # 价必须 >0、< 1
        no_bid = min(max(no_bid, tick), 1 - tick)
        size = p.clip                      # 用我们设定的小 clip(不强行抬到返佣门槛)
        # 是否落在返佣带(距 NO 中价 <= rew_max_spread)且量够门槛 -> 才真拿返佣
        in_band = (rmax is None) or (abs(no_mid - no_bid) <= rmax + 1e-9)
        reward_eligible = bool(in_band and rmin and size >= rmin)
        out.append({
            "no_token": lg.get("no_token"), "q": lg.get("q"),
            "yes_mid": round(yes_mid, 4), "no_mid": round(no_mid, 4),
            "no_bid": no_bid, "size": size, "rew_min_size": rmin,
            "in_reward_band": bool(in_band), "reward_eligible": reward_eligible,
            "is_tail": bool(is_tail),
        })
        sum_bid += no_bid
        n_quotable += 1

    N = n_quotable
    # 全篮: 集齐 N 腿各 1 套, 转换返还 (N−1); 毛边际 = (N−1) − Σ(no_bid)
    gross_edge = (N - 1) - sum_bid if N >= 2 else None
    edge_ok = (gross_edge is not None) and (gross_edge > p.edge_floor + p.fee_buffer)
    return {
        "N": N,
        "sum_no_bid": round(sum_bid, 4),
        "gross_edge_per_set": round(gross_edge, 4) if gross_edge is not None else None,
        "edge_per_leg": round(gross_edge / N, 5) if (gross_edge is not None and N) else None,
        "edge_ok": edge_ok,
        "legs": out,
    }

# --- 自测 ---
if __name__ == "__main__":
    import sys, io, json, os
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    # 用扫盘器产出的候选自测
    CAND = os.path.join(os.path.dirname(__file__), "negrisk_candidates.json")
    if os.path.exists(CAND):
        cands = json.load(open(CAND, encoding="utf-8"))
        # 取第一个中价边际为正的
        c = next((x for x in cands if x.get("maker_edge_per_set", 0) > 0), cands[0])
        print(f"自测事件: {c['title']}  N={c['N_tradeable']}  扫盘中价边际={c['maker_edge_per_set']*100:.1f}¢/套")
        for mg in (0.0, 0.005, 0.01):
            r = quote_event(c["legs"], QParams(clip=5, margin=mg))
            print(f"\n  margin={mg*100:.1f}¢/腿: 全篮毛边际 {r['gross_edge_per_set']*100:.1f}¢/套 "
                  f"(每腿{ (r['edge_per_leg'] or 0)*100:.2f}¢)  edge_ok={r['edge_ok']}  N={r['N']}")
            tails = sum(1 for l in r["legs"] if l["is_tail"])
            oob = sum(1 for l in r["legs"] if not l["in_reward_band"])
            print(f"    冷门尾腿 {tails}/{r['N']}  脱离返佣带 {oob}/{r['N']}")
            for l in r["legs"][:4]:
                print(f"      {str(l['q'])[:30]:<30} NO中价{l['no_mid']:.3f} 挂NO买价{l['no_bid']:.3f} x{l['size']:.0f}"
                      f" {'[尾]' if l['is_tail'] else ''}")
    else:
        print("先跑 negrisk_scan.py 生成 negrisk_candidates.json")
