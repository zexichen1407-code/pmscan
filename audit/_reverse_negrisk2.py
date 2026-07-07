# -*- coding: utf-8 -*-
"""Refine: opening-burst vs staggered late-adds; marginal at basket completion; price-sorted entry."""
import ijson, io, sys, json, datetime
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"
WALLET = "0x4f1d5ae26fc31472966e951af3183308736d8de2"
NEG = {
 "when-will-gpt-5pt6-be-released":("ep_4","GPT-5.6",6),
 "highest-temperature-in-seoul-on-june-9-2026":("ep_5","Seoul",7),
 "fed-decision-in-july-181":("ep_6","Fed",5),
 "daegu-mayoral-election-winner":("ep_7","Daegu",2),
 "colombia-presidential-election":("ep_8","Colombia",8),
 "spacex-closing-market-cap-end-of-ipo-month-20260606222757973":("ep_9","SpaceX",8),
 "2026-nba-champion":("ep_10","NBA",14),
 "elon-musk-of-tweets-june-22-june-24":("ep_13","ElonJ22",7),
 "elon-musk-of-tweets-june-1-june-3":("ep_14","ElonJ1",8),
}
def f(x):
    try:return float(x)
    except:return 0.0
def iso(ts):
    try:return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%SZ")
    except:return None

# keep TRADE rows IN FILE ORDER (preserves sub-second sequence as fetched)
rows={s:[] for s in NEG}
seq=0
with open(RAW,'rb') as fh:
    for rec in ijson.items(fh,'item'):
        if rec.get('proxyWallet')!=WALLET: continue
        es=rec.get('eventSlug') or rec.get('slug') or ""
        if es not in NEG or rec.get('type')!="TRADE": continue
        rows[es].append({"seq":seq,"ts":int(rec.get('timestamp')),"cid":rec.get('conditionId') or "",
            "title":rec.get('title') or "","out":rec.get('outcome') or "","side":rec.get('side') or "",
            "price":f(rec.get('price')),"size":f(rec.get('size'))})
        seq+=1

for es,(ep,name,N) in NEG.items():
    # stable sort by ts (file order breaks ties => fetch sequence within same second)
    T=sorted(rows[es],key=lambda r:(r["ts"],r["seq"]))
    firsts={}  # cid -> first NO-build trade
    for r in T:
        no=(r["out"]=="No" and r["side"]=="BUY"); ys=(r["out"]=="Yes" and r["side"]=="SELL")
        if not(no or ys) or r["size"]<=0: continue
        effp=r["price"] if no else (1.0-r["price"])
        if r["cid"] not in firsts:
            firsts[r["cid"]]={"ts":r["ts"],"seq":r["seq"],"p":effp,"title":r["title"],
                              "path":r["out"]+"/"+r["side"]}
    order=sorted(firsts.values(),key=lambda d:(d["ts"],d["seq"]))
    t0=order[0]["ts"]
    # opening burst = legs whose first-entry within 120s of t0
    burst=[o for o in order if o["ts"]-t0<=120]
    late =[o for o in order if o["ts"]-t0>120]
    # marginal at completion (all N legs touched): use first-entry prices
    sum_first=sum(o["p"] for o in order)
    marg_complete=(N-1)-sum_first
    # within opening burst, is he price-agnostic or sorted? correlation of seq vs price
    import statistics
    burst_sorted=sorted(burst,key=lambda d:d["seq"])
    prices_in_seq=[round(o["p"],3) for o in burst_sorted]
    # how many distinct seconds in burst (1 = truly simultaneous)
    secs=sorted(set(o["ts"] for o in burst))
    print(f"\n===== {ep} {name} N={N} =====")
    print(f"  opening burst: {len(burst)} legs in {len(secs)} distinct second(s) span {burst[-1]['ts']-t0}s ; late-adds: {len(late)}")
    print(f"  marginal at basket completion (N-1)-Σfirst = {marg_complete:.4f}  (Σfirst={sum_first:.4f})")
    print(f"  burst entry prices in FETCH/seq order: {prices_in_seq}")
    if late:
        print(f"  LATE-ADD legs (the resolvable 'build order' signal):")
        for o in late:
            print(f"    +{(o['ts']-t0)//3600}h  {iso(o['ts'])}  p={o['p']:.4f} [{o['path']}] {o['title'][:50]}")
    # price-rank of opening legs: lowest-price(favorite) vs highest(longshot)
    by_price=sorted(burst,key=lambda d:d["p"])
    print(f"  burst price range: min={by_price[0]['p']:.3f} ({by_price[0]['title'][:30]}) .. max={by_price[-1]['p']:.3f} ({by_price[-1]['title'][:30]})")
