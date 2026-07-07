"""
META-AUDIT on-chain attempt #3 via the ONE working non-Polymarket node.
Confirm independently the proxy is a Polymarket-pattern contract by reading
its bytecode signature, and try to read CTF (ERC1155) interaction by checking
USDC allowance to Polymarket exchange contracts (an on-chain fact set when a
Polymarket user first deposits/approves). Allowance!=0 to a Polymarket exchange
= independent proof this wallet is wired into Polymarket.
"""
import json, urllib.request, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
NODE="https://polygon-bor-rpc.publicnode.com"
W="0x4f1d5ae26fc31472966e951af3183308736d8de2"
USDC_E="0x2791bca1f2de4661ed88a30c99a7a9449aa84174"
CTF="0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
CTF_EXCHANGE="0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEGRISK_EXCHANGE="0xc5d563a36ae78145c45a50134d48a1215220f80a"
NEGRISK_ADAPTER="0xd91e80cf2e7be2e162c6513ced06f1dd0da35296"

def rpc(method,params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req=urllib.request.Request(NODE,data=body,headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.load(r)

# allowance(owner=W, spender=exchange): selector 0xdd62ed3e
def allowance(token,spender):
    data="0xdd62ed3e"+"0"*24+W[2:].lower()+"0"*24+spender[2:].lower()
    d=rpc("eth_call",[{"to":token,"data":data},"latest"])
    r=d.get("result")
    return int(r,16)/1e6 if (r and r!="0x") else None

# isApprovedForAll(owner=W, operator=exchange) on CTF (ERC1155): selector 0xe985e9c5
def approved_for_all(operator):
    data="0xe985e9c5"+"0"*24+W[2:].lower()+"0"*24+operator[2:].lower()
    d=rpc("eth_call",[{"to":CTF,"data":data},"latest"])
    r=d.get("result")
    return (int(r,16)==1) if (r and r!="0x") else None

print("=== independent on-chain proof of Polymarket wiring (via publicnode, NOT Polymarket) ===")
for name,sp in [("CTF_EXCHANGE",CTF_EXCHANGE),("NEGRISK_EXCHANGE",NEGRISK_EXCHANGE),("NEGRISK_ADAPTER",NEGRISK_ADAPTER)]:
    try:
        a=allowance(USDC_E,sp)
        print(f"  USDC.e allowance W -> {name}: {a}")
    except Exception as e:
        print(f"  allowance {name} err {str(e)[:80]}")
    time.sleep(0.4)
for name,op in [("CTF_EXCHANGE",CTF_EXCHANGE),("NEGRISK_EXCHANGE",NEGRISK_EXCHANGE),("NEGRISK_ADAPTER",NEGRISK_ADAPTER)]:
    try:
        a=approved_for_all(op)
        print(f"  CTF isApprovedForAll W -> {name}: {a}")
    except Exception as e:
        print(f"  approvedForAll {name} err {str(e)[:80]}")
    time.sleep(0.4)
