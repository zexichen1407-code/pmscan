# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
CL = r"C:\Users\zexi\pmscan\audit\arb_event_classified.json"
AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
with open(CL,'r',encoding='utf-8') as f: res = json.load(f)
with open(AGG,'r',encoding='utf-8') as f: agg = json.load(f)

# Top merge events by measured locked
mg = [r for r in res if r["pattern"]=="yes+no merge" and r["merge_locked_measured"]>0]
mg.sort(key=lambda r:-r["merge_locked_measured"])
print("===== TOP yes+no merge by MEASURED locked spread =====")
for r in mg[:12]:
    print(f"{r['merge_locked_measured']:10,.2f}  merge_usdc={r['merge_usdc']:11,.2f}  subs={r['n_submarkets']:3d}  cat={r['category']:14s} {r['eventSlug'][:55]}")

print("\n----- detail of #1 merge event legs -----")
top = mg[0]
for d in top["merge_detail"][:8]:
    print(json.dumps(d, ensure_ascii=False))

# Top conversion events
cv = [r for r in res if r["pattern"]=="neg-risk NO conversion"]
cv.sort(key=lambda r:-r["conv_usdc"])
print("\n===== TOP neg-risk NO conversion by conv_usdc =====")
for r in cv[:12]:
    print(f"conv_usdc={r['conv_usdc']:11,.2f} conv_cnt={r['conv_count']:5d} no_buy={r['no_buy_usdc']:11,.2f} proxy={r['conv_locked_proxy']:10,.2f} subs={r['n_submarkets']:3d} cat={r['category']:13s} {r['eventSlug'][:45]}")

# raw legs of top conversion event
topc = cv[0]
e = agg[topc["eventSlug"]]
print("\n----- raw legs of top conversion event:", topc["eventSlug"], "-----")
print("conv_usdc", e["conv_usdc"], "conv_size", e["conv_size"], "conv_count", e["conv_count"], "redeem_usdc", e["redeem_usdc"], "redeem_count", e["redeem_count"])
for cid, c in list(e["conds"].items())[:6]:
    print("  COND", c["title"][:50])
    for lab, od in c["out"].items():
        for side, sd in od.items():
            vw = sd["usdc"]/sd["size"] if sd["size"]>0 else 0
            print(f"     {lab:6s} {side:5s} n={sd['n']:4d} size={sd['size']:12.2f} usdc={sd['usdc']:12.2f} vwap={vw:.4f}")

# Top dutch book events
db = [r for r in res if r["pattern"]=="complete-set dutch book"]
db.sort(key=lambda r:-r["redeem_usdc"])
print("\n===== TOP complete-set dutch book by redeem_usdc =====")
for r in db[:12]:
    print(f"redeem={r['redeem_usdc']:10,.2f} rc={r['redeem_count']:4d} subs={r['n_submarkets']:3d} dutch_sum={r['dutch_sum_yes_vwap']} buy={r['buy_usdc']:10,.2f} cat={r['category']:13s} {r['eventSlug'][:45]}")
