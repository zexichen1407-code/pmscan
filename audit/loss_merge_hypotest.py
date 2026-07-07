import ijson
from collections import defaultdict
import statistics, json

PATH = r"C:\Users\zexi\pmscan\audit\raw_activity_full.json"

buys = defaultdict(lambda: {0: [], 1: []})
merge_size = defaultdict(float)
outcome_idx = defaultdict(set)
event_slug = {}

with open(PATH, "rb") as f:
    for rec in ijson.items(f, "item"):
        t = rec.get("type"); cid = rec.get("conditionId")
        if not cid: continue
        if t == "MERGE":
            merge_size[cid] += float(rec.get("size") or 0)
        elif t == "TRADE" and rec.get("side") == "BUY":
            oi = rec.get("outcomeIndex")
            outcome_idx[cid].add(oi)
            if oi in (0,1):
                buys[cid][oi].append((float(rec.get("price") or 0),
                                      float(rec.get("size") or 0),
                                      rec.get("timestamp")))
            event_slug.setdefault(cid, rec.get("eventSlug"))

def wavg(rows):
    tot = sum(s for _,s,_ in rows)
    return (sum(pr*s for pr,s,_ in rows)/tot if tot else None), tot

neg = []
both = 0
for cid, sides in buys.items():
    if not sides[0] or not sides[1]: continue
    if merge_size.get(cid,0) <= 0: continue
    if not outcome_idx[cid].issubset({0,1}): continue
    both += 1
    p0,sz0 = wavg(sides[0]); p1,sz1 = wavg(sides[1])
    if p0 <= p1:
        cheap, exp = sides[0], sides[1]
    else:
        cheap, exp = sides[1], sides[0]
    pc,szc = wavg(cheap); pe,sze = wavg(exp)
    pair = pc+pe
    if pair <= 1.0: continue
    msz = merge_size[cid]
    mq = min(szc, sze, msz)
    neg.append(dict(cid=cid, p=pc, q=pe, pair=pair, mq=mq,
                    cheap=cheap, exp=exp, event=event_slug.get(cid)))

# ---- Hypothesis sub-tests ----
# H1: expensive leg median ts > cheap leg median ts (per market), weighted by loss
exp_later = 0; exp_later_lossw = 0.0; total_lossw = 0.0
deltas = []
# H2: do the LATEST expensive buys (those above the cheap price's complement) push pair>1?
#     proxy: was the cheap leg fully purchasable at the time? Instead test:
#     "if we excluded expensive buys made AFTER the last cheap buy, would pair be <=1?"
pushed_over_by_late = 0
late_exp_at_rising = 0   # late expensive buys priced higher than early expensive buys
n_with_late_exp = 0

for r in neg:
    loss = (r["pair"]-1.0)*r["mq"]
    total_lossw += loss
    cts = [ts for _,_,ts in r["cheap"] if ts is not None]
    ets = [ts for _,_,ts in r["exp"] if ts is not None]
    if cts and ets:
        cm, em = statistics.median(cts), statistics.median(ets)
        deltas.append(em-cm)
        if em > cm:
            exp_later += 1; exp_later_lossw += loss

    # late expensive = expensive buys made after the last cheap buy
    if cts:
        last_cheap = max(cts)
        early_exp = [(pr,s,ts) for pr,s,ts in r["exp"] if ts is not None and ts <= last_cheap]
        late_exp  = [(pr,s,ts) for pr,s,ts in r["exp"] if ts is not None and ts > last_cheap]
        if late_exp:
            n_with_late_exp += 1
            # recompute expensive wavg using only early_exp; pair with cheap
            if early_exp:
                pe_early,_ = wavg(early_exp)
                if r["p"] + pe_early <= 1.0:
                    pushed_over_by_late += 1
            else:
                # ALL expensive buys came after cheap -> definitionally late
                pushed_over_by_late += 1
            # rising? late exp avg price vs early exp avg price (or vs full if no early)
            le_p,_ = wavg(late_exp)
            ref = wavg(early_exp)[0] if early_exp else None
            if ref is not None and le_p > ref:
                late_exp_at_rising += 1

out = {
  "n_both_side_merge_binary": both,
  "n_neg": len(neg),
  "exp_later_count": exp_later,
  "exp_later_pct": round(exp_later/len(neg)*100,2),
  "exp_later_loss_weighted_pct": round(exp_later_lossw/total_lossw*100,2),
  "median_delta_sec": statistics.median(deltas) if deltas else None,
  "mean_delta_sec": round(statistics.mean(deltas),1) if deltas else None,
  "n_with_late_exp_after_last_cheap": n_with_late_exp,
  "pushed_over_by_late_exp": pushed_over_by_late,
  "pushed_over_pct_of_neg": round(pushed_over_by_late/len(neg)*100,2),
  "late_exp_at_rising_price": late_exp_at_rising,
}
print(json.dumps(out, indent=2))
