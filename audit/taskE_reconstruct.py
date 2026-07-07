import json, requests, sys, time

H={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}
RPCS=['https://polygon.drpc.org','https://polygon-rpc.com','https://polygon.api.onfinality.io/public']
SUB='0x4f1d5ae26fc31472966e951af3183308736d8de2'.lower()
NEGRISK='0xe2222d279d744050d28e00520010520000310f59'.lower()
CTFV2='0xe111180000d2663c0091e4f400237545b87b996b'.lower()
CTF='0x4d97dcd97ec945f40cf65f87097ace5ea0476045'.lower()
USDC='0x2791bca1f2de4661ed88a30c99a7a9449aa84174'.lower()
PUSD='0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb'.lower()
NEGADAPTER='0xd91e80cf2e7be2e162c6513ced06f1dd0da35296'.lower()  # neg risk adapter (seen)

T_ERC20='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
T_SINGLE='0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62'
T_BATCH='0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb'
T_OF1='0xd543adfd'  # OrderFilled (neg-risk / v2 variant)
T_OF2='0xd0a08e8c'

def rpc(method, params):
    last=None
    for r_ in RPCS:
        for _ in range(3):
            try:
                resp=requests.post(r_,headers=H,json={'jsonrpc':'2.0','id':1,'method':method,'params':params},timeout=30)
                j=resp.json()
                if 'result' in j and j['result'] is not None:
                    return j['result']
                last=j
            except Exception as e:
                last=str(e)
                time.sleep(0.4)
    raise RuntimeError(f'rpc fail {method} {last}')

def h2i(x):
    return int(x,16)

def addr_from_topic(t):
    return '0x'+t[-40:]

def words(data):
    d=data[2:] if data.startswith('0x') else data
    return [d[i:i+64] for i in range(0,len(d),64)]

def analyze(tx):
    rec=rpc('eth_getTransactionReceipt',[tx])
    out={'tx':tx,'to':rec['to'].lower(),'status':rec['status']}
    # ERC20 nets per token for SUBJECT
    erc20={}  # token -> {'in':..,'out':..}
    erc20_counterparties={}
    erc1155_in=[]   # (asset_id, amount)
    erc1155_out=[]
    of_events=[]    # decoded OrderFilled
    for L in rec['logs']:
        a=L['address'].lower()
        t0=L['topics'][0]
        if t0==T_ERC20:
            frm=addr_from_topic(L['topics'][1]).lower()
            to=addr_from_topic(L['topics'][2]).lower()
            val=h2i(L['data'])
            erc20.setdefault(a,{'in':0,'out':0,'in_from':{}, 'out_to':{}})
            if frm==SUB:
                erc20[a]['out']+=val
                erc20[a]['out_to'][to]=erc20[a]['out_to'].get(to,0)+val
            if to==SUB:
                erc20[a]['in']+=val
                erc20[a]['in_from'][frm]=erc20[a]['in_from'].get(frm,0)+val
        elif t0==T_SINGLE and a==CTF:
            # topics: op, from, to ; data: id, value
            frm=addr_from_topic(L['topics'][2]).lower()
            to=addr_from_topic(L['topics'][3]).lower()
            w=words(L['data'])
            aid=h2i('0x'+w[0]); val=h2i('0x'+w[1])
            if to==SUB: erc1155_in.append((aid,val,frm))
            if frm==SUB: erc1155_out.append((aid,val,to))
        elif t0==T_BATCH and a==CTF:
            frm=addr_from_topic(L['topics'][2]).lower()
            to=addr_from_topic(L['topics'][3]).lower()
            w=words(L['data'])
            # data: offset_ids, offset_vals, then arrays. parse dynamic.
            # standard: [off1][off2][len_ids][id...][len_vals][val...]
            off1=h2i('0x'+w[0])//32
            n=h2i('0x'+w[off1])
            ids=[h2i('0x'+w[off1+1+i]) for i in range(n)]
            off2=h2i('0x'+w[1])//32
            m=h2i('0x'+w[off2])
            vals=[h2i('0x'+w[off2+1+i]) for i in range(m)]
            for aid,val in zip(ids,vals):
                if to==SUB: erc1155_in.append((aid,val,frm))
                if frm==SUB: erc1155_out.append((aid,val,to))
        elif t0.startswith(T_OF1) or t0.startswith(T_OF2):
            # OrderFilled(orderHash idx, maker idx, taker idx, makerAssetId, takerAssetId, makerAmountFilled, takerAmountFilled, fee)
            maker=addr_from_topic(L['topics'][2]).lower()
            taker=addr_from_topic(L['topics'][3]).lower()
            w=words(L['data'])
            makerAssetId=h2i('0x'+w[0]); takerAssetId=h2i('0x'+w[1])
            makerAmt=h2i('0x'+w[2]); takerAmt=h2i('0x'+w[3]); fee=h2i('0x'+w[4])
            of_events.append({'maker':maker,'taker':taker,'makerAssetId':makerAssetId,'takerAssetId':takerAssetId,
                              'makerAmt':makerAmt,'takerAmt':takerAmt,'fee':fee,'addr':a})
    out['erc20']=erc20
    out['erc1155_in']=erc1155_in
    out['erc1155_out']=erc1155_out
    out['of_events']=of_events
    return out

if __name__=='__main__':
    txs=json.load(open(sys.argv[1]))
    results=[]
    for item in txs:
        tx=item['tx']
        try:
            r=analyze(tx)
            r['meta']=item
            results.append(r)
            print('done',tx[:12])
        except Exception as e:
            print('ERR',tx[:12],e)
        time.sleep(0.3)
    json.dump(results,open(sys.argv[2],'w'),indent=1)
    print('wrote',sys.argv[2])
