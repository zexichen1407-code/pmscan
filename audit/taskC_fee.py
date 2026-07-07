import json, urllib.request, time, random
from collections import defaultdict, Counter

SUBJECT='0x4f1d5ae26fc31472966e951af3183308736d8de2'
SUBJ_TOPIC='0x'+'0'*24+SUBJECT[2:].lower()
NEGRISK='0xe2222d279d744050d28e00520010520000310f59'.lower()
STDV2='0xe111180000d2663c0091e4f400237545b87b996b'.lower()
OF1='0xd543adfd'; OF2='0xd0a08e8c'
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']

def rpc(method, params):
    last=None
    for attempt in range(6):
        url=RPCS[attempt % len(RPCS)]
        try:
            body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
            req=urllib.request.Request(url, data=body, headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0'})
            r=json.load(urllib.request.urlopen(req, timeout=45))
            if 'result' in r and r['result'] is not None:
                return r['result']
            last=r
        except Exception as e:
            last=str(e)
            time.sleep(0.4)
    return None

def cat(slug):
    s=(slug or '').lower()
    if any(k in s for k in ['temperature','weather','rain','snow','beijing','london','nyc','moscow','warsaw','home-value','median-home']): return 'weather/index'
    if any(k in s for k in ['nba','lakers','celtics','warriors','lebron']): return 'NBA'
    if any(k in s for k in ['f1','grand-prix','verstappen','hamilton','formula']): return 'F1'
    if any(k in s for k in ['msi','lol','dota','cs2','valorant','dcg','esport','league-of-legends']): return 'esports'
    if any(k in s for k in ['elon','tweet','musk']): return 'elon-tweets'
    if any(k in s for k in ['election','president','primary','senate','governor','nominee','love-island','win-']): return 'elections/awards'
    if any(k in s for k in ['fifa','fifwc','soccer','epl','laliga','uefa','-vs-','world-cup','paraguay']): return 'soccer/worldcup'
    if any(k in s for k in ['tennis','atp','wta','wimbledon']): return 'tennis'
    if any(k in s for k in ['btc','eth','bitcoin','ethereum','crypto','solana','market-cap','largest-company']): return 'crypto/markets'
    if any(k in s for k in ['mlb','nhl','nfl','ufc','boxing','golf','pga','championship','al-west','astros','mariners']): return 'other-sports'
    if any(k in s for k in ['box-office','toy-story','hormuz','strait']): return 'misc-events'
    return 'other'

d=json.load(open('recent_activity.json'))
trades=[r for r in d if r.get('type')=='TRADE' and r.get('side')=='BUY' and r.get('outcome')=='No']

# group txs by conditionId; we want broad coverage of distinct markets across categories.
by_cid=defaultdict(list)
for r in trades:
    by_cid[r['conditionId']].append(r)

# bucket cids by category
cid_cat={cid: cat(rs[0]['slug']) for cid,rs in by_cid.items()}
cat_cids=defaultdict(list)
for cid,c in cid_cat.items():
    cat_cids[c].append(cid)

random.seed(42)
# pick markets per category to spread coverage; aim >=30 markets, >=300 legs
TARGET_LEGS=420
picked_cids=[]
# round-robin across categories
cats=list(cat_cids.keys())
for c in cats:
    random.shuffle(cat_cids[c])
# take up to N per category proportional but capped
per_cat_cap={c: min(len(cat_cids[c]), 12) for c in cats}
idx=0
while len(picked_cids) < 90:
    progressed=False
    for c in cats:
        if per_cat_cap[c]>0 and cat_cids[c]:
            picked_cids.append(cat_cids[c].pop())
            per_cat_cap[c]-=1
            progressed=True
        if len(picked_cids)>=90: break
    if not progressed: break

# Now collect txHashes for these cids (each cid -> its No BUY txs). dedupe tx.
tx_meta={}  # tx -> (cid, slug, cat)
for cid in picked_cids:
    for r in by_cid[cid]:
        tx_meta[r['transactionHash']]=(cid, r['slug'], cid_cat[cid])

txs=list(tx_meta.keys())
random.shuffle(txs)

results=[]  # per leg dict
legs_done=0
tx_used=0
cids_seen=set()
errors=0
for tx in txs:
    if legs_done>=TARGET_LEGS and len(cids_seen)>=35:
        break
    rec=rpc('eth_getTransactionReceipt',[tx])
    if not rec:
        errors+=1
        continue
    cid, slug, c = tx_meta[tx]
    tx_used+=1
    found_leg=False
    for lg in rec['logs']:
        topics=lg.get('topics',[])
        if not topics: continue
        t0=topics[0]
        if t0[:10] not in (OF1,OF2): continue
        if len(topics)<4: continue
        if topics[3].lower()!=SUBJ_TOPIC:  # SUBJECT must be taker
            continue
        addr=lg['address'].lower()
        data=lg['data'][2:]
        w=[int(data[i:i+64],16) for i in range(0,len(data),64)]
        if len(w)<5: continue
        makerAssetId=w[0]; takerAssetId=w[1]; makerAmt=w[2]; takerAmt=w[3]; fee=w[4]
        # SUBJECT is taker buying NO: he gives USDC (makerAssetId on his order is USDC=0... but here event maker is counterparty)
        # In this event, the SUBJECT as taker: takerAssetId/takerAmt is what HE provides? Determine notional from USDC side.
        # USDC side = the leg with assetId==0. makerAmt is USDC if makerAssetId==0; takerAmt is USDC if takerAssetId==0.
        if makerAssetId==0:
            usdc=makerAmt; shares=takerAmt
        elif takerAssetId==0:
            usdc=takerAmt; shares=makerAmt
        else:
            usdc=None; shares=None
        notional = (usdc/1e6) if usdc is not None else None
        sh = (shares/1e6) if shares else None
        price = (notional/sh) if (notional and sh) else None
        results.append({
            'tx':tx,'cid':cid,'slug':slug,'cat':c,
            'exchange':'negrisk' if addr==NEGRISK else ('stdv2' if addr==STDV2 else addr),
            'fee_raw':fee,'fee_usd':fee/1e6,
            'notional_usd':notional,'shares':sh,'price':price,
            'makerAssetId0': makerAssetId==0,'takerAssetId0': takerAssetId==0,
        })
        legs_done+=1; found_leg=True
    if found_leg:
        cids_seen.add(cid)

json.dump(results, open('taskC_legs.json','w'))
print('TX fetched(used):',tx_used,'errors:',errors)
print('TOTAL SUBJECT-taker legs decoded:',len(results))
print('distinct cids with legs:',len(cids_seen))
print('exchanges:',Counter(r['exchange'] for r in results))
nz=[r for r in results if r['fee_raw']>0]
print('legs fee>0:',len(nz),'max_fee_usd:', max((r['fee_usd'] for r in results), default=0))
tot_fee=sum(r['fee_usd'] for r in results)
tot_not=sum((r['notional_usd'] or 0) for r in results)
print('total fee_usd: %.6f  total notional_usd: %.2f  fee/notional: %.6f%%'%(tot_fee,tot_not,100*tot_fee/tot_not if tot_not else 0))
