# -*- coding: utf-8 -*-
"""Sanity cross-checks:
 S1: CONVERSION usdcSize == size invariant (sets), across all events.
 S2: For the 8 sampled slugs + 7 episodes: confirm >=3 distinct NO legs each ACTUALLY have a NO BUY,
     and report first-NO -> first-CONVERSION (does he convert before all legs touched?).
 S3: Population shape: among 2028 neg-risk events, how many distinct NO legs (N) distribution,
     and what fraction have N==2 (would be 2-outcome sports, EXCLUDED already since we require >=3).
 S4: Confirm no SELL-No dominant (he is a NO-basket buyer, not a seller).
"""
import sys, io, json
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import ijson
RAW = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

WATCH = {
 # 7 episodes
 "when-will-gpt-5pt6-be-released","fed-decision-in-july-181","colombia-presidential-election",
 "spacex-closing-market-cap-end-of-ipo-month-20260606222757973","2026-nba-champion",
 "elon-musk-of-tweets-june-22-june-24","elon-musk-of-tweets-june-1-june-3",
 # 8 sample
 "which-company-has-best-ai-model-end-of-june","what-will-spacexs-public-ticker-be",
 "bank-of-russia-decision-in-june","english-premier-league-top-goalscorer",
 "highest-temperature-in-munich-on-may-31-2026","highest-temperature-in-nyc-on-may-27-2026",
 "riku-dining-group-ipo-closing-market-cap","highest-temperature-in-shenzhen-on-june-3-2026",
}

ev = defaultdict(lambda: {"no_first":{}, "conv":[], "no_sell_cnt":0, "no_buy_cnt":0})
conv_mismatch=0; conv_total=0; conv_max_reldiff=0.0
allN=[]  # N legs per event (full pass for distribution)
# also gather all-events legs+conv for N distribution
ev_all = defaultdict(lambda: {"nolegs":set(), "hasconv":False})

n=0
with open(RAW,'rb') as fh:
    for r in ijson.items(fh,'item'):
        n+=1
        t=r.get('type'); es=r.get('eventSlug') or r.get('slug') or r.get('conditionId') or ""
        try: ts=int(r.get('timestamp'))
        except: continue
        if t=="TRADE":
            side=r.get('side'); out=r.get('outcome'); cid=r.get('conditionId') or ""
            if out=="No" and side=="BUY":
                ev_all[es]["nolegs"].add(cid)
                if es in WATCH:
                    e=ev[es]; e["no_buy_cnt"]+=1
                    if cid not in e["no_first"] or ts<e["no_first"][cid]: e["no_first"][cid]=ts
            elif out=="No" and side=="SELL" and es in WATCH:
                ev[es]["no_sell_cnt"]+=1
        elif t=="CONVERSION":
            ev_all[es]["hasconv"]=True
            try: sz=float(r.get('size') or 0)
            except: sz=0.0
            try: us=float(r.get('usdcSize') or 0)
            except: us=0.0
            conv_total+=1
            if sz>0:
                rd=abs(us-sz)/sz
                if rd>1e-6: conv_mismatch+=1
                conv_max_reldiff=max(conv_max_reldiff,rd)
            if es in WATCH:
                ev[es]["conv"].append((ts,sz,us))

print("=== S1: CONVERSION usdcSize==size invariant ===")
print(f"  conv rows={conv_total}  mismatches(rel>1e-6)={conv_mismatch}  max_reldiff={conv_max_reldiff:.2e}")

print("\n=== S3: N (distinct NO legs) distribution among events WITH conversion ===")
ncount=defaultdict(int)
negrisk3=0
for es,e in ev_all.items():
    if e["hasconv"]:
        N=len(e["nolegs"])
        ncount[N]+=1
        if N>=3: negrisk3+=1
print(f"  events with >=1 conversion: {sum(ncount.values())}")
print(f"  of those, N>=3 (kept as neg-risk): {negrisk3}")
for N in sorted(ncount):
    if N<=15 or ncount[N]>5:
        print(f"    N={N:2}: {ncount[N]} events")

print("\n=== S2/S4: watched events — distinct NO legs, NO-sell vs NO-buy, first-conv timing ===")
def fmt(x):
    if x<90: return f"{x:.0f}s"
    if x<5400: return f"{x/60:.1f}m"
    if x<172800: return f"{x/3600:.2f}h"
    return f"{x/86400:.2f}d"
for es in sorted(WATCH):
    e=ev.get(es)
    if not e or not e["no_first"]:
        print(f"  {es}: NOT FOUND"); continue
    N=len(e["no_first"]); t0=min(e["no_first"].values()); tall=max(e["no_first"].values())
    convs=sorted(e["conv"])
    fc = convs[0][0]-t0 if convs else None
    before = (convs[0][0] < tall) if convs else None
    print(f"  {es[:48]:48}  N={N:2} no_buy={e['no_buy_cnt']:5} no_sell={e['no_sell_cnt']:4} "
          f"first->allLegs={fmt(tall-t0):>7}  first->firstConv={fmt(fc) if fc is not None else 'NA':>7} "
          f"firstConv_before_allLegs={before}")
