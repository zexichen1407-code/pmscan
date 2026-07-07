"""
META-AUDIT third-party cross-check, attempt #2: public Polygon RPC (no key).
eth_getTransactionCount = nonce = # of OUTBOUND tx this EOA ever sent.
Polymarket *proxy* wallets are smart-contract wallets (Gnosis-Safe-style),
so the proxy itself may have nonce 0 and be driven by a signer EOA. We probe:
 - is there code at this address? (eth_getCode -> proxy contract vs EOA)
 - native MATIC balance (eth_getBalance)
 - USDC balanceOf via eth_call (independent of Polymarket API)
 - current block (sanity that RPC is live)
These are on-chain facts from a node, NOT Polymarket's API.
"""
import json, urllib.request, urllib.error, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
USDC="0x2791bca1f2de4661ed88a30c99a7a9449aa84174"
USDC_NATIVE="0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"  # native USDC (Circle) on Polygon

RPCS=[
 "https://polygon-rpc.com",
 "https://rpc.ankr.com/polygon",
 "https://polygon.llamarpc.com",
 "https://polygon-bor-rpc.publicnode.com",
 "https://1rpc.io/matic",
]

def rpc(url, method, params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req=urllib.request.Request(url, data=body, headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def try_all(method, params, label):
    for u in RPCS:
        try:
            d=rpc(u, method, params)
            if "result" in d:
                return u, d["result"]
            else:
                print(f"   {label}: {u} -> err {d.get('error')}")
        except Exception as e:
            print(f"   {label}: {u} -> EXC {str(e)[:80]}")
        time.sleep(0.3)
    return None, None

out={}
print("=== current block (RPC liveness) ===")
u,bn=try_all("eth_blockNumber",[],"blockNumber")
print(f"  via {u}: block={int(bn,16) if bn else None}")
out["block"]=int(bn,16) if bn else None

print("\n=== code at wallet (proxy contract vs EOA) ===")
u,code=try_all("eth_getCode",[W,"latest"],"getCode")
is_contract = code not in (None,"0x","0x0")
print(f"  via {u}: codelen={len(code) if code else 0}  -> {'SMART-CONTRACT (proxy wallet)' if is_contract else 'EOA'}")
out["is_contract"]=is_contract; out["code_prefix"]=code[:42] if code else None

print("\n=== outbound tx count (nonce) ===")
u,nonce=try_all("eth_getTransactionCount",[W,"latest"],"txcount")
print(f"  via {u}: nonce(outbound tx)={int(nonce,16) if nonce else None}")
out["nonce"]=int(nonce,16) if nonce else None

print("\n=== MATIC balance ===")
u,bal=try_all("eth_getBalance",[W,"latest"],"balance")
print(f"  via {u}: {int(bal,16)/1e18 if bal else None} MATIC")
out["matic"]=int(bal,16)/1e18 if bal else None

# USDC balanceOf(W): selector 0x70a08231 + padded addr
def balanceof(token):
    data="0x70a08231"+"0"*24+W[2:].lower()
    u,res=try_all("eth_call",[{"to":token,"data":data},"latest"],"balanceOf")
    if res and res!="0x":
        return u, int(res,16)/1e6
    return u,None
print("\n=== USDC balance (independent of Polymarket API) ===")
u,b1=balanceof(USDC); print(f"  USDC.e via {u}: {b1}")
u,b2=balanceof(USDC_NATIVE); print(f"  native USDC via {u}: {b2}")
out["usdc_e"]=b1; out["usdc_native"]=b2

json.dump(out,open("meta_onchain_rpc_out.json","w"),indent=1)
print("\nwrote meta_onchain_rpc_out.json")
