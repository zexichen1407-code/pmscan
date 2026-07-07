import json,os,sys
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
pf=json.load(open('audit/raw_profit.json',encoding='utf-8'))
vf=json.load(open('audit/raw_volume.json',encoding='utf-8'))
tb=json.load(open('audit/type_breakdown.json',encoding='utf-8'))
def amt(store,w):
    r=store[w]['raw']
    return r[0]['amount'] if isinstance(r,list) and r else None

print("== PROFIT / VOLUME / ROI by window (lb-api, MEASURED) ==")
rows={}
for w in ['1d','7d','30d','all']:
    p=amt(pf,w); v=amt(vf,w); roi=(p/v*100) if v else None
    rows[w]={'profit':p,'volume':v,'roi_pct':roi}
    print(f"  {w:>4}: profit ${p:>13,.2f}  volume ${v:>15,.2f}  ROI {roi:>7.3f}%")

rew=tb['rewards']
print(f"\n== REWARDS (MEASURED from full activity) ==")
print(f"  total in pull: ${rew['total']:,.2f}  7d ${rew['last7d']:,.2f}  30d ${rew['last30d']:,.2f}")
pnl_all=rows['all']['profit']
print(f"  reward share of all-time lb-PnL (${pnl_all:,.0f}): {100*rew['total']/pnl_all:.2f}%  (INFERRED ratio)")
print(f"  reward share of 30d lb-PnL (${rows['30d']['profit']:,.0f}): {100*rew['last30d']/rows['30d']['profit']:.2f}%")

# write compact summary
summary={
 "wallet":"0x4f1d5ae26fc31472966e951af3183308736d8de2",
 "identity":{"lb_api_name":pf['all']['raw'][0].get('name'),"lb_api_pseudonym":pf['all']['raw'][0].get('pseudonym'),
             "activity_name":tb['identity']['name'],"activity_pseudonym":tb['identity']['pseudonym'],
             "activity_bio":tb['identity']['bio']},
 "profit_volume_roi":rows,
 "current_value_usd":6275.5053,
 "data_api_traded_markets":17081,
 "activity":{"records":tb['records'],"time_span":tb['time_span'],
             "type_breakdown":tb['type_breakdown'],"trade_side":tb['trade_side']},
 "rewards":rew,
 "arb_evidence":tb['arb_evidence'],
 "category_trade_usdc_refined":tb['category_trade_usdc_refined'],
 "top_events":tb['top_events'][:15],
 "reconciliation":tb['reconciliation'],
 "caveats":[
   "Full activity crawled via end= time-walk bypassing offset<=3000 cap. 301223 unique records, 75.6d span = full wallet lifetime (first activity ~2026-04-10 confirmed by binary search).",
   "lb-api profit/volume are Polymarket's own mark-to-market figures; they DO NOT label arb-spread vs directional. 99% BUY + 46k MERGE + 51k CONVERSION is structural evidence of complementary-set arb, but the % of PnL 'from arb' is INFERRED not measured.",
   "type= filter on /activity returned 502 during this session; unfiltered+offset+end used instead.",
   "Dedup key collapses exact-field-identical rows; a tiny number of genuinely-identical legitimate rows could be over-collapsed. Volume reconciles to within 0.6% of lb-api so impact is negligible.",
 ],
}
json.dump(summary,open('audit/SUMMARY.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)
print("\nwrote SUMMARY.json")
