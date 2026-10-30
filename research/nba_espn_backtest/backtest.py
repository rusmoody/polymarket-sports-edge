"""
NBA comeback backtest.
Вопрос: если команды до игры примерно равны, а одна сильно проигрывает к перерыву,
как часто отстающий выигрывает / закрывает плюсовую фору? И какой коэффициент нужен для плюса?

Данные: публичный API ESPN (stats.nba.com часто режет запросы, ESPN работает стабильно).
  - счёт по ходу игры (play-by-play)
  - пред-матчевая и лайв вероятность победы по модели ESPN (как прокси «равенства» и «честной цены»)

Запуск:
  pip install requests pandas
  python backtest.py --seasons 2022 2023 2024 2025      # год = год окончания сезона (2025 = 2024-25)
Кэш складывается в ./cache, повторный запуск не качает заново.
"""
import argparse, json, os, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE, exist_ok=True)
S = requests.Session()


def get(url, params, retries=3):
    for i in range(retries):
        try:
            r = S.get(url, params=params, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == retries - 1:
                raise


def season_event_ids(season_end_year):
    """ID всех завершённых матчей регулярки сезона."""
    path = os.path.join(CACHE, f"ids_{season_end_year}.json")
    if os.path.exists(path):
        return json.load(open(path))
    start, end = dt.date(season_end_year - 1, 10, 1), dt.date(season_end_year, 4, 25)
    days = [(start + dt.timedelta(d)).strftime("%Y%m%d") for d in range((end - start).days + 1)]

    def day(d):
        try:
            js = get(f"{BASE}/scoreboard", {"dates": d})
        except Exception:
            return []
        out = []
        for e in js.get("events", []):
            if e.get("season", {}).get("type") == 2 and e["status"]["type"].get("completed"):
                out.append(e["id"])
        return out

    with ThreadPoolExecutor(8) as ex:
        ids = sorted({i for lst in ex.map(day, days) for i in lst})
    json.dump(ids, open(path, "w"))
    return ids


def game_row(eid):
    """Сжатые данные по матчу: пред-матч WP, счёт на перерыве/после 3й, ESPN WP в эти моменты, финал."""
    path = os.path.join(CACHE, f"g_{eid}.json")
    if os.path.exists(path):
        return json.load(open(path))
    try:
        s = get(f"{BASE}/summary", {"event": eid})
    except Exception:
        return None
    plays, wp = s.get("plays", []), s.get("winprobability", [])
    if not plays or not wp:
        return None
    wp_by_play = {w["playId"]: w["homeWinPercentage"] for w in wp}
    comp = s["header"]["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")

    row = {
        "id": eid,
        "date": comp.get("date", "")[:10],
        "home": home["team"]["abbreviation"],
        "away": away["team"]["abbreviation"],
        "home_final": int(home["score"]),
        "away_final": int(away["score"]),
        "pre_wp_home": wp[0]["homeWinPercentage"],
    }
    # последний розыгрыш каждой четверти -> счёт и WP на конец периода
    last = {}
    max_def_1h = 0  # максимальный отрыв в 1-й половине (для любой стороны)
    for p in plays:
        per = p["period"]["number"]
        last[per] = p
        if per <= 2:
            max_def_1h = max(max_def_1h, abs(p["homeScore"] - p["awayScore"]))
    for per, tag in [(2, "ht"), (3, "q3")]:
        p = last.get(per)
        if p is None:
            return None
        row[f"{tag}_diff_home"] = p["homeScore"] - p["awayScore"]
        row[f"{tag}_wp_home"] = wp_by_play.get(p["id"])
    row["max_lead_1h"] = max_def_1h
    json.dump(row, open(path, "w"))
    return row


def analyze(df, moment="ht", equal_band=(0.40, 0.60)):
    """Смотрим с точки зрения ОТСТАЮЩЕЙ команды в момент `moment`."""
    d = df.dropna(subset=[f"{moment}_diff_home"]).copy()
    diff = d[f"{moment}_diff_home"]
    d = d[diff != 0]
    diff = d[f"{moment}_diff_home"]
    trail_home = diff < 0
    d["deficit"] = diff.abs()
    final_margin_home = d["home_final"] - d["away_final"]
    d["trail_final_margin"] = final_margin_home.where(trail_home, -final_margin_home)
    d["trail_won"] = d["trail_final_margin"] > 0
    d["trail_pre_wp"] = d["pre_wp_home"].where(trail_home, 1 - d["pre_wp_home"])
    wpm = d[f"{moment}_wp_home"]
    d["trail_live_wp"] = wpm.where(trail_home, 1 - wpm)
    d["equal"] = d["trail_pre_wp"].between(*equal_band)

    bins = [10, 15, 20, 25, 30, 100]
    labels = ["10-14", "15-19", "20-24", "25-29", "30+"]
    d["bucket"] = pd.cut(d["deficit"], bins=bins, labels=labels, right=False)

    def summarize(g):
        n = len(g)
        win = g["trail_won"].mean()
        res = {
            "N": n,
            "win%": round(100 * win, 1),
            "нужен_кэф_ML": round(1 / win, 1) if win > 0 else float("inf"),
            "ESPN_WP%": round(100 * g["trail_live_wp"].mean(), 1),
        }
        for h in [5.5, 9.5, 14.5]:
            res[f"+{h}%"] = round(100 * (g["trail_final_margin"] > -h).mean(), 1)
        return pd.Series(res)

    out = {}
    for name, sub in [("все матчи", d), ("равные до игры", d[d["equal"]])]:
        sub = sub.dropna(subset=["bucket"])
        out[name] = sub.groupby("bucket", observed=True).apply(summarize, include_groups=False)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2022, 2023, 2024, 2025])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    rows = []
    for y in a.seasons:
        ids = season_event_ids(y)
        print(f"Сезон {y-1}-{str(y)[2:]}: {len(ids)} матчей, качаю...")
        with ThreadPoolExecutor(a.workers) as ex:
            rows += [r for r in ex.map(game_row, ids) if r]
    df = pd.DataFrame(rows)
    df.to_csv("games.csv", index=False)
    print(f"\nВсего матчей с данными: {len(df)}  (сохранено в games.csv)\n")

    pd.set_option("display.width", 160)
    for moment, title in [("ht", "ПОСЛЕ 2-Й ЧЕТВЕРТИ (перерыв)"), ("q3", "ПОСЛЕ 3-Й ЧЕТВЕРТИ")]:
        print("=" * 80, f"\n{title} — с точки зрения отстающей команды\n", "=" * 80, sep="")
        for name, table in analyze(df, moment).items():
            print(f"\n[{name}] отставание в очках:")
            print(table.to_string())
        print()
    print("Как читать: win% — как часто отстающий выиграл; нужен_кэф_ML — минимальный кэф,")
    print("при котором ставка на камбэк в ноль; +9.5% — как часто отстающий закрыл фору +9.5;")
    print("ESPN_WP% — сколько давала модель ESPN в этот момент (близко к тому, что закладывает рынок).")


if __name__ == "__main__":
    main()
