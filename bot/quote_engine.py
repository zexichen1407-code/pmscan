"""
Phase 0 — 报价引擎（从 0xp3nny 逆向，见 audit/QUOTING_ALGO.md）

纯函数、确定性、不联网。两个职责:
  1) detect_arb : 给两条腿的最优卖价,判断当下是否存在"两边都买凑成 $1"的套利
  2) quote      : 给实时中价 + 当前库存,产出双边 maker 买单(价+量)

所有数字含义见 QUOTING_ALGO.md 的参数表。EDGE_FLOOR 必须含手续费,否则名义赚、实际亏。
"""
from dataclasses import dataclass


@dataclass
class Params:
    C_TARGET: float = 0.97       # 两腿买价之和上限(目标成对成本)
    EDGE_FLOOR: float = 0.015    # 凑成对后最起码要留的边际 —— ⚠️ 必须 >= 实际手续费
    FEE: float = 0.0             # 每股 taker 手续费(吃单才付;按品类设)
    PEG_OFFSET: float = 0.008    # 买价贴在中价下方多少
    CLIP: float = 25.0           # 每笔下单股数
    MAX_OVERHANG: float = 1500.0 # 单边未配对持仓上限(股) —— 最大亏损来源,设小
    PRICE_FLOOR: float = 0.03    # 某腿价格低于此(濒临归零)就不挂
    PRICE_CEIL: float = 0.98     # 某腿价格高于此(濒临到 1)就不挂


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def detect_arb(ask_yes: float, ask_no: float, p: Params) -> dict:
    """当下能否两边吃单、凑成保证值 $1 的一对。edge = 1 - 成本 - 两腿吃单费。"""
    cost = ask_yes + ask_no
    edge = 1.0 - cost - 2 * p.FEE
    return {"cost": round(cost, 4), "edge": round(edge, 4), "arb": edge > p.EDGE_FLOOR}


def quote(mid_yes: float, mid_no: float, inv_yes: float, inv_no: float,
          p: Params, time_to_resolution=None, vol_bucket: int = 0) -> dict:
    """产出双边被动买单。返回 action=quote/pull,两腿价+量,以及可合并的对子数。"""
    # (A) 临近结算: 撤单, 只合并
    if time_to_resolution is not None and time_to_resolution < 180:
        return {"action": "pull", "merge_pairs": min(inv_yes, inv_no)}

    # (B) 波动越大, 目标成对成本压越低(要求更宽边际)
    C = p.C_TARGET - 0.005 * vol_bucket

    # (C) 基准: 两腿买价贴在各自中价下方
    bid_yes = mid_yes - p.PEG_OFFSET
    bid_no = mid_no - p.PEG_OFFSET

    # (D) 钉住两腿之和 <= C —— 绝对等分: 超出部分两腿各让一半。
    #     这样两腿离各自中价的距离始终相等(d_yes == d_no), 二元两腿绝对波动又相等,
    #     => 两腿等概率成交、不偏袒任何一边(不再优先便宜腿)。
    s = bid_yes + bid_no
    if s > C:
        excess = s - C
        bid_yes -= excess / 2
        bid_no -= excess / 2

    size_yes = p.CLIP
    size_no = p.CLIP

    # (G) 边际纪律: 凑成对后边际 < EDGE_FLOOR 就两腿都不挂(只做有真实边际的对子,
    #     对称对待两腿)。配不上的残腿就让它光着到结算, 不主动追(光腿长期 EV≈0)。
    if (1.0 - (bid_yes + bid_no)) < p.EDGE_FLOOR:
        size_yes = 0.0
        size_no = 0.0

    # (H) 残腿上限: 单边未配对到顶就停挂该(多头)腿, 只挂对侧来配对
    overhang = abs(inv_yes - inv_no)
    if overhang >= p.MAX_OVERHANG:
        if inv_yes > inv_no:
            size_yes = 0.0
        else:
            size_no = 0.0

    # (I) 价格地板/天花板: 濒临归零或到 1 的腿不挂
    if mid_yes < p.PRICE_FLOOR or mid_yes > p.PRICE_CEIL:
        size_yes = 0.0
    if mid_no < p.PRICE_FLOOR or mid_no > p.PRICE_CEIL:
        size_no = 0.0

    return {
        "action": "quote",
        "bid_yes": round(clamp01(bid_yes), 4), "size_yes": size_yes,
        "bid_no": round(clamp01(bid_no), 4), "size_no": size_no,
        "pair_cost": round(clamp01(bid_yes) + clamp01(bid_no), 4),
        "merge_pairs": min(inv_yes, inv_no),
        "C": round(C, 4),
    }


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    p = Params()
    print("== 自测 ==")

    # 1) 套利探测: 两边卖价加起来 0.93 < 1 -> 有套利
    print("detect_arb(0.78, 0.15):", detect_arb(0.78, 0.15, p))   # edge ~0.07 -> arb True
    print("detect_arb(0.80, 0.21):", detect_arb(0.80, 0.21, p))   # cost 1.01 -> arb False

    # 2) 平静盘 mid 0.79 / 0.21 -> 两腿都挂, 和 <= C
    q = quote(0.79, 0.21, inv_yes=0, inv_no=0, p=p)
    print("quote calm:", q)
    assert q["pair_cost"] <= p.C_TARGET + 1e-9, "pair cost must be <= C"

    # 3) 残腿超上限(已多扛 NO) -> 停挂 NO, 只挂 YES 来配对
    q2 = quote(0.50, 0.50, inv_yes=0, inv_no=2000, p=p)
    print("quote overhang NO:", q2)
    assert q2["size_no"] == 0.0 and q2["size_yes"] > 0, "should stop adding the over-filled NO leg"

    # 4) 临近结算 -> 撤单只合并
    q3 = quote(0.6, 0.4, inv_yes=100, inv_no=80, p=p, time_to_resolution=60)
    print("quote near-settle:", q3)
    assert q3["action"] == "pull" and q3["merge_pairs"] == 80

    # 5) 某腿濒临归零 -> 不挂该腿
    q4 = quote(0.985, 0.015, inv_yes=0, inv_no=0, p=p)
    print("quote extreme price:", q4)
    assert q4["size_no"] == 0.0, "should not quote a near-zero leg"

    print("\nALL SELF-TESTS PASSED ✅")
