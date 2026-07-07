# -*- coding: utf-8 -*-
"""
0xp3nny MERGE 时序分析:
1) MERGE 批量大小分布 (size = 股数 = usdcSize for merge)
2) inter-merge 间隔 (相邻两次 merge 时间差)
3) 一对可merge(两腿都到手) -> 实际被merge 滞后时间
4) 成对持仓清退占比: MERGE vs REDEEM
全量扫 raw_activity_full.json (ijson), 关键比例用派生 pkl 交叉验证.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import ijson, pickle, json, statistics
from collections import defaultdict

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

def pct(sorted_list, p):
    if not sorted_list:
        return None
    if len(sorted_list) == 1:
        return sorted_list[0]
    k = (len(sorted_list) - 1) * p
    f = int(k); c = min(f + 1, len(sorted_list) - 1)
    if f == c:
        return sorted_list[f]
    return sorted_list[f] * (c - k) + sorted_list[c] * (k - f)

def stats(vals, label):
    if not vals:
        print(f"{label}: NO DATA")
        return {}
    s = sorted(vals)
    d = dict(
        n=len(s),
        median=pct(s, 0.5),
        p25=pct(s, 0.25),
        p75=pct(s, 0.75),
        p90=pct(s, 0.90),
        mean=sum(s)/len(s),
        min=s[0], max=s[-1],
    )
    print(f"{label}: n={d['n']}  median={d['median']:.3f}  p25={d['p25']:.3f}  "
          f"p75={d['p75']:.3f}  p90={d['p90']:.3f}  mean={d['mean']:.3f}  "
          f"min={d['min']:.3f}  max={d['max']:.3f}")
    return d

# ---- per condition collectors ----
# MERGE: list of (ts, size)
merge_by_cond = defaultdict(list)
# BUY by leg: outcomeIndex 0/1 -> list of (ts, size)
buy_leg = defaultdict(lambda: {0: [], 1: []})
# REDEEM by cond (count + usdc)
redeem_by_cond = defaultdict(lambda: {"n": 0, "usdc": 0.0})
# also track conversion at cond level for context
conv_n = 0

merge_size_all = []   # per-merge-event size (batch size)
n_rows = 0
merge_usdc_total = 0.0
redeem_usdc_total = 0.0

with open(PATH, "r", encoding="utf-8") as f:
    for row in ijson.items(f, "item"):
        n_rows += 1
        t = row.get("type", "")
        if t not in ("MERGE", "TRADE", "REDEEM", "CONVERSION"):
            continue
        cid = row.get("conditionId", "") or ""
        ts = row.get("timestamp", 0) or 0
        try: ts = int(ts)
        except: ts = 0
        sz = row.get("size", 0) or 0
        try: sz = float(sz)
        except: sz = 0.0
        usdc = row.get("usdcSize", 0) or 0
        try: usdc = float(usdc)
        except: usdc = 0.0

        if t == "MERGE":
            merge_by_cond[cid].append((ts, sz))
            merge_size_all.append(sz)
            merge_usdc_total += usdc
        elif t == "REDEEM":
            r = redeem_by_cond[cid]
            r["n"] += 1; r["usdc"] += usdc
            redeem_usdc_total += usdc
        elif t == "CONVERSION":
            conv_n += 1
        elif t == "TRADE":
            side = row.get("side", "") or ""
            if side != "BUY":
                continue
            oi = row.get("outcomeIndex", 999)
            try: oi = int(oi)
            except: oi = 999
            if oi in (0, 1):
                buy_leg[cid][oi].append((ts, sz))

print("TOTAL ROWS SCANNED:", n_rows)
print("MERGE events:", len(merge_size_all), " merge_usdc_total=%.2f" % merge_usdc_total)
print("CONVERSION events:", conv_n)
print("REDEEM conds:", len(redeem_by_cond), " redeem_usdc_total=%.2f" % redeem_usdc_total)
print()

out = {}

# ===== 1) MERGE batch size distribution =====
print("=== 1) MERGE batch size (股数/份, size==usdcSize) ===")
out["merge_batch_size"] = stats(merge_size_all, "merge_batch_size")
# also in USDC terms equal to size for merge (1 pair = $1), so same numbers
print()

# ===== 2) inter-merge interval (相邻 merge 时间差, 秒) =====
# within same cond, consecutive merges; also global across all merges as alt view
inter_within = []   # within-condition consecutive merge gaps
for cid, lst in merge_by_cond.items():
    if len(lst) < 2:
        continue
    tss = sorted(t for t, _ in lst)
    for i in range(1, len(tss)):
        inter_within.append(tss[i] - tss[i-1])

# global: across ALL merge events sorted by time (the actual cadence of his merge clicks)
all_merge_ts = sorted(t for lst in merge_by_cond.values() for t, _ in lst)
inter_global = [all_merge_ts[i] - all_merge_ts[i-1] for i in range(1, len(all_merge_ts))]

print("=== 2) inter-merge interval (秒) ===")
out["inter_merge_within_cond_sec"] = stats(inter_within, "inter_merge_within_cond_sec")
out["inter_merge_global_sec"]      = stats(inter_global, "inter_merge_global_sec")
# fraction of within-cond gaps == 0 (same block batch merges)
if inter_within:
    z = sum(1 for g in inter_within if g == 0)
    print(f"  within-cond gaps==0 (同时间戳批量merge): {z}/{len(inter_within)} = {100*z/len(inter_within):.1f}%")
print()

# ===== 3) pair-ready -> merge lag =====
# pair-ready time = moment both legs first jointly held a matched pair.
# FIFO: sort each leg's buys by ts; the matched pair count = min(cumulative b0, cumulative b1).
# "pair becomes mergeable" first reached when min(cum0(t), cum1(t)) > 0, i.e. the later of
# (first buy on leg0, first buy on leg1). For lag we use: first_merge_ts - pair_ready_ts.
# Restrict to conds that have BOTH legs bought AND >=1 merge (clean merge-arb subset).
lags_first = []     # first_merge - pair_ready (time to FIRST merge after pair exists)
lags_50 = []        # time from pair_ready to merge of >=50% of eventually-merged size (size-weighted-ish)
n_pair_and_merge = 0
for cid, mlst in merge_by_cond.items():
    legs = buy_leg.get(cid)
    if not legs:
        continue
    b0 = sorted(legs[0]); b1 = sorted(legs[1])
    if not b0 or not b1:
        continue
    n_pair_and_merge += 1
    # pair-ready: later of the two first-buy timestamps
    pair_ready = max(b0[0][0], b1[0][0])
    mtss = sorted(t for t, _ in mlst)
    first_merge = mtss[0]
    lag = first_merge - pair_ready
    lags_first.append(lag)

print("=== 3) pair-ready -> first MERGE lag (秒) ===")
print("conds with both legs bought AND >=1 merge:", n_pair_and_merge)
out["pair_ready_to_first_merge_sec"] = stats(lags_first, "pair_ready_to_first_merge_sec")
# negative lags? (merge before later leg's first buy -> means earlier partial pair from re-buys)
neg = sum(1 for x in lags_first if x < 0)
print(f"  negative lags (merge before both-leg first buy): {neg}/{len(lags_first)} = {100*neg/len(lags_first):.1f}%")
# clip negatives to see positive-only distribution
pos = [x for x in lags_first if x >= 0]
out["pair_ready_to_first_merge_sec_posonly"] = stats(pos, "pair_ready_to_first_merge_sec (>=0 only)")
print()

# ===== 4) clearing channel: MERGE vs REDEEM (成对持仓如何清掉) =====
# By USDC notional of structural clearing:
print("=== 4) 清退渠道占比 (USDC notional) ===")
total_clear = merge_usdc_total + redeem_usdc_total
print(f"  MERGE  usdc = {merge_usdc_total:,.2f}  ({100*merge_usdc_total/total_clear:.2f}% of MERGE+REDEEM)")
print(f"  REDEEM usdc = {redeem_usdc_total:,.2f}  ({100*redeem_usdc_total/total_clear:.2f}% of MERGE+REDEEM)")
out["clear_channel_usdc"] = {
    "merge_usdc": merge_usdc_total, "redeem_usdc": redeem_usdc_total,
    "merge_pct": 100*merge_usdc_total/total_clear,
    "redeem_pct": 100*redeem_usdc_total/total_clear,
}
# By event count
n_merge_ev = len(merge_size_all)
n_redeem_ev = sum(r["n"] for r in redeem_by_cond.values())
tot_ev = n_merge_ev + n_redeem_ev
print(f"  MERGE  events = {n_merge_ev}  ({100*n_merge_ev/tot_ev:.2f}%)")
print(f"  REDEEM events = {n_redeem_ev}  ({100*n_redeem_ev/tot_ev:.2f}%)")
out["clear_channel_events"] = {
    "merge_n": n_merge_ev, "redeem_n": n_redeem_ev,
    "merge_pct": 100*n_merge_ev/tot_ev, "redeem_pct": 100*n_redeem_ev/tot_ev,
}
print()

json.dump(out, open(r"C:\Users\zexi\pmscan\audit\merge_timing_out.json", "w"), indent=2)
print("WROTE merge_timing_out.json")
