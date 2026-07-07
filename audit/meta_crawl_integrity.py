"""
META-AUDIT: the REAL crawl-completeness test the prior agents did not run.

The crawler fetched <=4000 rows per window then set end=win_min-1. Every
non-terminal window hit exactly 4000 rows (the cap). The danger:
if MORE than 4000 records exist between a window's start and the natural
window_min, the crawler truncates at row 4000 and sets win_min to that row's
ts. The next window starts at win_min-1 (overlap). The ONLY way a record is
LOST is if records sharing win_min's exact second were split: some returned
in this window (>= win_min) and some NOT returned, AND the next window
(end=win_min-1) excludes ts==win_min, so those unreturned win_min-second rows
vanish.

Decisive tests:
 1) For each window boundary win_min, count how many file records have
    ts == win_min. If a window's win_min second is 'dense' (many records),
    truncation risk at that boundary is real -> flag.
 2) Reconstruct contiguous coverage: sort all distinct ts; verify EVERY
    distinct ts in [global_min, global_max] that appears in the file is
    bracketed by a window that fetched it. (We can't see server-side missing
    rows, but we CAN check internal consistency: are there ts values where
    file count looks suspiciously capped?)
 3) Per-window 'new' analysis: window 'rows'=4000 but 'new'<4000 means overlap
    re-fetched old rows. If any window had new==rows==4000 with NO overlap into
    the previous window's min, that window could have truncated mid-stream.
"""
import json, sys
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

acts=json.load(open(r"C:/Users/zexi/pmscan/audit/raw_activity_full.json",encoding="utf-8"))
log=json.load(open(r"C:/Users/zexi/pmscan/audit/full_window_log.json",encoding="utf-8"))

ts_count=Counter(int(a["timestamp"]) for a in acts if a.get("timestamp"))
print(f"records={len(acts)} distinct_ts={len(ts_count)}")

# --- TEST 1: density at each window's win_min boundary ---
print("\n=== TEST 1: record density at each window's win_min (boundary second) ===")
risky=[]
for w in log:
    wm=w.get("ts_min")
    if wm is None: continue
    c=ts_count.get(wm,0)
    # boundary risk: many records share win_min second AND window hit 4000 cap
    if w["rows"]==4000 and c>=50:
        risky.append((w["window"],wm,c))
print(f"  windows where win_min-second has >=50 records AND window was capped at 4000: {len(risky)}")
for win,wm,c in risky[:20]:
    print(f"    win{win}: win_min={wm} has {c} records at that exact second")
maxsec=ts_count.most_common(5)
print(f"  busiest seconds overall: {maxsec}")

# --- TEST 2: how many windows actually OVERLAP enough to bracket their boundary ---
# overlap = prev window's ts_min is RE-FETCHED by this window (i.e. this window's
# ts_max >= prev ts_min). end=prev_min-1 so this window CANNOT return ts==prev_min.
# Real overlap protection comes from the *page* re-fetch: 'new'<'rows'.
print("\n=== TEST 2: overlap protection per window (rows vs new) ===")
no_overlap=[w["window"] for w in log if w["rows"]>0 and w["new"]==w["rows"]]
print(f"  windows with new==rows (ZERO overlap re-fetch): {no_overlap if no_overlap else 'none'}")
overlap_rows = sum(w["rows"]-w["new"] for w in log)
print(f"  total overlap (re-fetched) rows across all windows = {overlap_rows}")

# --- TEST 3: contiguity of distinct ts coverage; look for capped-second artifact ---
# If the server ever returned a window of 4000 rows that did NOT span enough time
# to overlap the previous window's min, we'd see a window whose ts_max < (prev_min)
# by a large margin with high density. Quantify worst case.
print("\n=== TEST 3: per-window time span vs 4000-row cap (capped & narrow = risk) ===")
narrow=[]
for i,w in enumerate(log):
    if w["rows"]==4000 and w["ts_min"] and w["ts_max"]:
        span=w["ts_max"]-w["ts_min"]
        rate=4000/span if span>0 else 9e9
        if span < 300:   # 4000 rows in <5 min -> extremely dense, truncation likely mid-second-cluster
            narrow.append((w["window"],span,w["ts_min"],w["ts_max"]))
print(f"  capped windows spanning <300s (dense, truncation-prone): {len(narrow)}")
for win,span,a,b in narrow[:20]:
    print(f"    win{win}: span={span}s ts[{a}..{b}] (4000 rows in {span}s)")

# --- TEST 4: the genuine off-by-one risk: does any record sit EXACTLY at a
# window 'end' boundary (=prev_min-1)? end is exclusive-ish; a record at end could
# be the seam. Count file records whose ts equals any window 'end'. ---
ends=set(w["end"] for w in log if w["end"] is not None)
at_end=sum(1 for a in acts if a.get("timestamp") and int(a["timestamp"]) in ends)
print(f"\n=== TEST 4: file records sitting exactly on a window 'end' boundary ===")
print(f"  records with ts == some window.end: {at_end}")
print(f"  (these were captured by the window whose end is one MORE, i.e. next window;")
print(f"   if >0 they prove the seam is covered, not dropped)")

# --- TEST 5: cross-check 'all' volume integrity differently ---
# data-api /traded = 17081 markets. file TRADE conds = 17080. The 1-market gap:
# is it a market with only non-TRADE activity, or a genuinely missing market?
trade_conds=set(a.get("conditionId") for a in acts if a.get("type")=="TRADE")
all_conds=set(a.get("conditionId") for a in acts)
print(f"\n=== TEST 5: the 17080 vs 17081 condition gap ===")
print(f"  TRADE conds={len(trade_conds)} ALL-type conds={len(all_conds)}")
print(f"  conds in non-TRADE only (could explain the +1): {len(all_conds-trade_conds)}")
empties=sum(1 for a in acts if a.get('type')=='TRADE' and not a.get('conditionId'))
print(f"  TRADE rows with empty conditionId: {empties}")
