import json, urllib.request, urllib.error, time

UA = {"User-Agent": "Mozilla/5.0"}

COBOLLI = "104121662816591135157633034467926697984733660874566468356740527855523275425437"
ZVEREV  = "32113956590504382638811631788145724904940827912112275742691865237227049243037"
COND = "0x221b05db581e0beb3bd140683b89b06f6bb565f67674fb7243c6d3789ee18b96"

def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()

# 1) gamma metadata by conditionId to confirm clobTokenIds
print("=== GAMMA markets by conditionId ===")
st, body = get(f"https://gamma-api.polymarket.com/markets?condition_ids={COND}")
print("status", st)
try:
    data = json.loads(body)
    if isinstance(data, list) and data:
        m = data[0]
        print("question:", m.get("question"))
        print("slug:", m.get("slug"))
        print("clobTokenIds:", m.get("clobTokenIds"))
        print("outcomes:", m.get("outcomes"))
        print("startDate:", m.get("startDate"), "endDate:", m.get("endDate"))
        print("closed:", m.get("closed"), "umaResolutionStatus:", m.get("umaResolutionStatus"))
    else:
        print("no market / unexpected:", body[:400])
except Exception as e:
    print("parse err", e, body[:400])

# 2) price history for each token
for name, tok in [("COBOLLI", COBOLLI), ("ZVEREV", ZVEREV)]:
    print(f"\n=== prices-history {name} ===")
    url = f"https://clob.polymarket.com/prices-history?market={tok}&interval=max&fidelity=1"
    st, body = get(url)
    print("status", st, "len", len(body))
    try:
        d = json.loads(body)
        hist = d.get("history", d if isinstance(d, list) else [])
        print("points:", len(hist))
        if hist:
            print("first:", hist[0], "last:", hist[-1])
            json.dump(hist, open(rf"C:/Users/zexi/pmscan/audit/_ph_{name}.json","w"))
    except Exception as e:
        print("parse err", e, body[:300])
