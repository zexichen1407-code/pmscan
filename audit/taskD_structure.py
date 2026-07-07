import json
from taskD_probe import get_receipt, words, h2i, OF_TOPICS, decode_orderfilled_logs

# Understand the two-log structure: who is the REAL taker, and where does fee sit?
# In Polymarket exchange, the matching engine emits one OrderFilled per maker order,
# plus one aggregate OrderFilled where taker=the exchange addr (the taker's net).
# We must classify by REAL party, not the exchange placeholder.

NEGRISK_EX='0xe2222d279d744050d28e00520010520000310f59'
SUBJECT='0x4f1d5ae26fc31472966e951af3183308736d8de2'

for txh in ['0x90dca1f5ad0cd342991f5b06cb7c8116be84ba002025913926513f10e90346ae',
            '0xad1543692d95d6e59ec79abd6a0f256d0a7874bf7e46cf6237386cbad100ad89']:
    r=get_receipt(txh)
    ofs=decode_orderfilled_logs(r)
    print('=== tx', txh[:18])
    for o in ofs:
        is_agg = (o['taker'].lower()==NEGRISK_EX.lower())
        role_taker = 'EXCHANGE-AGG' if is_agg else ('SUBJECT' if o['taker'].lower()==SUBJECT.lower() else 'other')
        print('  maker=%s taker=%s [%s] makAmt=%d takAmt=%d FEE=%d' % (
            o['maker'][:10], o['taker'][:10], role_taker,
            o['makerAmountFilled'], o['takerAmountFilled'], o['fee']))
    # Also: is there a USDC transfer of the fee out? check ERC20 Transfer from subject of fee size
    print()
