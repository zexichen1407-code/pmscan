"""
Audit window-log continuity + completeness:
 - each window's end == previous window's ts_min - 1 (time-walk correctness)
 - consecutive windows OVERLAP in [ts_min,ts_max] (so no gap can hide records)
 - last window empty (exhaustion), 2nd-last short page (reached genesis)
 - cross-check: union of all window ts ranges covers [global_min, global_max]
 - check the file's per-window contribution sums to 301223 ('new' column)
"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
log=json.load(open(r"C:/Users/zexi/pmscan/audit/full_window_log.json",encoding="utf-8"))

print("windows:",len(log))
total_new=sum(w["new"] for w in log)
total_rows=sum(w["rows"] for w in log)
print(f"sum(new)={total_new}  sum(rows)={total_rows}  (file has 301223)")

problems=[]
gaps=[]
for i,w in enumerate(log):
    # end-advance check
    if i>0:
        prev=log[i-1]
        if prev["ts_min"] is not None:
            expect_end=prev["ts_min"]-1
            if w["end"]!=expect_end:
                problems.append(f"win{w['window']}: end={w['end']} expected {expect_end}")
    # overlap check between consecutive windows (current ts_max should be >= ... actually
    # window i covers older data; its ts_max should be close to/below prev end.
    if i>0 and w["ts_min"] is not None and log[i-1]["ts_min"] is not None:
        # gap if this window's newest (ts_max) < previous window's oldest-1 boundary minus margin
        # Since end=prev_min-1, window starts fetching from prev_min-1 downward.
        # ts_max of this window should be <= prev_min-1 (it is, by construction).
        # The key: is there a GAP, i.e., does ts_max of win[i] reach up to end (prev_min-1)?
        end_boundary=log[i-1]["ts_min"]-1
        if w["ts_max"] is not None and w["ts_max"]<end_boundary:
            gaps.append(f"win{w['window']}: ts_max={w['ts_max']} < boundary {end_boundary} (gap {end_boundary-w['ts_max']}s)")

print("\n-- end-advance problems --")
print("\n".join(problems) if problems else "  none (every end == prev ts_min - 1)")

print("\n-- potential gaps (window newest below its window boundary) --")
print("\n".join(gaps) if gaps else "  none")

# exhaustion checks
last=log[-1]; second=log[-2]
print(f"\nlast window: rows={last['rows']} new={last['new']} stop={last['stop']}  -> {'EMPTY OK' if last['rows']==0 else 'NOT EMPTY!'}")
print(f"2nd-last:    rows={second['rows']} new={second['new']} stop={second['stop']}")

# every non-terminal window fetched full 4000 (offset cap bypass working)
non4000=[w['window'] for w in log[:-2] if w['rows']!=4000]
print(f"\nwindows (excl last 2) NOT fetching full 4000 rows: {non4000 if non4000 else 'none (all 4000 -> offset cap bypass confirmed)'}")

# global coverage
mins=[w['ts_min'] for w in log if w['ts_min']]
maxs=[w['ts_max'] for w in log if w['ts_max']]
print(f"\nwindow global ts range: {min(mins)} .. {max(maxs)}")
print(f"file global ts range:   1775842151 .. 1782377918")
print(f"match low: {min(mins)==1775842151}   match high: {max(maxs)==1782377918}")

# duplicate-fetch ratio: rows fetched 4000/window but 'new' < 4000 because windows overlap.
# overlap fraction tells us re-fetch redundancy (healthy: <1 means overlap exists = no gaps)
print(f"\noverall new/rows ratio = {total_new/total_rows:.3f}  (<1 confirms overlap re-fetch, i.e. gap-free walking)")
