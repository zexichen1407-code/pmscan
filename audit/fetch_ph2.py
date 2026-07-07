import json, urllib.request, urllib.error
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0"}
COBOLLI = "104121662816591135157633034467926697984733660874566468356740527855523275425437"
ZVEREV  = "32113956590504382638811631788145724904940827912112275742691865237227049243037"

def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()

# Confirm token mapping via gamma slug
print("=== GAMMA by slug ===")
st, body = get("https://gamma-api.polymarket.com/markets?slug=atp-cobolli-zverev-2026-06-07")
print("status", st)
try:
    data = json.loads(body)
    if data:
        m = data[0]
        print("question:", m.get("question"))
        print("outcomes:", m.get("outcomes"))
        print("clobTokenIds:", m.get("clobTokenIds"))
        print("conditionId:", m.get("conditionId"))
except Exception as e:
    print("err", e, body[:300])

# His fill window: 1749301479 (13:04) .. 1749318171 (17:42) on 2026-06-07
# Fetch fidelity=1 (1-min) within a tight window using startTs/endTs
START = 1749290000  # ~10:00 UTC 2026-06-07
END   = 1749320000  # ~18:13 UTC
for name, tok in [("COBOLLI", COBOLLI), ("ZVEREV", ZVEREV)]:
    for fid in [1]:
        url = f"https://clob.polymarket.com/prices-history?market={tok}&startTs={START}&endTs={END}&fidelity={fid}"
        st, body = get(url)
        try:
            d = json.loads(body)
            hist = d.get("history", [])
            print(f"\n{name} fid={fid} window status={st} points={len(hist)}")
            if hist:
                a=datetime.fromtimestamp(hist[0]['t'],timezone.utc)
                b=datetime.fromtimestamp(hist[-1]['t'],timezone.utc)
                print(f"  range {a.isoformat()} .. {b.isoformat()}")
                # median spacing
                ts=[h['t'] for h in hist]
                difs=sorted(ts[i+1]-ts[i] for i in range(len(ts)-1))
                print("  median spacing s:", difs[len(difs)//2] if difs else None)
                json.dump(hist, open(rf"C:/Users/zexi/pmscan/audit/_phw_{name}.json","w"))
        except Exception as e:
            print(name, "err", e, body[:200])
