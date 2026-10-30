"""
Проверка live-подключения к Polymarket БЕЗ ставок.
Читает PRIVATE_KEY / FUNDER / SIGNATURE_TYPE из .env, проверяет геоблок IP сервера,
получает API-ключи и показывает баланс pUSD на аккаунте.

  python livecheck.py
"""
import os, sys, requests

HERE = os.path.dirname(os.path.abspath(__file__))
for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

gb = requests.get("https://polymarket.com/api/geoblock", timeout=15).json()
print("Геоблок IP сервера:", gb)
if gb.get("blocked"):
    sys.exit("IP сервера заблокирован — live невозможен.")

for k in ("PRIVATE_KEY", "FUNDER"):
    if not os.environ.get(k):
        sys.exit(f"В .env не заполнен {k}")

from py_clob_client_v2 import ClobClient, BalanceAllowanceParams, AssetType

c = ClobClient("https://clob.polymarket.com", chain_id=137,
               key=os.environ["PRIVATE_KEY"],
               signature_type=int(os.environ.get("SIGNATURE_TYPE", "1")),
               funder=os.environ["FUNDER"])
print("Адрес подписанта:", c.get_address())
print("FUNDER (кошелёк Polymarket):", os.environ["FUNDER"])

creds = c.create_or_derive_api_key()
c.set_api_creds(creds)
print("API-ключи получены ✅")

bal = c.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
raw = int(bal.get("balance", 0))
print(f"Баланс pUSD: {raw / 1e6:.2f}$")
print("Allowances:", bal.get("allowances") or bal.get("allowance"))
if raw == 0:
    print("⚠️ Баланс 0: либо деньги не заведены, либо FUNDER/SIGNATURE_TYPE указаны неверно.")
else:
    print("Готово к LIVE. Поставь START_BANKROLL равным балансу и LIVE=1 в .env.")
