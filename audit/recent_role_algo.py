import json
import time
import datetime as dt
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


SUBJECT = "0x4f1d5ae26fc31472966e951af3183308736d8de2".lower()
STD_EX = "0xe111180000d2663c0091e4f400237545b87b996b".lower()
NEG_EX = "0xe2222d279d744050d28e00520010520000310f59".lower()
EXCHANGES = {STD_EX, NEG_EX}
OF_PREFIXES = ("0xd543adfd", "0xd0a08e8c")
RPCS = [
    "https://polygon.drpc.org",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
]
HDR = {"User-Agent": "Mozilla/5.0 role-audit", "Content-Type": "application/json"}


def word(data, i):
    h = data[2:] if data.startswith("0x") else data
    return int(h[i * 64 : (i + 1) * 64], 16)


def topic_addr(topic):
    return "0x" + topic[-40:].lower()


def rpc(method, params, tries=4):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last = None
    for endpoint in RPCS:
        for attempt in range(tries):
            try:
                r = requests.post(endpoint, json=payload, headers=HDR, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("result") is not None:
                        return data["result"]
                    last = data
                else:
                    last = r.text[:160]
            except Exception as exc:
                last = repr(exc)
            time.sleep(0.15 * (attempt + 1))
    return None


def receipt(tx):
    return rpc("eth_getTransactionReceipt", [tx])


def block_time(block_number_hex):
    blk = rpc("eth_getBlockByNumber", [block_number_hex, False], tries=2)
    if not blk:
        return None
    return int(blk["timestamp"], 16)


def decode_orderfilled(rec):
    out = []
    for log in rec.get("logs", []):
        topics = log.get("topics") or []
        if len(topics) < 4:
            continue
        if not any(topics[0].lower().startswith(prefix) for prefix in OF_PREFIXES):
            continue
        try:
            maker_asset = word(log["data"], 0)
            taker_asset = word(log["data"], 1)
            maker_amt = word(log["data"], 2)
            taker_amt = word(log["data"], 3)
            fee = word(log["data"], 4)
        except Exception:
            continue
        out.append(
            {
                "exchange": log["address"].lower(),
                "maker": topic_addr(topics[2]),
                "taker": topic_addr(topics[3]),
                "makerAssetId": maker_asset,
                "takerAssetId": taker_asset,
                "makerAmountFilled": maker_amt,
                "takerAmountFilled": taker_amt,
                "fee": fee,
            }
        )
    return out


def classify_tx(tx):
    rec = receipt(tx)
    if not rec:
        return {"tx": tx, "role": "NO_RECEIPT", "fee_micro": 0, "logs": []}
    logs = decode_orderfilled(rec)
    relevant = []
    sub_taker = []
    sub_maker = []
    sub_taker_agg = []
    for item in logs:
        maker = item["maker"].lower()
        taker = item["taker"].lower()
        taker_is_exchange = taker in EXCHANGES
        if maker == SUBJECT or taker == SUBJECT:
            relevant.append(item)
        if taker == SUBJECT:
            sub_taker.append(item)
        if maker == SUBJECT and not taker_is_exchange:
            sub_maker.append(item)
        if maker == SUBJECT and taker_is_exchange:
            sub_taker_agg.append(item)

    if (sub_taker or sub_taker_agg) and sub_maker:
        role = "MIXED"
    elif sub_taker or sub_taker_agg:
        role = "TAKER"
    elif sub_maker:
        role = "MAKER"
    else:
        role = "NO_SUBJECT_LOG"

    return {
        "tx": tx,
        "role": role,
        "block_ts": block_time(rec["blockNumber"]),
        "fee_micro": sum(x["fee"] for x in sub_taker_agg) or sum(x["fee"] for x in relevant),
        "sub_taker_logs": len(sub_taker),
        "sub_maker_logs": len(sub_maker),
        "sub_taker_agg_logs": len(sub_taker_agg),
        "logs": relevant[:20],
    }


def load_rows():
    with open("audit/live_recent_activity_jul1.json", encoding="utf-8") as f:
        rows = json.load(f)
    return sorted(rows, key=lambda x: int(x.get("timestamp") or 0))


def main():
    rows = load_rows()
    trades = [r for r in rows if r.get("type") == "TRADE" and r.get("transactionHash")]
    tx_meta = {}
    for r in trades:
        tx = r["transactionHash"]
        tx_meta.setdefault(tx, {"rows": [], "timestamp": int(r["timestamp"])})
        tx_meta[tx]["rows"].append(r)
        tx_meta[tx]["timestamp"] = min(tx_meta[tx]["timestamp"], int(r["timestamp"]))

    txs = sorted(tx_meta, key=lambda t: tx_meta[t]["timestamp"], reverse=True)
    print(f"unique trade txs: {len(txs)}")

    decoded = {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(classify_tx, tx): tx for tx in txs}
        done = 0
        for fut in as_completed(futures):
            tx = futures[fut]
            try:
                decoded[tx] = fut.result()
            except Exception as exc:
                decoded[tx] = {"tx": tx, "role": "ERROR", "error": repr(exc), "fee_micro": 0, "logs": []}
            done += 1
            if done % 500 == 0:
                print(f"decoded {done}/{len(txs)}")

    enriched = []
    for tx, meta in tx_meta.items():
        rs = meta["rows"]
        first = min(int(r["timestamp"]) for r in rs)
        cost = sum(float(r.get("usdcSize") or 0) for r in rs)
        size = sum(float(r.get("size") or 0) for r in rs)
        d = decoded.get(tx, {})
        enriched.append(
            {
                "tx": tx,
                "timestamp": first,
                "time_utc": dt.datetime.fromtimestamp(first, dt.UTC).isoformat(),
                "role": d.get("role"),
                "fee": round((d.get("fee_micro") or 0) / 1e6, 8),
                "sub_taker_logs": d.get("sub_taker_logs", 0),
                "sub_maker_logs": d.get("sub_maker_logs", 0),
                "sub_taker_agg_logs": d.get("sub_taker_agg_logs", 0),
                "cost": cost,
                "size": size,
                "side_set": sorted(set(r.get("side") for r in rs)),
                "outcome_set": sorted(set(str(r.get("outcome")) for r in rs)),
                "eventSlug": rs[0].get("eventSlug"),
                "conditionId": rs[0].get("conditionId"),
                "title": rs[0].get("title"),
                "rows": rs,
                "decode": d,
            }
        )

    closes = [r for r in rows if r.get("type") in ("MERGE", "CONVERSION")]
    next_close = []
    # Use nearest later close by condition for MERGE and by event for CONVERSION.
    closes_by_cond = defaultdict(list)
    closes_by_event = defaultdict(list)
    for c in closes:
        ts = int(c["timestamp"])
        if c.get("conditionId"):
            closes_by_cond[c["conditionId"]].append(c)
        if c.get("eventSlug"):
            closes_by_event[c["eventSlug"]].append(c)
    for bucket in list(closes_by_cond.values()) + list(closes_by_event.values()):
        bucket.sort(key=lambda x: int(x["timestamp"]))

    for e in enriched:
        t = e["timestamp"]
        candidates = []
        for c in closes_by_cond.get(e.get("conditionId"), []):
            dt_sec = int(c["timestamp"]) - t
            if dt_sec >= 0:
                candidates.append((dt_sec, c))
                break
        for c in closes_by_event.get(e.get("eventSlug"), []):
            dt_sec = int(c["timestamp"]) - t
            if dt_sec >= 0:
                candidates.append((dt_sec, c))
                break
        if candidates:
            dt_sec, c = min(candidates, key=lambda x: x[0])
            e["dt_to_close"] = dt_sec
            e["next_close_type"] = c["type"]
            e["next_close_tx"] = c.get("transactionHash")
        else:
            e["dt_to_close"] = None
            e["next_close_type"] = None
            e["next_close_tx"] = None
        next_close.append(e)

    summary = {
        "activity_span": {
            "min_utc": dt.datetime.fromtimestamp(min(int(r["timestamp"]) for r in rows), dt.UTC).isoformat(),
            "max_utc": dt.datetime.fromtimestamp(max(int(r["timestamp"]) for r in rows), dt.UTC).isoformat(),
            "rows": len(rows),
            "trades": len(trades),
            "unique_trade_txs": len(txs),
        },
        "role_counts": Counter(e["role"] for e in enriched),
        "role_costs": {
            role: round(sum(e["cost"] for e in enriched if e["role"] == role), 6)
            for role in sorted(set(e["role"] for e in enriched))
        },
        "fee_by_role": {
            role: round(sum(e["fee"] for e in enriched if e["role"] == role), 6)
            for role in sorted(set(e["role"] for e in enriched))
        },
        "close_distance_buckets": {},
        "examples": {},
    }

    buckets = [
        ("<=5s", lambda x: x is not None and x <= 5),
        ("6-30s", lambda x: x is not None and 6 <= x <= 30),
        ("31-120s", lambda x: x is not None and 31 <= x <= 120),
        (">120s", lambda x: x is not None and x > 120),
        ("no_close", lambda x: x is None),
    ]
    for name, pred in buckets:
        subset = [e for e in enriched if pred(e["dt_to_close"])]
        summary["close_distance_buckets"][name] = {
            "n": len(subset),
            "roles": Counter(e["role"] for e in subset),
            "cost_by_role": {
                role: round(sum(e["cost"] for e in subset if e["role"] == role), 6)
                for role in sorted(set(e["role"] for e in subset))
            },
        }

    # Event-level timeline examples: latest events with maker first, taker later, close seconds after taker.
    by_event = defaultdict(list)
    for e in enriched:
        by_event[e["eventSlug"]].append(e)
    event_examples = []
    for event, items in by_event.items():
        items.sort(key=lambda x: x["timestamp"])
        closes_here = sorted([c for c in closes if c.get("eventSlug") == event], key=lambda x: int(x["timestamp"]))
        if not closes_here:
            continue
        for close in closes_here:
            ct = int(close["timestamp"])
            window = [x for x in items if 0 <= ct - x["timestamp"] <= 600]
            if len(window) < 2:
                continue
            maker_before = [x for x in window if x["role"] == "MAKER" and ct - x["timestamp"] > 5]
            taker_near = [x for x in window if x["role"] == "TAKER" and ct - x["timestamp"] <= 30]
            if maker_before and taker_near:
                event_examples.append(
                    {
                        "eventSlug": event,
                        "close_type": close["type"],
                        "close_ts": ct,
                        "close_time_utc": dt.datetime.fromtimestamp(ct, dt.UTC).isoformat(),
                        "close_tx": close.get("transactionHash"),
                        "close_size": close.get("size"),
                        "close_usdcSize_activity": close.get("usdcSize"),
                        "n_window": len(window),
                        "maker_before": maker_before[-5:],
                        "taker_near": taker_near[-8:],
                    }
                )
                break
    event_examples.sort(key=lambda x: x["close_ts"], reverse=True)
    summary["examples"]["maker_then_taker_then_close"] = event_examples[:12]

    # Latest individual tx examples by role.
    for role in ("MAKER", "TAKER", "MIXED"):
        summary["examples"][f"latest_{role.lower()}"] = [
            {
                "time_utc": e["time_utc"],
                "tx": e["tx"],
                "eventSlug": e["eventSlug"],
                "title": e["title"],
                "cost": round(e["cost"], 6),
                "size": round(e["size"], 6),
                "fee": e["fee"],
                "dt_to_close": e["dt_to_close"],
                "close_type": e["next_close_type"],
            }
            for e in sorted([x for x in enriched if x["role"] == role], key=lambda x: x["timestamp"], reverse=True)[:20]
        ]

    with open("audit/recent_role_algo_out.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "trades": enriched}, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
