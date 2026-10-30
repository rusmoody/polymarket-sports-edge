"""
Сбор данных для бэктеста на РЕАЛЬНЫХ ценах Polymarket.
Для каждого закрытого матча НБА на Polymarket:
  - находим матч в ESPN, берём время и счёт на перерыве и после 3-й четверти, финал;
  - из истории цен Polymarket берём цены всех исходов (манилайн + форы):
      до игры, на перерыве, после 3-й четверти.
Результат: games_pm.jsonl (одна строка = один матч).

  python collect.py
"""
import json, os, datetime as dt, statistics, sys
from concurrent.futures import ThreadPoolExecutor
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache"); os.makedirs(CACHE, exist_ok=True)
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
S = requests.Session()


def jget(url, params=None, tries=4):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                import time; time.sleep(2 + 2 * i); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            import time; time.sleep(1 + i)


def cached(name, fn):
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        return json.load(open(p))
    v = fn()
    json.dump(v, open(p, "w"))
    return v


def ts(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T").replace("+00:00:00", "+00:00")).timestamp())


def pm_games():
    def fetch():
        out, off = [], 0
        while True:
            js = jget(f"{GAMMA}/events", {"series_id": 10345, "closed": "true", "limit": 100,
                                          "offset": off, "order": "startDate", "ascending": "true"})
            if not js:
                break
            out += js; off += len(js)
            if len(js) < 100:
                break
        keep = []
        for e in out:
            if e.get("slug", "").count("-") != 5:
                continue
            mk = [m for m in e["markets"] if m.get("sportsMarketType") in ("moneyline", "spreads")]
            ml = [m for m in mk if m["sportsMarketType"] == "moneyline"]
            if not ml or not ml[0].get("gameStartTime"):
                continue
            keep.append({
                "slug": e["slug"],
                "start": ml[0]["gameStartTime"].replace(" ", "T").replace("+00", "+00:00"),
                "markets": [{"type": m["sportsMarketType"], "q": m["question"], "line": m.get("line"),
                             "outcomes": json.loads(m["outcomes"]), "tokens": json.loads(m["clobTokenIds"]),
                             "final": json.loads(m.get("outcomePrices") or "[]")} for m in mk],
            })
        return keep
    return cached("pm_games.json", fetch)


def espn_day(day):
    return cached(f"sb_{day}.json", lambda: jget(f"{ESPN}/scoreboard", {"dates": day}).get("events", []))


def espn_game(g):
    """Ищем матч в ESPN по названиям команд и дате (по времени ET)."""
    start = dt.datetime.fromisoformat(g["start"])
    ml = next(m for m in g["markets"] if m["type"] == "moneyline")
    teams = {o.lower() for o in ml["outcomes"]}
    for shift in (-5, -4, 0):
        day = (start + dt.timedelta(hours=shift)).strftime("%Y%m%d")
        for e in espn_day(day):
            c = e["competitions"][0]
            names = {(t["team"].get("shortDisplayName") or t["team"].get("name") or "").lower() for t in c["competitors"]}
            if names == teams and abs(ts(e["date"]) - start.timestamp()) < 6 * 3600:
                return e
    return None


def espn_detail(eid):
    def fetch():
        s = jget(f"{ESPN}/summary", {"event": eid})
        comp = s["header"]["competitions"][0]
        t = {x["homeAway"]: x for x in comp["competitors"]}
        plays = s.get("plays", [])
        wp = s.get("winprobability") or []
        nm = lambda x: x["team"].get("shortDisplayName") or x["team"].get("name")
        res = {"home": nm(t["home"]), "away": nm(t["away"]),
               "hf": int(t["home"]["score"] or 0), "af": int(t["away"]["score"] or 0),
               "completed": comp["status"]["type"].get("completed", False),
               "season_type": s["header"].get("season", {}).get("type"),
               "pre_wp_home": wp[0]["homeWinPercentage"] if wp else None}
        for per, tag in [(2, "ht"), (3, "q3")]:
            pp = [p for p in plays if p["period"]["number"] == per and p.get("wallclock")]
            if pp:
                p = pp[-1]
                res[tag] = {"hs": p["homeScore"], "as": p["awayScore"], "ts": ts(p["wallclock"])}
        return res
    return cached(f"g_{eid}.json", fetch)


def price_at(token, t0, t1):
    """Средняя цена токена в окне [t0, t1] (история цен Polymarket, шаг 1 мин)."""
    h = jget(f"{CLOB}/prices-history", {"market": token, "startTs": t0, "endTs": t1, "fidelity": 1}).get("history", [])
    ps = [float(x["p"]) for x in h if t0 <= int(x["t"]) <= t1]
    return round(statistics.median(ps), 4) if ps else None


def last_before(token, t):
    h = jget(f"{CLOB}/prices-history", {"market": token, "startTs": t - 3 * 3600, "endTs": t, "fidelity": 1}).get("history", [])
    ps = [float(x["p"]) for x in h if int(x["t"]) <= t]
    return ps[-1] if ps else None


def process(g):
    path = os.path.join(CACHE, f"r_{g['slug']}.json")
    if os.path.exists(path):
        return json.load(open(path))
    try:
        e = espn_game(g)
        if not e:
            return None
        d = espn_detail(e["id"])
        if not d.get("completed") or "ht" not in d or "q3" not in d:
            return None
        st = ts(g["start"])
        rows = []
        for m in g["markets"]:
            for i, (o, tok) in enumerate(zip(m["outcomes"], m["tokens"])):
                if m["type"] == "moneyline":
                    h = 0.0
                else:
                    line = float(m["line"] or 0)
                    h = line if i == 0 else -line
                rows.append({
                    "type": m["type"], "team": o, "handicap": h,
                    "pre": last_before(tok, st) if m["type"] == "moneyline" else None,
                    # перерыв длится ~15 мин: берём медиану первых 8 минут после конца 2-й четверти
                    "ht": price_at(tok, d["ht"]["ts"], d["ht"]["ts"] + 480),
                    # после 3-й четверти пауза ~2–3 мин
                    "q3": price_at(tok, d["q3"]["ts"], d["q3"]["ts"] + 150),
                })
        r = {"slug": g["slug"], "espn_id": e["id"], **d, "start": g["start"], "prices": rows}
        json.dump(r, open(path, "w"))
        return r
    except Exception as ex:
        print("ERR", g["slug"], ex, file=sys.stderr)
        return None


def main():
    games = pm_games()
    print("Матчей на Polymarket:", len(games))
    done = 0
    with ThreadPoolExecutor(6) as ex, open(os.path.join(HERE, "games_pm.jsonl"), "w") as f:
        for r in ex.map(process, games):
            done += 1
            if r:
                f.write(json.dumps(r) + "\n")
            if done % 100 == 0:
                print(f"  {done}/{len(games)}", flush=True)
    print("Готово")


if __name__ == "__main__":
    main()
