"""
Phase 1 骨架 — 带状态的实时做市循环(事件驱动, 不接真盘口, 先用历史带回放)

在 quote_engine(无状态决策) 之上加上"活"的部分:
  - 挂单状态: 记住当前两腿各挂在什么价
  - 重报/撤换单: 每 REQUOTE_SEC 秒 或 中价漂移 > DRIFT_TRIG 就撤旧挂新
  - 成交跟踪: 维护两腿库存 + 成本
  - ★残腿时间闸 + 追腿: 一腿成交后, 另一腿过了 WAIT_SECS 还没配上,
       就把缺腿出价抬向中价 —— 但硬顶在"配成对成本 <= CHASE_MAX_PAIRCOST"(默认 1.0),
       宁可打平绝不倒贴(这是他亏损最大的来源, 写成硬规则)
  - 批量合并: 攒够 MERGE_BATCH 对就合并, 落袋 (1-p-q) 价差
  - 临盘撤单: 结算前 PULL_TTR 秒撤所有单, 只合并

接口设计成事件驱动 on_tick(t, leg, price, size), 以后接 CLOB websocket 喂同样的 tick 即可。
本文件用历史成交带回放来验证这套机器跑得通。

⚠️ 成交用的仍是粗糙模型(我们的挂价 >= 当时成交价就算接到一部分)。真实成交率要 Phase 1
   接真盘口实测; 这里验证的是【循环/撤换单/追腿/合并机器】本身跑得对, 不是 P&L 结论。
用法: python live_loop.py [fills.json]
"""
import json, sys, statistics
sys.path.insert(0, r"C:\Users\zexi\pmscan\bot")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from quote_engine import Params, quote


class MarketMaker:
    def __init__(self, leg_hi, leg_lo, params: Params,
                 REQUOTE_SEC=12, DRIFT_TRIG=0.005, WAIT_SECS=150,
                 CHASE_MAX_PAIRCOST=1.0, MERGE_BATCH=5, PULL_TTR=180,
                 CAPTURE=0.5, end_t=None):
        self.HI, self.LO = leg_hi, leg_lo          # 贵腿 / 便宜腿(名称)
        self.p = params
        self.REQUOTE_SEC = REQUOTE_SEC             # 多久没动就重报
        self.DRIFT_TRIG = DRIFT_TRIG               # 中价漂移多少立刻重报
        self.WAIT_SECS = WAIT_SECS                 # 残腿等多久触发追腿
        self.CHASE_MAX_PAIRCOST = CHASE_MAX_PAIRCOST  # 追腿硬顶: 配对成本不得超过此
        self.MERGE_BATCH = MERGE_BATCH
        self.PULL_TTR = PULL_TTR
        self.CAPTURE = CAPTURE                     # 粗糙成交: 接到当时成交量的比例
        self.end_t = end_t                         # 结算近似时间(回放=最后一笔)

        self.mid = {leg_hi: None, leg_lo: None}
        self.inv = {leg_hi: 0.0, leg_lo: 0.0}
        self.cost = {leg_hi: 0.0, leg_lo: 0.0}
        self.rest = {leg_hi: None, leg_lo: None}   # (price, size) 当前挂单
        self.quoted_mid = {leg_hi: None, leg_lo: None}
        self.last_quote_t = -1e18
        self.resid_start_t = None                  # 当前残腿从何时开始
        self.realized = 0.0                         # 已合并落袋价差
        self.pairs = 0.0
        self.stopped = False
        self.c = dict(requote=0, fill=0, chase=0, merge=0, cancel=0, pull=0)
        self.log = []

    def _ttr(self, t):
        return (self.end_t - t) if self.end_t is not None else None

    def _residual(self):
        return abs(self.inv[self.HI] - self.inv[self.LO])

    def _deficient(self):
        # 库存少的那条腿 = 需要补来配对的腿(通常是贵腿)
        return self.HI if self.inv[self.HI] < self.inv[self.LO] else self.LO

    def _avg_cost(self, leg):
        return self.cost[leg] / self.inv[leg] if self.inv[leg] else 0.0

    def _place(self, t, bid_hi, sz_hi, bid_lo, sz_lo, why):
        # 撤旧 + 挂新
        if self.rest[self.HI] or self.rest[self.LO]:
            self.c["cancel"] += 1
        self.rest[self.HI] = (bid_hi, sz_hi) if sz_hi > 0 else None
        self.rest[self.LO] = (bid_lo, sz_lo) if sz_lo > 0 else None
        self.quoted_mid = dict(self.mid)
        self.last_quote_t = t
        self.c["requote"] += 1
        self.log.append((t, why, f"HI {bid_hi:.3f}x{sz_hi:.0f} | LO {bid_lo:.3f}x{sz_lo:.0f}"))

    def _maybe_requote(self, t):
        if self.mid[self.HI] is None or self.mid[self.LO] is None:
            return
        drift = (self.quoted_mid[self.HI] is None or
                 abs(self.mid[self.HI] - self.quoted_mid[self.HI]) > self.DRIFT_TRIG or
                 abs(self.mid[self.LO] - self.quoted_mid[self.LO]) > self.DRIFT_TRIG)
        if not (t - self.last_quote_t >= self.REQUOTE_SEC or drift):
            return

        q = quote(self.mid[self.HI], self.mid[self.LO], self.inv[self.HI], self.inv[self.LO],
                  self.p, time_to_resolution=self._ttr(t))
        if q["action"] == "pull":
            if self.rest[self.HI] or self.rest[self.LO]:
                self.rest = {self.HI: None, self.LO: None}
                self.c["pull"] += 1
                self.log.append((t, "PULL", "临盘撤单, 只合并"))
            return
        bid_hi, sz_hi = q["bid_yes"], q["size_yes"]
        bid_lo, sz_lo = q["bid_no"], q["size_no"]
        why = "requote"

        # ★ 残腿时间闸 + 追腿
        if self.resid_start_t is not None and (t - self.resid_start_t) > self.WAIT_SECS and self._residual() > 0:
            dleg = self._deficient()
            other = self.LO if dleg == self.HI else self.HI
            cap = self.CHASE_MAX_PAIRCOST - self._avg_cost(other)   # 配对成本不超过硬顶
            chase = min(self.mid[dleg], cap)                        # 抬向中价, 但绝不越过硬顶
            if dleg == self.HI and chase > bid_hi:
                bid_hi, sz_hi = round(chase, 4), self.p.CLIP; why = "chase HI"; self.c["chase"] += 1
            elif dleg == self.LO and chase > bid_lo:
                bid_lo, sz_lo = round(chase, 4), self.p.CLIP; why = "chase LO"; self.c["chase"] += 1

        self._place(t, bid_hi, sz_hi, bid_lo, sz_lo, why)

    def _try_fill(self, t, leg, price, size):
        # 粗糙成交: 当前这笔以 price 出货; 若我们该腿挂价 >= price, 接到一部分
        r = self.rest[leg]
        if not r:
            return
        bid, qsz = r
        if qsz <= 0 or bid < price:
            return
        fill = min(qsz, size * self.CAPTURE)
        if fill <= 0:
            return
        before = self._residual()
        self.inv[leg] += fill
        self.cost[leg] += fill * price
        self.c["fill"] += 1
        after = self._residual()
        # 残腿计时: 失衡出现就开始计时; 回到平衡就清零
        if after > 0 and self.resid_start_t is None:
            self.resid_start_t = t
        if after == 0:
            self.resid_start_t = None
        self.log.append((t, "FILL", f"{leg[:10]} {fill:.0f}@{price:.3f} -> 残腿 {after:.0f}"))

    def _try_merge(self, t):
        matched = min(self.inv[self.HI], self.inv[self.LO])
        if matched < self.MERGE_BATCH:
            return
        cph, cpl = self._avg_cost(self.HI), self._avg_cost(self.LO)
        self.realized += matched * (1.0 - cph - cpl)
        self.pairs += matched
        self.cost[self.HI] -= matched * cph
        self.cost[self.LO] -= matched * cpl
        self.inv[self.HI] -= matched
        self.inv[self.LO] -= matched
        self.c["merge"] += 1
        if self._residual() == 0:
            self.resid_start_t = None
        self.log.append((t, "MERGE", f"{matched:.0f} 对, 每对成本 {cph+cpl:.3f}, 落袋 {matched*(1-cph-cpl):+.2f}"))

    def on_tick(self, t, leg, price, size):
        """回放入口: 历史里每个 tick 既是价格更新也是一笔成交。"""
        self.on_book(t, leg, price)
        if not self.stopped:
            self._try_fill(t, leg, price, size)
            self._try_merge(t)

    def on_book(self, t, leg, mid):
        """实时盘口更新: 设中价, 重报, 合并(不产生成交)。"""
        if self.stopped:
            return
        self.mid[leg] = mid
        if self._ttr(t) is not None and self._ttr(t) < self.PULL_TTR:
            if self.rest[self.HI] or self.rest[self.LO]:
                self.rest = {self.HI: None, self.LO: None}
                self.c["pull"] += 1
            self._try_merge(t)
            return
        self._maybe_requote(t)
        self._try_merge(t)

    def on_trade(self, t, leg, price, size):
        """实时真实成交: 检验我们的挂单会不会被打中(成交)。"""
        if self.stopped:
            return
        self._try_fill(t, leg, price, size)
        self._try_merge(t)

    def hit_test(self, leg, price):
        """观察用: 这笔真实成交价是否会打中我们当前在该腿的挂单(纯信号, 不改状态)。"""
        r = self.rest.get(leg)
        return bool(r) and r[1] > 0 and r[0] >= price


def run(path):
    d = json.load(open(path, encoding="utf-8"))
    trades = d["trades"]
    # 定贵/便宜腿
    buy = {}
    for tr in trades:
        if tr["side"] == "BUY":
            a = buy.setdefault(tr["outcome"], {"sz": 0.0, "usd": 0.0})
            a["sz"] += tr["size"]; a["usd"] += tr["size"] * tr["price"]
    vwap = {o: (a["usd"]/a["sz"] if a["sz"] else 0) for o, a in buy.items()}
    HI, LO = max(vwap, key=vwap.get), min(vwap, key=vwap.get)
    end_t = max(tr["t"] for tr in trades)

    mm = MarketMaker(HI, LO, Params(), end_t=end_t)
    print(f"市场: {d['slug']}  贵腿={HI} 便宜腿={LO}  ({len(trades)} 笔回放)")
    for tr in trades:
        mm.on_tick(tr["t"], tr["outcome"], tr["price"], tr["size"])
    # 收尾: 把还能配的对合并掉
    matched = min(mm.inv[HI], mm.inv[LO])
    if matched > 0:
        cph, cpl = mm._avg_cost(HI), mm._avg_cost(LO)
        mm.realized += matched*(1-cph-cpl); mm.pairs += matched
        mm.inv[HI] -= matched; mm.inv[LO] -= matched; mm.c["merge"] += 1

    print("\n== 循环机器计数(验证机器跑通) ==")
    print(f"  重报/撤换单: {mm.c['requote']} 次 (其中撤旧单 {mm.c['cancel']} 次)")
    print(f"  成交接到:    {mm.c['fill']} 笔")
    print(f"  ★追腿触发:   {mm.c['chase']} 次")
    print(f"  合并:        {mm.c['merge']} 次,  共 {mm.pairs:,.0f} 对")
    print(f"  临盘撤单:    {mm.c['pull']} 次")
    print(f"\n  模拟落袋价差: ${mm.realized:,.2f}  (粗糙成交模型, 非 P&L 结论)")
    print(f"  收尾残腿: 贵腿 {mm.inv[HI]:,.0f} / 便宜腿 {mm.inv[LO]:,.0f}")

    print("\n== 事件日志抽样(前 6 + 含追腿/合并各几条) ==")
    shown = 0
    for e in mm.log[:6]:
        print(f"  t+{e[0]-trades[0]['t']:>5}s [{e[1]}] {e[2]}"); shown += 1
    for tag in ("chase", "MERGE", "PULL"):
        ex = [e for e in mm.log if tag.lower() in e[1].lower()][:3]
        for e in ex:
            print(f"  t+{e[0]-trades[0]['t']:>5}s [{e[1]}] {e[2]}")
    print(f"\n  (日志共 {len(mm.log)} 条)")
    print("\n说明: 本次验证的是【撤换单/残腿时间闸/追腿硬顶/合并】这套状态机跑得对。")
    print("      成交用的是粗糙模型, 真实成交率与净边际要 Phase 1 接真盘口观察模式实测。")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\zexi\pmscan\bot\tennis_fills.json")
