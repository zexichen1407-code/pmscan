"""
FULL-HISTORY crawler for p3nny activity, bypassing the offset<=3000 cap.
Method: time-walk. For each `end` window, page offset 0..3000 (limit=1000).
Then set end = (oldest ts seen in window) and repeat, until a window returns
no NEW records older than what we already have.
Dedup key = (txHash, asset, type, timestamp, outcomeIndex, side, usdcSize).
"""
import json, urllib.request, urllib.error, sys, time, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
OUT=os.path.dirname(os.path.abspath(__file__))
def get(url,tries=8):
    last=None
    for i in range(tries):
        try:
            req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req,timeout=90) as r:
                return r.getcode(),json.load(r)
        except urllib.error.HTTPError as e:
            last=(e.code,e.read().decode('utf-8','replace')[:120])
            if e.code in (500,502,503,429,408): time.sleep(1.0+i*0.7); continue
            return last
        except Exception as e:
            last=("ERR",str(e)[:120]); time.sleep(1.0+i*0.7)
    return last

def key(a):
    return (a.get("transactionHash"),a.get("asset"),a.get("type"),
            a.get("timestamp"),a.get("outcomeIndex"),a.get("side"),a.get("usdcSize"))

seen=set(); all_acts=[]
end=None   # start = most recent
window=0; total_http=0; window_log=[]
GLOBAL_MIN=None
while True:
    window+=1
    base=f"https://data-api.polymarket.com/activity?user={W}&limit=1000"
    if end is not None: base+=f"&end={end}"
    win_min=None; win_max=None; win_new=0; win_rows=0; stop_reason=None
    for off in range(0,3001,1000):
        c,d=get(f"{base}&offset={off}")
        total_http+=1
        if c!=200 or not isinstance(d,list):
            stop_reason=f"offset {off} code {c}"; break
        if not d:
            stop_reason=f"empty at offset {off}"; break
        win_rows+=len(d)
        for a in d:
            ts=int(a["timestamp"]) if a.get("timestamp") else None
            if ts is not None:
                win_min=ts if win_min is None else min(win_min,ts)
                win_max=ts if win_max is None else max(win_max,ts)
            k=key(a)
            if k not in seen:
                seen.add(k); all_acts.append(a); win_new+=1
        if len(d)<1000:
            stop_reason=f"short page ({len(d)}) at offset {off}"; break
    window_log.append({"window":window,"end":end,"rows":win_rows,"new":win_new,
                       "ts_min":win_min,"ts_max":win_max,"stop":stop_reason})
    print(f" win{window:3} end={end} rows={win_rows} new={win_new} ts[{win_min}..{win_max}] stop={stop_reason}")
    # advance: next window ends just before the oldest ts we saw
    if win_min is None:
        print("  no data this window -> done"); break
    if GLOBAL_MIN is not None and win_min>=GLOBAL_MIN:
        # not making progress backward
        print("  no backward progress -> done"); break
    GLOBAL_MIN=win_min if GLOBAL_MIN is None else min(GLOBAL_MIN,win_min)
    if win_new==0:
        print("  zero new records -> done"); break
    new_end=win_min-1
    if end is not None and new_end>=end:
        print("  end not advancing -> done"); break
    end=new_end
    time.sleep(0.2)
    if window>500:
        print("  safety cap 500 windows"); break

print(f"\nTOTAL unique records: {len(all_acts)}  (http calls: {total_http})")
tss=[int(a['timestamp']) for a in all_acts if a.get('timestamp')]
if tss:
    import datetime
    print(f"ts span: {min(tss)}..{max(tss)} = {datetime.datetime.utcfromtimestamp(min(tss))} .. {datetime.datetime.utcfromtimestamp(max(tss))}")
    print(f"span days: {(max(tss)-min(tss))/86400:.1f}")

with open(os.path.join(OUT,"raw_activity_full.json"),"w",encoding="utf-8") as f:
    json.dump(all_acts,f,ensure_ascii=False)
with open(os.path.join(OUT,"full_window_log.json"),"w",encoding="utf-8") as f:
    json.dump(window_log,f,ensure_ascii=False)
print("dumped raw_activity_full.json + full_window_log.json")
