"""
按 maker 边际(≈买卖价差)筛全市场, 找价差宽、有成交、bot 没盯死的冷门二元盘。
原理: 二元盘两腿都挂最优买价并成交, 每对成本 ≈ 1 - 价差 => maker 边际 ≈ 价差。
所以按"价差宽 + 有量"排序 = 找肥盘(热门榜上没有, 因为那些被压到价差≈0)。
用法: python scan_maker_edge.py [扫描页数,默认20] [最低24h量$,默认300]
"""
import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 20
VOL_FLOOR = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0

def get(url, tries=3):
    import time
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            last = e; time.sleep(0.3)
    raise last

def fnum(x, d=0.0):
    try: return float(x)
    except: return d

# 深翻市场列表(按量倒序翻很多页, 把长尾也带进来)
markets = []
for off in range(0, PAGES*100, 100):
    page = get(f"https://gamma-api.polymarket.com/markets?closed=false&active=true&limit=100&offset={off}&order=volume24hr&ascending=false")
    if not page: break
    markets.extend(page)
    if len(page) < 100: break
print(f"拉取 {len(markets)} 个市场, 筛二元 + 有量 + 价差宽...\n")

rows = []
for m in markets:
    try:
        toks = json.loads(m.get("clobTokenIds") or "[]")
        outs = json.loads(m.get("outcomes") or "[]")
        prices = [fnum(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except Exception:
        continue
    if len(toks) != 2 or len(prices) != 2:
        continue
    if not m.get("acceptingOrders") or not m.get("enableOrderBook"):
        continue
    bb, ba = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
    spread = (ba - bb) if (ba > 0 and bb > 0 and ba > bb) else fnum(m.get("spread"))
    v24 = fnum(m.get("volume24hr"))
    px = prices[0]
    if not (0.05 <= px <= 0.95):   # 排除濒临 0/1 的死盘
        continue
    if v24 < VOL_FLOOR:            # 要有成交, 否则挂了也不成交
        continue
    if not (0.02 <= spread <= 0.30):  # 太窄=没边际; 太宽=基本是死/停盘
        continue
    rows.append({
        "q": (m.get("question") or m.get("slug") or "")[:50],
        "spread": spread, "v24": v24, "px": px,
        "edge_pair": spread,         # maker 两腿挂touch的每对边际 ≈ 价差
    })

rows.sort(key=lambda r: -r["spread"])
print("== 价差最宽(maker 边际最肥)且有成交的二元盘 TOP 30 ==")
print(f"{'价差(分)':>8}{'≈每对边际(分)':>13}{'24h量$':>11}{'YES价':>7}  市场")
for r in rows[:30]:
    print(f"{r['spread']*100:>8.1f}{r['edge_pair']*100:>13.1f}{r['v24']:>11,.0f}{r['px']:>7.2f}  {r['q']}")

# 分布概览
import statistics
if rows:
    sp = [r["spread"] for r in rows]
    print(f"\n共 {len(rows)} 个合格盘。价差分布: 中位 {statistics.median(sp)*100:.1f}分 / "
          f">5分的有 {sum(1 for x in sp if x>0.05)} 个 / >10分的有 {sum(1 for x in sp if x>0.10)} 个")
    print("读法: '≈每对边际'是你两腿都挂最优买价、都成交时的毛边际(还没扣 fee/滑点/排队失败)。")
    print("      边际宽的盘往往成交慢——肥但难接到单; 要在'边际够厚'和'还有成交'之间找平衡点。")
else:
    print("没筛到合格盘(放宽 VOL_FLOOR 或价差区间再试)。")
