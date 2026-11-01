"""
NBA halftime comeback bot для Polymarket.

Идея: в перерыве отстающий на 10–19 очков (в примерно равной до игры паре) отыгрывается
чаще, чем закладывает модель. Бот:
  1. каждые N секунд смотрит лайв-счёт ESPN;
  2. на перерыве матча с отставанием MIN_DEF..MAX_DEF находит рынки Polymarket
     (манилайн + плюсовые форы на отстающего);
  3. считает «честную» вероятность по истории (games.csv, 4 сезона) — с консервативной
     поправкой (точечная оценка минус Z стандартных ошибок);
  4. сравнивает с ценой best ask + комиссия; если edge >= MIN_EDGE — ставит (или пишет в журнал);
  5. после матча сам рассчитывает результат и PnL.

Режимы:
  python bot.py selftest   — проверка доступа к ESPN/Polymarket, геоблок, тестовая оценка
  python bot.py run        — основной цикл (по умолчанию PAPER, без денег)
  python bot.py report     — статистика по журналу

Живые ставки включаются только через LIVE=1 в .env.
"""
import json, math, os, sqlite3, sys, time, datetime as dt
import requests
import pandas as pd

# ---------------------------------------------------------------- config
def env(k, d, cast=str):
    v = os.environ.get(k)
    return cast(v) if v not in (None, "") else d

def load_dotenv(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

LIVE = env("LIVE", 0, int) == 1
START_BANKROLL = env("START_BANKROLL", 200.0, float)
MIN_DEF, MAX_DEF = env("MIN_DEF", 10, int), env("MAX_DEF", 19, int)
EQUAL_ONLY = env("EQUAL_ONLY", 1, int) == 1          # только «равные до игры»
EQUAL_SOURCE = env("EQUAL_SOURCE", "pm")             # pm = по предматчевой цене Polymarket, espn = по модели ESPN
EQUAL_LO, EQUAL_HI = env("EQUAL_LO", 0.40, float), env("EQUAL_HI", 0.60, float)
MIN_EDGE = env("MIN_EDGE", 0.03, float)              # мин. перевес (в долях вероятности)
Z = env("Z", 1.0, float)                             # консервативность оценки
WINDOW = env("WINDOW", 2, int)                       # ±очков вокруг текущего отставания для истории
MIN_N = env("MIN_N", 60, int)                        # мин. похожих матчей в истории
MAX_HANDICAP = env("MAX_HANDICAP", 15.0, float)
STAKE_PCT = env("STAKE_PCT", 0.05, float)           # фиксированно 5% от текущего банка на ставку
MAX_STAKE = env("MAX_STAKE", 0.0, float)             # потолок $ на ставку, 0 = без потолка
MAX_BETS_PER_DAY = env("MAX_BETS_PER_DAY", 4, int)
MAX_DAILY_LOSS = env("MAX_DAILY_LOSS", 30.0, float)
FEE_RATE = env("FEE_RATE", 0.05, float)              # fee = rate*p*(1-p) за акцию, берём с запасом
POLL_SEC = env("POLL_SEC", 20, int)
NBA_ENABLED = env("NBA_ENABLED", 1, int) == 1      # 0 = не брать новые НБА-сигналы (открытые ставки всё равно досчитаются)
TP_ENABLED = env("TP_ENABLED", 1, int) == 1        # тейк-профит
TP_PCT = env("TP_PCT", 0.25, float)                # продать, когда чистый плюс >= 25% от вложенного
TENNIS_TP_PCT = env("TENNIS_TP_PCT", 0.25, float)  # тейк-профит для тенниса отдельно (0 = держать до конца матча)

# Теннис: андердог (<40% до матча) взял 1-й сет -> покупаем его победу в матче
TENNIS_ENABLED = env("TENNIS_ENABLED", 1, int) == 1
TENNIS_DOG_MAX = env("TENNIS_DOG_MAX", 0.40, float)  # «андердог» = до матча стоил меньше 40%
TENNIS_MIN_ASK = env("TENNIS_MIN_ASK", 0.30, float)  # не покупать дешевле/дороже этих цен
TENNIS_MAX_ASK = env("TENNIS_MAX_ASK", 0.85, float)
TENNIS_MAX_PER_DAY = env("TENNIS_MAX_PER_DAY", 6, int)
TENNIS_POLL_SEC = env("TENNIS_POLL_SEC", 15, int)
TENNIS_MAX_DELAY_MIN = env("TENNIS_MAX_DELAY_MIN", 4, int)   # входим только в первые N минут после 1-го сета (как в бэктесте)
TENNIS_SET_DONE = env("TENNIS_SET_DONE", 0.97, float)        # 1-й сет считаем решённым, когда его рынок >= 0.97
TENNIS_BIG_ONLY = env("TENNIS_BIG_ONLY", 1, int) == 1  # только крупные турниры (как в бэктесте)
TENNIS_INCLUDE_500 = env("TENNIS_INCLUDE_500", 1, int) == 1  # плюс турниры категории 500 (ATP/WTA)
TENNIS_INCLUDE_250 = env("TENNIS_INCLUDE_250", 1, int) == 1  # плюс турниры категории 250 (ATP/WTA), отдельная строка в отчёте
TENNIS_SERIES = (10365, 10366)                        # ATP, WTA
TENNIS_BIG = ["Australian Open", "Roland Garros", "French Open", "Wimbledon", "US Open", "Indian Wells",
              "BNP Paribas Open", "Miami Open", "Monte-Carlo", "Monte Carlo", "Madrid", "Internazionali",
              "Italian Open", "National Bank Open", "Canadian Open", "Cincinnati", "Shanghai", "Paris Masters",
              "Rolex Paris", "China Open", "Wuhan", "Dubai", "Qatar", "Finals"]
# Турниры категории 500 (ATP и WTA). На них бэктеста не было — в отчёте идут отдельной строкой.
# Списки раздельные: одно и то же название города у мужчин и женщин бывает разной категории
# (например, Нинбо — WTA 500, но у мужчин там Челленджер).
TENNIS_500 = {
    10365: ["Rotterdam", "ABN AMRO", "Rio Open", "Acapulco", "Abierto Mexicano", "Barcelona", "Queen's", "Queens",
            "cinch", "Halle", "Terra Wortmann", "Hamburg", "Washington", "Citi Open", "DC Open", "Mubadala",
            "Japan Open Tennis", "Kinoshita", "Erste Bank", "Vienna", "Swiss Indoors", "Basel", "Dallas",
            "BMW Open", "Munich"],
    10366: ["Adelaide", "Brisbane", "Abu Dhabi", "Linz", "Charleston", "Stuttgart", "Porsche", "Strasbourg",
            "Berlin", "Bad Homburg", "Eastbourne", "Queen's", "Queens", "HSBC Championships", "Washington",
            "Citi Open", "DC Open", "Mubadala", "Monterrey", "Korea Open", "Seoul", "Pan Pacific", "Toray",
            "Ningbo", "Merida", "San Diego", "Zhengzhou"],
}
# Турниры категории 250. Список положительный (а не «всё остальное»), потому что Челленджеры
# на Polymarket лежат в той же серии ATP и в названии слова «Challenger» часто нет.
TENNIS_250 = {
    10365: ["Brisbane International", "Hong Kong Tennis Open", "Adelaide International", "ASB Classic",
            "Open Occitanie", "Montpellier", "Delray Beach", "Open 13", "Marseille", "Argentina Open",
            "Chile Open", "Los Cabos", "Mexican Open", "U.S. Men's Clay Court", "Houston", "Hassan II",
            "Marrakech", "Tiriac", "Bucharest", "Geneva", "Gonet Geneva", "BOSS Open", "Stuttgart",
            "Libema", "Hertogenbosch", "Mallorca", "Eastbourne", "Nordea Open", "Bastad", "Swiss Open",
            "Gstaad", "Generali Open", "Kitzbuhel", "Croatia Open", "Umag", "Atlanta Open", "Winston-Salem",
            "Chengdu Open", "Hangzhou Open", "Almaty Open", "Nordic Open", "Stockholm", "European Open",
            "Antwerp", "Open de Moselle", "Metz", "Hellenic Championship", "Athens", "Belgrade Open"],
    10366: ["ASB Classic", "Hobart International", "Thailand Open", "Hua Hin", "Transylvania Open",
            "ATX Open", "Austin", "Copa Colsanitas", "Bogota", "Rouen", "Morocco Open", "Rabat",
            "Libema", "Hertogenbosch", "Nottingham", "Rothesay", "Birmingham", "Iasi", "Palermo",
            "Hamburg", "Prague", "Livesport Prague", "Tennis in the Land", "Cleveland", "Jasmin Open",
            "Monastir", "Guadalajara", "Prudential Hong Kong", "Japan Women's Open", "Osaka",
            "Jiangxi Open", "Guangzhou Open", "Chennai Open", "Singapore Tennis Open", "Kaohsiung"],
}
TENNIS_EXCLUDE = ("Qualification", "Juniors", "Doubles", "Challenger", "ITF", "UTR", "Exhibition", " 125")
TG_TOKEN, TG_CHAT = env("TG_BOT_TOKEN", ""), env("TG_CHAT_ID", "")
# Публичный канал: сюда идут сигналы и итоги в формате «для людей» (бот должен быть админом канала)
TG_CHANNEL = env("TG_CHANNEL_ID", "")                 # @username канала или -100… id
CHANNEL_SUMMARY_HOUR = env("CHANNEL_SUMMARY_HOUR", 7, int)   # час (UTC) ежедневной сводки, 7 = 10:00 МСК
CHANNEL_ENTRY_SLACK = env("CHANNEL_ENTRY_SLACK", 0.03, float)  # «вход не дороже» = цена бота + запас

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "bets.db")
HIST_CSV = os.path.join(HERE, "games.csv")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
NBA_SERIES = 10345
S = requests.Session()


def log(msg):
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with open(os.path.join(HERE, "bot.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def tg(msg):
    if TG_TOKEN and TG_CHAT:
        try:
            S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                   json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
        except Exception as e:
            log(f"TG error: {e}")


def channel(msg):
    """Сообщение в публичный канал (если задан). Без ссылок-превью."""
    if TG_TOKEN and TG_CHANNEL:
        try:
            S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                   json={"chat_id": TG_CHANNEL, "text": msg, "disable_web_page_preview": True}, timeout=10)
        except Exception as e:
            log(f"Channel error: {e}")


def jget(url, params=None):
    r = S.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- история / честная вероятность
def load_history():
    d = pd.read_csv(HIST_CSV)
    d = d[d["ht_diff_home"] != 0].copy()
    trail_home = d["ht_diff_home"] < 0
    d["deficit"] = d["ht_diff_home"].abs()
    fm = d["home_final"] - d["away_final"]
    d["trail_margin"] = fm.where(trail_home, -fm)
    d["trail_pre_wp"] = d["pre_wp_home"].where(trail_home, 1 - d["pre_wp_home"])
    d["equal"] = d["trail_pre_wp"].between(0.40, 0.60)
    d["seg"] = d["trail_pre_wp"].apply(segment)
    return d

def segment(pre):
    """Кем был отстающий до игры: eq = равные (40–60%), fav = фаворит (60–80%), other — остальное."""
    if pre is None:
        return None
    if 0.40 <= pre < 0.60:
        return "eq"
    if 0.60 <= pre <= 0.80:
        return "fav"
    return "other"

HIST = None

def fair_prob(deficit, handicap, seg=None):
    """P(отстающий + handicap > 0 по итогу) по похожим матчам. Возвращает (консерв., точечная, N).
    seg: 'eq' / 'fav' — только такие матчи из истории (по ESPN до игры), 'all' — все;
    None — 'eq' если EQUAL_ONLY, иначе 'all'."""
    if seg is None:
        seg = "eq" if EQUAL_ONLY else "all"
    d = HIST[HIST["deficit"].between(deficit - WINDOW, deficit + WINDOW)]
    if seg in ("eq", "fav"):
        d = d[d["seg"] == seg]
    n = len(d)
    if n == 0:
        return 0.0, 0.0, 0
    p = float((d["trail_margin"] + handicap > 0).mean())
    se = math.sqrt(max(p * (1 - p), 1e-9) / n)
    return max(0.0, p - Z * se), p, n


# ---------------------------------------------------------------- ESPN
def espn_games():
    return jget(f"{ESPN}/scoreboard").get("events", [])

def parse_espn(e):
    c = e["competitions"][0]
    t = {x["homeAway"]: x for x in c["competitors"]}
    st = e["status"]
    return {
        "id": e["id"],
        "start": e["date"],
        "status": st["type"]["name"],
        "state": st["type"]["state"],
        "period": st.get("period", 0),
        "clock": st.get("clock", 0),
        "completed": st["type"].get("completed", False),
        "home": t["home"]["team"]["shortDisplayName"],
        "away": t["away"]["team"]["shortDisplayName"],
        "hs": int(t["home"].get("score") or 0),
        "as": int(t["away"].get("score") or 0),
    }

def is_halftime(g):
    return g["status"] == "STATUS_HALFTIME" or (
        g["state"] == "in" and g["period"] == 2 and float(g["clock"] or 0) == 0.0)

def pregame_wp_home(eid):
    try:
        wp = jget(f"{ESPN}/summary", {"event": eid}).get("winprobability") or []
        return wp[0]["homeWinPercentage"] if wp else None
    except Exception:
        return None

def espn_final(eid):
    s = jget(f"{ESPN}/summary", {"event": eid})
    comp = s["header"]["competitions"][0]
    if not comp["status"]["type"].get("completed"):
        return None
    t = {x["homeAway"]: x for x in comp["competitors"]}
    # в summary у команды бывает только "name" без "shortDisplayName" — берём что есть
    nm = lambda x: x["team"].get("shortDisplayName") or x["team"].get("name") or x["team"].get("displayName")
    return {nm(t["home"]): int(t["home"]["score"]), nm(t["away"]): int(t["away"]["score"])}


# ---------------------------------------------------------------- Polymarket
def pm_event_markets(home, away, start_iso):
    """Рынки Polymarket на матч: moneyline + spreads. Матчим по названиям команд и времени старта."""
    start = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    evs = jget(f"{GAMMA}/events", {"series_id": NBA_SERIES, "active": "true",
                                   "closed": "false", "limit": 200})
    for ev in evs:
        mkts = ev.get("markets", [])
        ml = [m for m in mkts if m.get("sportsMarketType") == "moneyline"]
        if not ml:
            continue
        outs = {o.lower() for o in json.loads(ml[0]["outcomes"])}
        if {home.lower(), away.lower()} != outs:
            continue
        gst = ml[0].get("gameStartTime")
        if gst:
            g = dt.datetime.fromisoformat(gst.replace(" ", "T").replace("+00", "+00:00"))
            if abs((g - start).total_seconds()) > 6 * 3600:
                continue
        return [m for m in mkts if m.get("sportsMarketType") in ("moneyline", "spreads")
                and m.get("acceptingOrders") and not m.get("closed")]
    return []

def candidates(markets, trail_team):
    """(market, token_id, handicap) для ставок на отстающего с handicap >= 0."""
    out = []
    for m in markets:
        outs = json.loads(m["outcomes"])
        toks = json.loads(m["clobTokenIds"])
        names = [o.lower() for o in outs]
        if trail_team.lower() not in names:
            continue
        i = names.index(trail_team.lower())
        if m["sportsMarketType"] == "moneyline":
            h = 0.0
        else:
            line = float(m.get("line") or 0)
            h = line if i == 0 else -line   # line относится к первому исходу
        if 0 <= h <= MAX_HANDICAP:
            out.append((m, toks[i], h))
    return out

def pm_pregame_price(token_id, start_iso):
    """Последняя цена токена перед стартом матча (из истории цен Polymarket)."""
    start = int(dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00")).timestamp())
    h = jget(f"{CLOB}/prices-history", {"market": token_id, "startTs": start - 3 * 3600,
                                        "endTs": start, "fidelity": 1}).get("history", [])
    # API добавляет в конец текущую цену, даже если она позже endTs — отбрасываем всё после старта
    h = [x for x in h if int(x["t"]) <= start]
    return float(h[-1]["p"]) if h else None

def best_ask(token_id):
    b = jget(f"{CLOB}/book", {"token_id": token_id})
    asks = sorted(((float(a["price"]), float(a["size"])) for a in b.get("asks", [])))
    if not asks:
        return None
    return {"price": asks[0][0], "size": asks[0][1], "tick": b.get("tick_size", "0.01"),
            "neg_risk": bool(b.get("neg_risk")), "min_size": float(b.get("min_order_size") or 5)}

def fee_rate(market):
    sched = market.get("feeSchedule") or {}
    try:
        r = float(sched.get("rate") or 0)
    except Exception:
        r = 0
    return max(r, FEE_RATE)


# ---------------------------------------------------------------- БД
def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS bets(
        id INTEGER PRIMARY KEY, ts TEXT, day TEXT, espn_id TEXT, game TEXT, team TEXT,
        market TEXT, handicap REAL, deficit INT, pre_wp REAL, ask REAL, fee_ps REAL,
        fair_lb REAL, fair_pt REAL, n_hist INT, edge REAL, shares REAL, cost REAL,
        mode TEXT, order_resp TEXT, status TEXT DEFAULT 'open', pnl REAL)""")
    con.execute("CREATE TABLE IF NOT EXISTS seen(espn_id TEXT PRIMARY KEY, ts TEXT, note TEXT)")
    for col, typ in [("tp_price", "REAL"), ("tp_ts", "TEXT"), ("pnl_hold", "REAL"), ("tp_resp", "TEXT"),
                     ("tp_proceeds", "REAL"), ("shares_left", "REAL"),
                     ("seg", "TEXT"), ("sport", "TEXT")]:
        try:
            con.execute(f"ALTER TABLE bets ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    return con

def bankroll(con):
    settled = con.execute("SELECT COALESCE(SUM(pnl),0) FROM bets WHERE pnl IS NOT NULL").fetchone()[0]
    open_cost = con.execute("SELECT COALESCE(SUM(cost),0) FROM bets WHERE pnl IS NULL").fetchone()[0]
    return START_BANKROLL + settled - open_cost

def today_stats(con, day, sport="nba"):
    if sport == "tennis":
        n = con.execute("SELECT COUNT(*) FROM bets WHERE day=? AND sport='tennis'", (day,)).fetchone()[0]
    else:
        n = con.execute("SELECT COUNT(*) FROM bets WHERE day=? AND COALESCE(sport,'nba')='nba'", (day,)).fetchone()[0]
    loss = con.execute("SELECT COALESCE(SUM(pnl),0) FROM bets WHERE day=? AND pnl IS NOT NULL", (day,)).fetchone()[0]
    return n, loss


# ---------------------------------------------------------------- ордера
_client = None
def clob_client():
    global _client
    if _client is None:
        from py_clob_client_v2 import ClobClient
        _client = ClobClient(CLOB, chain_id=137, key=os.environ["PRIVATE_KEY"],
                             signature_type=int(os.environ.get("SIGNATURE_TYPE", "1")),
                             funder=os.environ["FUNDER"])
        _client.set_api_creds(_client.create_or_derive_api_key())
    return _client

def place_order(token_id, price, shares, tick, neg_risk):
    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions
    from py_clob_client_v2.order_builder.constants import BUY
    c = clob_client()
    return c.create_and_post_order(
        OrderArgs(token_id=token_id, price=price, size=shares, side=BUY),
        options=PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg_risk),
        order_type=OrderType.FAK)   # забрать что есть по этой цене, остаток отменить


def best_bid(token_id):
    b = jget(f"{CLOB}/book", {"token_id": token_id})
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    if not bids:
        return None
    return {"price": bids[0][0], "size": bids[0][1], "tick": b.get("tick_size", "0.01"),
            "neg_risk": bool(b.get("neg_risk"))}

def live_shares(token_id, fallback):
    """Реальный остаток акций (на покупке комиссия берётся акциями, поэтому их чуть меньше заказанного)."""
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        r = clob_client().get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
        return math.floor(int(r["balance"]) / 1e6 * 100) / 100
    except Exception as e:
        log(f"Не смог получить баланс акций: {e}")
        return fallback

def sell_order(token_id, price, shares, tick, neg_risk):
    from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions
    from py_clob_client_v2.order_builder.constants import SELL
    return clob_client().create_and_post_order(
        OrderArgs(token_id=token_id, price=price, size=shares, side=SELL),
        options=PartialCreateOrderOptions(tick_size=str(tick), neg_risk=neg_risk),
        order_type=OrderType.FAK)

def check_take_profit(con, live_ids):
    """Для открытых ставок в идущих матчах: если продажа по best bid даёт >= TP_PCT чистыми — продаём."""
    if not TP_ENABLED:
        return
    rows = con.execute("""SELECT id, espn_id, game, team, handicap, shares, cost, fee_ps, ask, tp_resp,
                                 COALESCE(tp_proceeds,0), shares_left
                          FROM bets WHERE status='open'""").fetchall()
    for bid_id, eid, game, team, h, shares, cost, fee_ps, ask, meta, proceeds, left in rows:
        if eid not in live_ids and not str(eid).startswith("ten:"):
            continue
        tok = json.loads(meta or "{}").get("token")
        if not tok:
            continue
        try:
            bb = best_bid(tok)
        except Exception:
            continue
        if not bb:
            continue
        p = bb["price"]
        rate = fee_ps / (ask * (1 - ask)) if 0 < ask < 1 else FEE_RATE
        net = p - rate * p * (1 - p)                     # сколько чистыми за акцию при продаже
        if left is None:
            # в LIVE на покупке комиссия списывается акциями — берём реальный баланс
            left = live_shares(tok, shares * (1 - fee_ps / ask)) if LIVE else shares
        tp = TENNIS_TP_PCT if str(eid).startswith("ten:") else TP_PCT
        if tp <= 0 or proceeds + left * net - cost < tp * cost:
            continue
        sz = math.floor(min(left, bb["size"]) * 100) / 100
        if sz <= 0:
            continue
        if LIVE:
            try:
                sell_order(tok, p, sz, bb["tick"], bb["neg_risk"])
            except Exception as e:
                log(f"Ошибка продажи {game}: {e}"); tg(f"❌ Ошибка продажи {game}: {e}")
                continue
            remaining = live_shares(tok, left - sz)
        else:
            remaining = round(left - sz, 2)
        sold = left - remaining
        proceeds += sold * net
        if remaining < 0.01:
            pnl = round(proceeds - cost, 4)
            con.execute("""UPDATE bets SET status='closed_tp', pnl=?, tp_price=?, tp_ts=?, tp_proceeds=?, shares_left=0
                           WHERE id=?""", (pnl, p, dt.datetime.now().isoformat(), proceeds, bid_id))
            msg = (f"💰 TP {'LIVE' if LIVE else 'PAPER'} {game}: {team} +{h} продано по {p:.2f} "
                   f"(вход {ask:.2f}). PnL {pnl:+.2f}$ ({pnl/cost:+.0%}). Банк ~{bankroll(con):.2f}$")
            channel(f"💰 TAKE PROFIT (paper)\n{game}: {team}\n"
                    f"Sold at {p:.2f} (entry {ask:.2f}) · {pnl/cost:+.0%} · {pnl:+.2f}$\n"
                    f"Bankroll: ${bankroll(con):.2f}\n#paper #results")
        else:
            con.execute("UPDATE bets SET tp_price=?, tp_proceeds=?, shares_left=? WHERE id=?",
                        (p, proceeds, remaining, bid_id))
            msg = f"⚠️ {game}: продано {sold:.2f} акций по {p:.2f}, осталось {remaining:.2f} — продолжаю продавать"
        con.commit()
        log(msg); tg(msg)


# ---------------------------------------------------------------- логика
def evaluate(g, pre_wp_home, markets):
    """Возвращает лучший кандидат или None + текст причины."""
    diff = g["hs"] - g["as"]
    deficit = abs(diff)
    if diff == 0 or not (MIN_DEF <= deficit <= MAX_DEF):
        return None, f"отставание {deficit} вне {MIN_DEF}-{MAX_DEF}"
    trail = g["home"] if diff < 0 else g["away"]
    if not markets:
        return None, "рынки Polymarket не найдены"
    espn_pre = None
    if pre_wp_home is not None:
        espn_pre = pre_wp_home if diff < 0 else 1 - pre_wp_home
    pm_pre = None
    try:
        ml = next(m for m in markets if m["sportsMarketType"] == "moneyline")
        names = [o.lower() for o in json.loads(ml["outcomes"])]
        pm_pre = pm_pregame_price(json.loads(ml["clobTokenIds"])[names.index(trail.lower())], g["start"])
    except Exception as e:
        log(f"  не смог получить предматчевую цену Polymarket: {e}")
    trail_pre = pm_pre if EQUAL_SOURCE == "pm" else espn_pre
    if trail_pre is None:          # запасной вариант
        trail_pre = espn_pre if EQUAL_SOURCE == "pm" else pm_pre
    log(f"  {trail} до игры: Polymarket {pm_pre}, ESPN {espn_pre} (фильтр по {EQUAL_SOURCE})")
    if EQUAL_ONLY and (trail_pre is None or not (EQUAL_LO <= trail_pre <= EQUAL_HI)):
        return None, f"не равные до игры ({trail} до игры: PM {pm_pre}, ESPN {espn_pre})"
    # История разбита по ESPN. Берём сегмент и по цене Polymarket, и по ESPN, и считаем
    # честную вероятность по более пессимистичному из них (консервативно).
    segs = {sg if sg in ("eq", "fav") else "all" for sg in (segment(pm_pre), segment(espn_pre)) if sg}
    segs = segs or {"all"}
    seg_label = segment(trail_pre)
    best = None
    for m, tok, h in candidates(markets, trail):
        ests = [fair_prob(deficit, h, sg) for sg in segs]
        ests = [e for e in ests if e[2] >= MIN_N]
        if not ests:
            continue
        lb, pt, n = min(ests, key=lambda e: e[0])
        ba = best_ask(tok)
        if not ba or ba["price"] >= 0.99:
            continue
        p = ba["price"]
        fee_ps = fee_rate(m) * p * (1 - p)
        cost_ps = p + fee_ps
        edge = lb - cost_ps
        row = dict(team=trail, market=m["question"], handicap=h, deficit=deficit, pre_wp=trail_pre, seg=seg_label,
                   ask=p, fee_ps=fee_ps, fair_lb=lb, fair_pt=pt, n_hist=n, edge=edge,
                   token=tok, book=ba)
        log(f"  {trail} +{h:<5} [{m['question']}] ask={p:.2f} fee={fee_ps:.3f} "
            f"fair={pt:.3f} (lb {lb:.3f}, N={n}) edge={edge:+.3f}")
        if best is None or edge > best["edge"]:
            best = row
    if best is None:
        return None, "нет подходящих рынков/данных"
    if best["edge"] < MIN_EDGE:
        return None, f"лучший edge {best['edge']:+.3f} < {MIN_EDGE}"
    return best, "ok"

def size_bet(best, bank):
    """Фиксированная доля банка: STAKE_PCT от текущего депозита (вкл. уже рассчитанные PnL)."""
    p = best["ask"] + best["fee_ps"]
    stake = STAKE_PCT * bank
    if MAX_STAKE > 0:
        stake = min(stake, MAX_STAKE)
    stake = min(stake, bank)
    shares = math.floor(stake / p * 100) / 100
    shares = min(shares, best["book"]["size"])
    if shares < best["book"]["min_size"]:          # Polymarket не примет меньше 5 акций
        shares = best["book"]["min_size"]
        if shares * p > bank:
            return 0, 0
    return shares, round(shares * p, 4)

def handle_halftime(con, g):
    game = f"{g['away']} @ {g['home']}"
    log(f"Перерыв: {game} {g['as']}:{g['hs']}")
    day = dt.date.today().isoformat()
    n_today, pnl_today = today_stats(con, day)
    if n_today >= MAX_BETS_PER_DAY:
        return "лимит ставок на день"
    if pnl_today <= -MAX_DAILY_LOSS:
        return "дневной стоп-лосс"
    pre = pregame_wp_home(g["id"])
    try:
        markets = pm_event_markets(g["home"], g["away"], g["start"])
    except Exception as e:
        return f"ошибка Polymarket: {e}"
    best, why = evaluate(g, pre, markets)
    if not best:
        return why
    bank = bankroll(con)
    shares, cost = size_bet(best, bank)
    if shares <= 0:
        return "размер ставки 0 (банк/лимиты)"
    mode, resp = "paper", ""
    if LIVE:
        try:
            r = place_order(best["token"], best["ask"], shares, best["book"]["tick"], best["book"]["neg_risk"])
            resp, mode = json.dumps(r, default=str)[:1000], "live"
        except Exception as e:
            log(f"Ошибка ордера: {e}")
            tg(f"❌ Ошибка ордера {game}: {e}")
            return f"ошибка ордера: {e}"
    cost_total = round(shares * (best["ask"] + best["fee_ps"]), 4)
    con.execute("""INSERT INTO bets(ts,day,espn_id,game,team,market,handicap,deficit,pre_wp,ask,fee_ps,
        fair_lb,fair_pt,n_hist,edge,shares,cost,mode,order_resp,tp_resp,seg,sport) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'nba')""",
        (dt.datetime.now().isoformat(), day, g["id"], game, best["team"], best["market"], best["handicap"],
         best["deficit"], best["pre_wp"], best["ask"], best["fee_ps"], best["fair_lb"], best["fair_pt"],
         best["n_hist"], best["edge"], shares, cost_total, mode, resp, json.dumps({"token": best["token"]}), best["seg"]))
    con.commit()
    msg = (f"{'🟢 LIVE' if mode == 'live' else '📝 PAPER'} {game}\n{best['team']} минус {best['deficit']} на перерыве\n"
           f"{best['market']} (фора +{best['handicap']})\n"
           f"до игры {best['pre_wp']:.0%} ({'фаворит' if best['seg'] == 'fav' else 'равные'})\nask {best['ask']:.2f} (кэф ~{1/best['ask']:.1f}), "
           f"честная {best['fair_pt']:.1%} (консерв. {best['fair_lb']:.1%})\n"
           f"edge {best['edge']:+.1%}, {shares} акций, ${cost_total}")
    log(msg.replace("\n", " | "))
    tg(msg)
    lim = min(0.97, best["ask"] + CHANNEL_ENTRY_SLACK)
    side = "pre-game favourite" if best["seg"] == "fav" else "even pre-game"
    bet = f"{best['team']} +{best['handicap']}" if best["handicap"] else f"{best['team']} to win"
    channel(f"🏀 PAPER SIGNAL · NBA · halftime\n"
            f"{game}: {best['team']} trail by {best['deficit']} ({side}, {best['pre_wp']:.0%} before tip-off)\n"
            f"Bet: {bet}\n"
            f"Price {best['ask']:.2f} (odds {1/best['ask']:.2f}) · max entry {lim:.2f}\n"
            f"Historical estimate: {best['fair_pt']:.0%} · edge {best['edge']:+.1%}\n"
            f"Size: 5% of bankroll (${cost_total:.2f})\n"
            f"#paper #NBA #Polymarket")
    return "ставка"

def settle(con):
    rows = con.execute("""SELECT id, espn_id, team, handicap, shares, cost, game, status, pnl,
                                 COALESCE(tp_proceeds,0), shares_left
                          FROM bets WHERE pnl_hold IS NULL""").fetchall()
    for bid, eid, team, h, shares, cost, game, status, pnl_done, proceeds, left in rows:
        if str(eid).startswith("ten:"):
            meta = json.loads(con.execute("SELECT tp_resp FROM bets WHERE id=?", (bid,)).fetchone()[0] or "{}")
            win = tennis_result(meta.get("cid"), meta.get("idx"))
            if win is None:
                continue
            fin = "победа в матче" if win else "проиграл матч"
        else:
            try:
                fin = espn_final(eid)
            except Exception as ex:
                log(f"Не смог рассчитать {game}: {ex}")
                continue
            if not fin or team not in fin:
                continue
            other = [v for k, v in fin.items() if k != team][0]
            win = fin[team] - other + h > 0
            fin = " – ".join(f"{k} {v}" for k, v in fin.items())
        pnl_hold = round((shares if win else 0) - cost, 4)
        if pnl_done is not None:
            pnl = pnl_done
        elif proceeds > 0:   # частично продали по TP, остаток досидел
            pnl = round(proceeds + (left or 0) * (1 if win else 0) - cost, 4)
            pnl_done = pnl
        else:
            pnl = pnl_hold
        con.execute("UPDATE bets SET status='settled', pnl=?, pnl_hold=? WHERE id=?", (pnl, pnl_hold, bid))
        con.commit()
        extra = f" (с TP: {pnl_done:+.2f}$, если бы держали: {pnl_hold:+.2f}$)" if pnl_done is not None else ""
        label = f"🎾 {team}" if str(eid).startswith("ten:") else f"{team} +{h}"
        msg = f"{'✅' if win else '❌'} {game}: {label} -> {fin}. PnL {pnl:+.2f}$.{extra} Банк ~{bankroll(con):.2f}$"
        log(msg); tg(msg)
        fin_en = ("won the match" if win else "lost the match") if str(eid).startswith("ten:") else \
            ("covered" if win else "did not cover") + f" (final {fin})"
        label_en = f"🎾 {team}" if str(eid).startswith("ten:") else (f"{team} +{h}" if h else f"{team} to win")
        if pnl_done is None:   # если был TP — результат уже опубликован в момент продажи
            channel(f"{'✅ WIN' if win else '❌ LOSS'} (paper)\n{game}: {label_en} → {fin_en}\n"
                    f"{pnl:+.2f}$ ({pnl/cost:+.0%}) · bankroll ${bankroll(con):.2f}\n#paper #results")
        else:
            channel(f"ℹ️ FINAL: {game} — {label_en} → {fin_en}\n"
                    f"We exited early via take-profit ({pnl_done:+.2f}$); holding would have been {pnl_hold:+.2f}$\n"
                    f"#paper #results")

# ---------------------------------------------------------------- теннис
def tennis_events():
    """Активные матчи ATP/WTA на Polymarket, у которых уже закрылся рынок «Победитель 1-го сета»."""
    out = []
    now = dt.datetime.now(dt.timezone.utc)
    for sid in TENNIS_SERIES:
        off = 0
        while True:
            js = jget(f"{GAMMA}/events", {"series_id": sid, "active": "true", "closed": "false", "limit": 100, "offset": off})
            for e in js:
                ml = next((m for m in e.get("markets", []) if m.get("sportsMarketType") == "moneyline"), None)
                s1 = next((m for m in e.get("markets", []) if m.get("sportsMarketType") == "tennis_first_set_winner"), None)
                if not ml or not s1 or ml.get("closed") or not ml.get("gameStartTime"):
                    continue
                tourn = ml["question"].split(":")[0]
                full_q = (e.get("title") or "") + " " + ml["question"] + " " + (e.get("slug") or "")
                if any(x.lower() in full_q.lower() for x in TENNIS_EXCLUDE):
                    continue
                if any(b in tourn for b in TENNIS_BIG):
                    level = "big"
                elif any(b in tourn for b in TENNIS_500.get(sid, [])):
                    level = "500"
                elif any(b in tourn for b in TENNIS_250.get(sid, [])):
                    level = "250"
                else:
                    level = "other"
                if TENNIS_BIG_ONLY and not (level == "big" or (level == "500" and TENNIS_INCLUDE_500)
                                            or (level == "250" and TENNIS_INCLUDE_250)):
                    continue
                start = dt.datetime.fromisoformat(ml["gameStartTime"].replace(" ", "T").replace("+00", "+00:00"))
                if start > now:
                    continue
                try:
                    p1 = [float(x) for x in json.loads(s1.get("outcomePrices") or "[]")]
                except Exception:
                    continue
                if len(p1) != 2 or max(p1) < TENNIS_SET_DONE:
                    continue                     # 1-й сет ещё идёт
                # не ждём официального закрытия рынка: как в бэктесте, сет решён при цене >= 0.97
                ct = s1.get("closedTime") if s1.get("closed") else None
                if ct:
                    closed_at = dt.datetime.fromisoformat(ct.replace(" ", "T").replace("Z", "+00:00").replace("+00", "+00:00").replace("+00:00:00", "+00:00"))
                    if (now - closed_at).total_seconds() > TENNIS_MAX_DELAY_MIN * 60:
                        continue                 # 1-й сет кончился давно — момент упущен
                out.append({"slug": e["slug"], "title": e.get("title") or ml["question"], "ml": ml, "s1": s1,
                            "s1_winner_idx": 0 if p1[0] > p1[1] else 1, "tourn": tourn, "level": level})
            off += len(js)
            if len(js) < 100:
                break
    return out

def tennis_result(cid, idx):
    """Итог матча по резолюции рынка Polymarket: True/False, None — ещё не решён."""
    if not cid or idx is None:
        return None
    m = jget(f"{CLOB}/markets/{cid}")
    toks = m.get("tokens") or []
    if not m.get("closed") or len(toks) <= idx or not any(t.get("winner") for t in toks):
        return None
    return bool(toks[idx].get("winner"))

def handle_tennis(con, t):
    ml, s1 = t["ml"], t["s1"]
    outs, toks = json.loads(ml["outcomes"]), json.loads(ml["clobTokenIds"])
    s1outs = json.loads(s1["outcomes"])
    w_short = s1outs[t["s1_winner_idx"]].lower()
    # в рынке 1-го сета короткие имена (фамилии) — ищем игрока в манилайне
    idx = next((i for i, o in enumerate(outs) if w_short in o.lower() or o.lower() in w_short), t["s1_winner_idx"])
    player = outs[idx]
    log(f"Теннис: {t['title']} — 1-й сет взял {player}")
    day = dt.date.today().isoformat()
    n_today, pnl_today = today_stats(con, day, "tennis")
    if n_today >= TENNIS_MAX_PER_DAY:
        return "лимит теннисных ставок на день"
    if pnl_today <= -MAX_DAILY_LOSS:
        return "дневной стоп-лосс"
    pre = pm_pregame_price(toks[idx], ml["gameStartTime"])
    if pre is None:
        return "нет предматчевой цены"
    if pre >= TENNIS_DOG_MAX:
        return f"{player} не андердог (до матча {pre:.0%})"
    ba = best_ask(toks[idx])
    if not ba:
        return "пустой стакан"
    p = ba["price"]
    if not (TENNIS_MIN_ASK <= p <= TENNIS_MAX_ASK):
        return f"цена {p:.2f} вне {TENNIS_MIN_ASK}-{TENNIS_MAX_ASK}"
    fee_ps = fee_rate(ml) * p * (1 - p)
    best = {"ask": p, "fee_ps": fee_ps, "book": ba}
    shares, _ = size_bet(best, bankroll(con))
    if shares <= 0:
        return "размер ставки 0 (банк/лимиты)"
    mode, resp = "paper", ""
    if LIVE:
        try:
            r = place_order(toks[idx], p, shares, ba["tick"], ba["neg_risk"])
            resp, mode = json.dumps(r, default=str)[:1000], "live"
        except Exception as e:
            tg(f"❌ Ошибка ордера {t['title']}: {e}")
            return f"ошибка ордера: {e}"
    cost_total = round(shares * (p + fee_ps), 4)
    meta = {"token": toks[idx], "cid": ml["conditionId"], "idx": idx}
    con.execute("""INSERT INTO bets(ts,day,espn_id,game,team,market,handicap,deficit,pre_wp,ask,fee_ps,
        fair_lb,fair_pt,n_hist,edge,shares,cost,mode,order_resp,tp_resp,seg,sport)
        VALUES(?,?,?,?,?,?,0,0,?,?,?,NULL,NULL,NULL,NULL,?,?,?,?,?,?,'tennis')""",
        (dt.datetime.now().isoformat(), day, "ten:" + t["slug"], t["title"], player, ml["question"],
         pre, p, fee_ps, shares, cost_total, mode, resp, json.dumps(meta),
         {"big": "tennis_dog", "500": "tennis_dog500", "250": "tennis_dog250"}.get(t["level"], "tennis_dog_other")))
    con.commit()
    lvl = {"big": "крупный турнир", "500": "турнир 500", "250": "турнир 250"}.get(t["level"], "другой турнир")
    msg = (f"🎾 {'🟢 LIVE' if mode == 'live' else '📝 PAPER'} {t['title']} ({lvl})\n"
           f"{player} — андердог (до матча {pre:.0%}) взял 1-й сет\n"
           f"победа в матче: ask {p:.2f} (кэф ~{1/p:.2f}), {shares} акций, ${cost_total}")
    log(msg.replace("\n", " | ")); tg(msg)
    opp = next((o for i, o in enumerate(outs) if i != idx), "opponent")
    lim = min(TENNIS_MAX_ASK, p + CHANNEL_ENTRY_SLACK)
    lvl_en = {"big": "Slam/1000", "500": "500", "250": "250"}.get(t["level"], "other")
    channel(f"🎾 PAPER SIGNAL · {t['tourn']} ({lvl_en})\n"
            f"Underdog {player} ({pre:.0%} pre-match) took set 1 vs {opp}\n"
            f"Bet: {player} to win the match\n"
            f"Price {p:.2f} (odds {1/p:.2f}) · max entry {lim:.2f}\n"
            f"Size: 5% of bankroll (${cost_total:.2f})\n"
            f"#paper #tennis #Polymarket")
    return "ставка"

_last_summary_day = None
def channel_summary_tick(con):
    """Раз в день — сводка в канал: вчера и всё время, по сегментам."""
    global _last_summary_day
    if not TG_CHANNEL:
        return
    now = dt.datetime.now(dt.timezone.utc)
    if now.hour != CHANNEL_SUMMARY_HOUR or _last_summary_day == now.date():
        return
    _last_summary_day = now.date()
    df = pd.read_sql("SELECT * FROM bets WHERE pnl IS NOT NULL", con)
    if df.empty:
        return
    y = (now.date() - dt.timedelta(days=1)).isoformat()
    names = {"eq": "NBA · even teams", "fav": "NBA · favourites", "tennis_dog": "Tennis · Slams/1000",
             "tennis_dog500": "Tennis · 500", "tennis_dog250": "Tennis · 250"}
    def line(x):
        return f"{len(x)} bets, {(x.pnl > 0).sum()} winners, {x.pnl.sum():+.2f}$ (ROI {x.pnl.sum() / x.cost.sum():+.1%})"
    yd = df[df.day == y]
    parts = [f"📊 Daily paper report · {y}", (line(yd) if len(yd) else "no bets yesterday"), "",
             f"All time: {line(df)}", f"Bankroll: ${bankroll(con):.2f} (start ${START_BANKROLL:.0f})"]
    for sg, x in df.groupby(df.seg.fillna("?")):
        parts.append(f"• {names.get(sg, sg)}: {line(x)}")
    parts += ["", "Paper trading: real Polymarket prices, no real money. Not financial advice.",
              "#paper #stats #Polymarket"]
    channel("\n".join(parts))


_last_tennis = 0.0
def tennis_tick(con):
    global _last_tennis
    if not TENNIS_ENABLED or time.time() - _last_tennis < TENNIS_POLL_SEC:
        return
    _last_tennis = time.time()
    for t in tennis_events():
        key = "ten:" + t["slug"]
        if con.execute("SELECT 1 FROM seen WHERE espn_id=?", (key,)).fetchone():
            continue
        note = handle_tennis(con, t)
        log(f"  -> {note}")
        if note.startswith("ошибка"):
            continue
        con.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?)", (key, dt.datetime.now().isoformat(), note))
        con.commit()


def run():
    global HIST
    HIST = load_history()
    con = db()
    log(f"Старт. Режим: {'LIVE' if LIVE else 'PAPER'}. Банк: {bankroll(con):.2f}$")
    tg(f"Бот запущен ({'LIVE' if LIVE else 'PAPER'}), банк {bankroll(con):.2f}$")
    while True:
        try:
            games = [parse_espn(e) for e in espn_games()]
            live_ids = {g["id"] for g in games if g["state"] == "in"}
            for g in games:
                if NBA_ENABLED and is_halftime(g) and not con.execute("SELECT 1 FROM seen WHERE espn_id=?", (g["id"],)).fetchone():
                    note = handle_halftime(con, g)
                    log(f"  -> {note}")
                    if note.startswith("ошибка"):
                        continue   # повторим на следующем круге
                    con.execute("INSERT OR IGNORE INTO seen VALUES(?,?,?)", (g["id"], dt.datetime.now().isoformat(), note))
                    con.commit()
            try:
                tennis_tick(con)
            except Exception as ex:
                log(f"Ошибка тенниса: {ex}")
            check_take_profit(con, live_ids)
            settle(con)
            try:
                channel_summary_tick(con)
            except Exception as ex:
                log(f"Ошибка сводки: {ex}")
        except Exception as ex:
            log(f"Ошибка цикла: {ex}")
        time.sleep(POLL_SEC)

def report():
    con = db()
    df = pd.read_sql("SELECT * FROM bets", con)
    if df.empty:
        print("Ставок пока нет."); return
    st = df[df.status == "settled"]
    print(df[["day", "game", "team", "handicap", "deficit", "ask", "fair_pt", "edge", "shares", "cost",
              "mode", "status", "tp_price", "pnl", "pnl_hold"]].to_string())
    if len(st):
        wins = (st.pnl > 0).sum()
        print(f"\nРассчитано: {len(st)}, побед {wins} ({wins/len(st):.0%}), "
              f"ожид. побед по модели {st.fair_pt.sum():.1f}")
        print(f"PnL (как торговали): {st.pnl.sum():+.2f}$ на оборот {st.cost.sum():.2f}$ (ROI {st.pnl.sum()/st.cost.sum():+.1%})")
        print(f"PnL (если держать всё до конца): {st.pnl_hold.sum():+.2f}$ (ROI {st.pnl_hold.sum()/st.cost.sum():+.1%})")
        if "seg" in st and st.seg.notna().any():
            for sg, x in st.groupby("seg"):
                nm = {"eq": "НБА равные", "fav": "НБА фавориты", "tennis_dog": "теннис (Шлемы/1000): андердог взял 1-й сет",
                      "tennis_dog500": "теннис (500): андердог взял 1-й сет",
                      "tennis_dog250": "теннис (250): андердог взял 1-й сет"}.get(sg, sg)
                print(f"  [{nm}] ставок {len(x)}, побед {(x.pnl_hold > 0).sum()}"
                      + (f" (ожид. {x.fair_pt.sum():.1f})" if x.fair_pt.notna().any() else "") + ", "
                              f"PnL {x.pnl.sum():+.2f}$ (ROI {x.pnl.sum()/x.cost.sum():+.1%})")
        tp = st[st.tp_price.notna()]
        if len(tp):
            print(f"Сработал TP: {len(tp)} раз. TP дал {tp.pnl.sum():+.2f}$ против {tp.pnl_hold.sum():+.2f}$ при удержании")
    print(f"Текущий банк: {bankroll(con):.2f}$")

def selftest():
    global HIST
    HIST = load_history()
    print("История:", len(HIST), "матчей")
    for dfc in (10, 12, 14, 16, 18):
        print(f"  минус {dfc}: " + ", ".join(
            f"+{h}: {fair_prob(dfc, h)[1]:.1%} (N={fair_prob(dfc, h)[2]})" for h in (0, 5.5, 9.5)))
    gb = jget("https://polymarket.com/api/geoblock")
    print("Геоблок Polymarket для этого IP:", gb)
    games = [parse_espn(e) for e in espn_games()]
    print(f"ESPN сегодня: {len(games)} матчей")
    if not games:
        return
    g = dict(games[0]); g["hs"], g["as"] = g["hs"] + 0, g["hs"] + 14   # фейк: хозяева минус 14
    print(f"Тестовая оценка (фейковый счёт, хозяева минус 14): {g['away']} @ {g['home']}")
    mk = pm_event_markets(g["home"], g["away"], g["start"])
    print(f"  найдено рынков Polymarket: {len(mk)}")
    global EQUAL_ONLY
    EQUAL_ONLY = False
    best, why = evaluate(g, None, mk)
    print("  итог:", why, "" if not best else f"-> {best['market']} edge {best['edge']:+.3f}")
    print("  (цены сейчас ПРЕДматчевые, поэтому edge тут не показателен — проверка только связки)")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    {"run": run, "report": report, "selftest": selftest}[cmd]()
