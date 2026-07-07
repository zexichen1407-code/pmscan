"""
Phase 0 回测 — 用 0xp3nny 的真实逐笔成交,验证两件事(Phase 0 闸门):
  A) 信号识别: 我们的引擎能不能在历史上认出他认的套利时刻(p+q < 1)
  B) 账务重算: 用他的真实成交价,能不能重算出他锁定的价差(应 ≈ $890)
  C) 成交模拟(光示意): 一个粗糙的 maker 接单模拟,看引擎跑起来什么样
     —— ⚠️ C 假设我们能接到单,真实成交率是 Phase 1 才能测的,这里是乐观上界

用法: python backtest.py [market_fills.json]
"""
import json, sys, statistics
sys.path.insert(0, r"C:\Users\zexi\pmscan\bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from quote_engine import Params, quote, detect_arb

path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\zexi\pmscan\bot\tennis_fills.json"
d = json.load(open(path, encoding="utf-8"))
trades = d["trades"]
merges = d["merges"]

# 两条腿: 按总买量定 YES/NO(贵的=YES 这里只是命名,引擎对称)
legs = list(d["outcomes"].keys())
# 用 size 加权均价区分贵/便宜腿
buy = {}
for tr in trades:
    if tr["side"] == "BUY":
        a = buy.setdefault(tr["outcome"], {"sz": 0.0, "usd": 0.0})
        a["sz"] += tr["size"]; a["usd"] += tr["size"] * tr["price"]
vwap = {o: (a["usd"] / a["sz"] if a["sz"] else 0) for o, a in buy.items()}
LEG_HI = max(vwap, key=vwap.get)   # 贵腿
LEG_LO = min(vwap, key=vwap.get)   # 便宜腿
p = Params()

print(f"市场: {d['slug']}  ({d['n_trades']} 笔成交, {d['n_merges']} 次合并)")
print(f"两条腿: 贵腿={LEG_HI} vwap={vwap[LEG_HI]:.4f} | 便宜腿={LEG_LO} vwap={vwap[LEG_LO]:.4f}")

# ---------- A) 信号识别 ----------
last = {LEG_HI: None, LEG_LO: None}
pqs = []
arb_steps = sub1_steps = total = 0
for tr in trades:
    last[tr["outcome"]] = tr["price"]
    if last[LEG_HI] is not None and last[LEG_LO] is not None:
        pq = last[LEG_HI] + last[LEG_LO]
        pqs.append(pq); total += 1
        if pq < 1.0: sub1_steps += 1
        if pq < p.C_TARGET: arb_steps += 1
print("\n== A) 信号识别 ==")
print(f"  有效观测步: {total}")
print(f"  成对成本 p+q: 最低 {min(pqs):.4f} / 中位 {statistics.median(pqs):.4f} / 最高 {max(pqs):.4f}")
print(f"  p+q < 1.00 (有套利空间) 的步占比 : {100*sub1_steps/total:.0f}%")
print(f"  p+q < {p.C_TARGET} (够我们目标边际) 的步占比: {100*arb_steps/total:.0f}%")
gateA = arb_steps / total > 0.5
print(f"  闸门A(过半数时间存在可做套利): {'PASS' if gateA else 'FAIL'}")

# ---------- B) 账务重算 ----------
merge_size = sum(m["size"] for m in merges)
pair_cost = vwap[LEG_HI] + vwap[LEG_LO]
locked = (1.0 - pair_cost) * merge_size
print("\n== B) 账务重算 ==")
print(f"  合并对数: {merge_size:,.2f}")
print(f"  每对成本 p+q = {pair_cost:.5f}  ->  每对锁定 {1-pair_cost:.5f}")
print(f"  重算锁定价差 = (1-{pair_cost:.5f}) x {merge_size:,.0f} = ${locked:,.2f}")
print(f"  落盘审计值              = $890.44")
gateB = abs(locked - 890.44) < 30
print(f"  闸门B(误差 < $30): {'PASS' if gateB else 'FAIL'}  (差 ${abs(locked-890.44):.2f})")

# ---------- C) 粗糙成交模拟(乐观上界,仅示意) ----------
CAPTURE = 0.5   # 假设我们能接到当时成交量的一半(乐观!Phase 1 用真实成交率替换)
inv = {LEG_HI: 0.0, LEG_LO: 0.0}
cost = {LEG_HI: 0.0, LEG_LO: 0.0}
mid = {LEG_HI: None, LEG_LO: None}
sim_pairs = 0.0
sim_locked = 0.0
for tr in trades:
    mid[tr["outcome"]] = tr["price"]
    if mid[LEG_HI] is None or mid[LEG_LO] is None:
        continue
    q = quote(mid[LEG_HI], mid[LEG_LO], inv[LEG_HI], inv[LEG_LO], p)
    if q["action"] != "quote":
        continue
    # 把当前这笔成交当作"市场上有人以 tr.price 出货";若我们的对应腿买价 >= tr.price,我们接到一部分
    leg = tr["outcome"]
    bid = q["bid_yes"] if leg == LEG_HI else q["bid_no"]
    size_q = q["size_yes"] if leg == LEG_HI else q["size_no"]
    if size_q > 0 and bid >= tr["price"]:
        fill = min(size_q, tr["size"] * CAPTURE)
        inv[leg] += fill
        cost[leg] += fill * tr["price"]   # 以当时成交价成交(我们其实出价更低,这里保守用成交价)
    # 攒够就合并(批量 170)
    matched = min(inv[LEG_HI], inv[LEG_LO])
    if matched >= 170:
        # 按比例摊销成本
        cph = cost[LEG_HI] / inv[LEG_HI] if inv[LEG_HI] else 0
        cpl = cost[LEG_LO] / inv[LEG_LO] if inv[LEG_LO] else 0
        sim_locked += matched * (1.0 - cph - cpl)
        sim_pairs += matched
        cost[LEG_HI] -= matched * cph; cost[LEG_LO] -= matched * cpl
        inv[LEG_HI] -= matched; inv[LEG_LO] -= matched
print("\n== C) 粗糙成交模拟(仅示意, 不是 P&L 结论) ==")
print(f"  模拟凑成对数: {sim_pairs:,.0f}   模拟锁定: ${sim_locked:,.2f}")
print(f"  残留未配对: 贵腿 {LEG_HI} {inv[LEG_HI]:,.0f} / 便宜腿 {LEG_LO} {inv[LEG_LO]:,.0f}")
print("  解读: 单价格带模型接不到贵腿——贵腿(Zverev)价格一路涨跑,我们贴低的买价够不着,")
print("        只接得到一路跌的便宜腿(Cobolli),于是堆成单边光腿、凑不成对。")
print("  ⚠️ 这不是说策略亏钱,而是说明两点:")
print("     1) 这个粗糙模型无法模拟真实双边成交(需要真实盘口+盘中来回震荡才能两腿都接到);")
print("     2) 它恰好预演了【光腿堆积】风险——和他真实结尾也净多便宜腿(+3193 Cobolli)同向。")
print("     真实成交率必须靠 Phase 1 观察模式(挂真实盘口、记录会不会成交)来测,这是核心未知数。")

print("\n==== Phase 0 闸门 ====")
print(f"  A 信号识别: {'PASS' if gateA else 'FAIL'}")
print(f"  B 账务重算: {'PASS' if gateB else 'FAIL'}")
print(f"  => {'Phase 0 通过 ✅ 引擎能认出他的套利、账也对得上' if (gateA and gateB) else '需排查'}")
