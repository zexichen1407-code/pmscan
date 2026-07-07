import json
from taskD_probe import get_receipt, words, h2i, OF_TOPICS

# Dump ALL 7 words for a SUBJECT neg-risk tx to nail the real ABI layout.
txh='0x90dca1f5ad0cd342991f5b06cb7c8116be84ba002025913926513f10e90346ae'
r=get_receipt(txh)
for lg in r['logs']:
    t0=lg['topics'][0].lower()
    if not any(t0.startswith(p) for p in OF_TOPICS): continue
    w=words(lg['data'])
    maker='0x'+lg['topics'][2][-40:]
    taker='0x'+lg['topics'][3][-40:]
    print('--- OrderFilled @', lg['address'][:10])
    print('   maker',maker,'taker',taker)
    for i,x in enumerate(w):
        v=h2i('0x'+x)
        print('   word[%d] = %d   (0x%s)' % (i, v, x))
