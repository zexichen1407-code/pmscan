import requests, json
HDR={"User-Agent":"Mozilla/5.0 audit"}
# PolygonScan v2 multichain API (etherscan unified). Try without key (rate-limited but works for source).
ADDRS={
 "NEGRISK":"0xe2222d279d744050d28e00520010520000310f59",
 "STD_V2":"0xe111180000d2663c0091e4f400237545b87b996b",
}
for name,addr in ADDRS.items():
    url=f"https://api.etherscan.io/v2/api?chainid=137&module=contract&action=getsourcecode&address={addr}"
    try:
        r=requests.get(url,headers=HDR,timeout=30)
        j=r.json()
    except Exception as e:
        print(name,"ERR",e); continue
    print("="*70, name, addr)
    print("status",j.get("status"),"msg",j.get("message"))
    res=j.get("result")
    if isinstance(res,list) and res:
        src=res[0].get("SourceCode","")
        cn=res[0].get("ContractName","")
        comp=res[0].get("CompilerVersion","")
        print("ContractName:",cn,"Compiler:",comp,"srclen:",len(src))
        # save raw
        open(f"_src_{name}.txt","w",encoding="utf-8").write(src)
        print("saved _src_%s.txt"%name)
    else:
        print("no result:",str(j)[:300])
