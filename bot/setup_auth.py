"""
Phase 2 第一步 — 验证凭证链路(不下单、不动钱)。
你(用你自己的私钥)运行。它会:
  1) 用私钥连上 CLOB
  2) 生成/取回 API 凭证(打印出来, 你填回 .env)
  3) 查 USDC 余额 + 授权状态
全程只读, 不下任何单。
用法: python setup_auth.py
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def load_env(path=os.path.join(os.path.dirname(__file__), ".env")):
    env = {}
    if not os.path.exists(path):
        print(f"没找到 {path} —— 先把 .env.example 复制成 .env 并填好。"); sys.exit(1)
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k.strip()] = v.strip()
    return env

env = load_env()
PK = env.get("PK", "")
FUNDER = env.get("FUNDER", "")
SIG = int(env.get("SIG_TYPE", "1"))
if not PK or PK.startswith("0xYOUR"):
    print("PK 没填(一次性钱包私钥)。"); sys.exit(1)

from py_clob_client.client import ClobClient
HOST = "https://clob.polymarket.com"
CHAIN = 137  # Polygon

print(f"连接 CLOB... signature_type={SIG} funder={FUNDER[:10]}...")
try:
    if SIG == 0:
        client = ClobClient(HOST, key=PK, chain_id=CHAIN)
    else:
        client = ClobClient(HOST, key=PK, chain_id=CHAIN, signature_type=SIG, funder=FUNDER)
except Exception as e:
    print("连接失败:", e); sys.exit(1)

# 1) 生成/取回 API 凭证
try:
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    print("\n✅ API 凭证已就绪, 把下面三行填回 .env:")
    print("CLOB_API_KEY=", creds.api_key)
    print("CLOB_SECRET=", creds.api_secret)
    print("CLOB_PASSPHRASE=", creds.api_passphrase)
except Exception as e:
    print("生成凭证失败(检查 SIG_TYPE / FUNDER 是否匹配你的钱包类型):", e); sys.exit(1)

# 2) 查余额 + 授权
try:
    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    bal = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print("\n== USDC 余额/授权 ==")
    print(bal)
    print("\n说明: balance 是你能用的 USDC(6位小数, 1e6=1美元); allowance 是已授权给交易所的额度。")
    print("若 allowance=0, 用官方 app 先做一次小额操作/充值会自动设好授权, 再回来。")
except Exception as e:
    print("查余额失败(不影响凭证):", e)

print("\n下一步: 把上面三行凭证填回 .env, 然后 python live_trader.py (默认 dry-run, 不下真单)。")
