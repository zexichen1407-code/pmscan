# -*- coding: utf-8 -*-
"""
Probe the neg-risk NO conversion economics on concrete events to derive the
correct activity-only locked-spread identity.

NegRisk CONVERSION identity (Polymarket NegRiskAdapter):
  Converting a set of k NO tokens (one each from k of the N outcomes) returns:
     (k-1) USDC  +  1 YES token for each of the remaining (N-k) outcomes.
  Most commonly the bot converts the FULL set k=N:
     N NO tokens -> (N-1) USDC + 0 YES   (no outcomes remaining)
  So a full-set conversion of size S burns N*S NO tokens, releases (N-1)*S USDC.

  The activity row 'CONVERSION' has size S and usdcSize. Let's check whether
  usdcSize == (N-1)*S or == S, to learn what the field means.

Locked spread (full-set case, MEASURED):
  cost to acquire one full set of NOs = sum_i(vwap_No_i)  (i over N outcomes)
  payout per set = (N-1)
  locked per set = (N-1) - sum_i(vwap_No_i)
  total locked = locked_per_set * sets_converted
  sets_converted derived from conversion size.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
AGG = r"C:\Users\zexi\pmscan\audit\arb_event_agg.json"
with open(AGG,'r',encoding='utf-8') as f: agg=json.load(f)

for slug in ["fifwc-ger-kor-2026-06-14","daegu-mayoral-election-winner","fed-decision-in-july-181","colombia-presidential-election"]:
    e = agg.get(slug)
    if not e:
        print("MISSING", slug); continue
    N = len(e["conds"])
    print("\n##### EVENT", slug, " N_subs=", N)
    print("  conv_usdc=%.2f conv_size=%.2f conv_count=%d  redeem_usdc=%.2f rc=%d merge_usdc=%.2f"%(
        e["conv_usdc"], e["conv_size"], e["conv_count"], e["redeem_usdc"], e["redeem_count"], e["merge_usdc"]))
    sum_no_vwap = 0.0
    no_legs = 0
    no_buy_size_total = 0.0
    for cid,c in e["conds"].items():
        nob = c["out"].get("No",{}).get("BUY")
        yesb = c["out"].get("Yes",{}).get("BUY")
        nov = nob["usdc"]/nob["size"] if nob and nob["size"]>0 else None
        yev = yesb["usdc"]/yesb["size"] if yesb and yesb["size"]>0 else None
        if nov is not None:
            sum_no_vwap += nov; no_legs += 1; no_buy_size_total += nob["size"]
        print("   %-45s NoBuy_vwap=%s NoBuy_size=%s YesBuy_vwap=%s"%(
            c["title"][:45],
            ("%.4f"%nov) if nov is not None else "-",
            ("%.0f"%nob["size"]) if nob else "-",
            ("%.4f"%yev) if yev is not None else "-"))
    print("  sum_No_vwap over %d legs = %.4f   (full-set payout would be N-1=%d)"%(no_legs, sum_no_vwap, N-1))
    if no_legs==N and N>=2:
        locked_per_set = (N-1) - sum_no_vwap
        # sets = conv_size / N  if conv_size counts individual NO tokens burned
        sets_if_tokens = e["conv_size"]/N
        # OR conv_size already = sets
        print("  locked_per_set=(N-1)-sum_No_vwap = %.4f"%locked_per_set)
        print("  IF conv_size==tokens: sets=%.1f -> locked=%.2f"%(sets_if_tokens, locked_per_set*sets_if_tokens))
        print("  IF conv_size==sets:   sets=%.1f -> locked=%.2f"%(e["conv_size"], locked_per_set*e["conv_size"]))
