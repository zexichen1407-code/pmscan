"""
从全量活动数据里抽出某个 eventSlug 的逐笔成交(按时间排序),给回测当价格带。
用法: python extract_market.py <eventSlug> <out.json>
"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
slug = sys.argv[1] if len(sys.argv) > 1 else "atp-cobolli-zverev-2026-06-07"
out = sys.argv[2] if len(sys.argv) > 2 else r"C:\Users\zexi\pmscan\bot\market_fills.json"

rows = []
try:
    import ijson
    with open(SRC, "rb") as f:
        for o in ijson.items(f, "item"):
            if o.get("eventSlug") == slug:
                rows.append(o)
    src = "ijson"
except Exception as e:
    data = json.load(open(SRC, encoding="utf-8"))
    rows = [o for o in data if o.get("eventSlug") == slug]
    src = f"json.load ({e})"

def f(x):
    try: return float(x)
    except: return 0.0

rows.sort(key=lambda r: int(r.get("timestamp") or 0))
trades = [{
    "t": int(r.get("timestamp") or 0),
    "side": r.get("side"),
    "outcome": r.get("outcome"),
    "outcomeIndex": r.get("outcomeIndex"),
    "price": f(r.get("price")),
    "size": f(r.get("size")),
    "conditionId": r.get("conditionId"),
} for r in rows if r.get("type") == "TRADE"]
merges = [{"t": int(r.get("timestamp") or 0), "size": f(r.get("size"))}
          for r in rows if r.get("type") == "MERGE"]

# 识别两条腿(outcome 名称)
outs = {}
for tr in trades:
    outs[tr["outcome"]] = outs.get(tr["outcome"], 0) + 1

payload = {
    "slug": slug, "src": src,
    "n_trades": len(trades), "n_merges": len(merges),
    "outcomes": outs,
    "trades": trades, "merges": merges,
}
json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"[{src}] {slug}: {len(trades)} trades, {len(merges)} merges, outcomes={outs}")
print("wrote", out)
