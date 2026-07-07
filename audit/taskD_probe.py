import urllib.request, json, ssl, time

ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
UA = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

RPCS = ['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
NEGRISK_EX = '0xe2222d279d744050d28e00520010520000310f59'
STD_EX     = '0xe111180000d2663c0091e4f400237545b87b996b'
OF_TOPICS  = ('0xd543adfd','0xd0a08e8c')

def jget(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, context=ctx, timeout=timeout))

def rpc(method, params, timeout=60):
    body = json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    last=None
    for u in RPCS:
        try:
            req = urllib.request.Request(u, data=body, headers={**UA,'Content-Type':'application/json'})
            r = json.load(urllib.request.urlopen(req, context=ctx, timeout=timeout))
            if 'result' in r and r['result'] is not None:
                return r['result']
            last = r
        except Exception as e:
            last = str(e)
    return None

def get_receipt(txh):
    return rpc('eth_getTransactionReceipt', [txh])

def h2i(x):
    return int(x,16)

def words(data):
    h = data[2:] if data.startswith('0x') else data
    return [h[i:i+64] for i in range(0,len(h),64)]

def decode_orderfilled_logs(receipt, want_ex=None):
    """Return list of dicts for OrderFilled logs."""
    out=[]
    for lg in receipt.get('logs',[]):
        t0 = lg['topics'][0].lower()
        if not any(t0.startswith(p) for p in OF_TOPICS):
            continue
        addr = lg['address'].lower()
        if want_ex and addr != want_ex.lower():
            continue
        topics = lg['topics']
        maker = '0x'+topics[2][-40:] if len(topics)>2 else None
        taker = '0x'+topics[3][-40:] if len(topics)>3 else None
        w = words(lg['data'])
        # non-indexed: makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee
        rec = {
            'exchange': addr,
            'orderHash': topics[1] if len(topics)>1 else None,
            'maker': maker, 'taker': taker,
            'makerAssetId': h2i('0x'+w[0]) if len(w)>0 else None,
            'takerAssetId': h2i('0x'+w[1]) if len(w)>1 else None,
            'makerAmountFilled': h2i('0x'+w[2]) if len(w)>2 else None,
            'takerAmountFilled': h2i('0x'+w[3]) if len(w)>3 else None,
            'fee': h2i('0x'+w[4]) if len(w)>4 else None,
            'nwords': len(w),
        }
        out.append(rec)
    return out

if __name__ == '__main__':
    import sys
    # Step 1: confirm ABI on a known SUBJECT neg-risk tx
    SUBJECT='0x4f1d5ae26fc31472966e951af3183308736d8de2'
    d=json.load(open('recent_activity.json'))
    # find a TRADE BUY on neg-risk (weather slug suggests neg-risk multi)
    sample_txs=[]
    for x in d:
        if x.get('type')=='TRADE' and x.get('transactionHash'):
            sample_txs.append(x['transactionHash'])
        if len(sample_txs)>=3: break
    print('=== ABI confirm on SUBJECT trades ===')
    for txh in sample_txs:
        r=get_receipt(txh)
        if not r: print(txh,'no receipt'); continue
        ofs=decode_orderfilled_logs(r)
        print(txh, 'OrderFilled logs:', len(ofs))
        for o in ofs:
            print('   ex=%s nwords=%d maker=%s taker=%s makAmt=%s takAmt=%s fee=%s' % (
                o['exchange'][:10], o['nwords'], o['maker'][:8], o['taker'][:8],
                o['makerAmountFilled'], o['takerAmountFilled'], o['fee']))
