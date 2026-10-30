"""
Step 0 of the multi-sport research: download closed Polymarket events and keep only
what the collectors need (moneyline / spreads / first-set markets).

Outputs (next to this file):
  slim_nhl.json, slim_epl.json, slim_lal.json, slim_sea.json, slim_bun.json, slim_fl1.json, slim_ucl.json
  tennis_atp.json, tennis_wta.json   (big tournaments only: Slams, Masters/1000, Finals)

  python ms_fetch_events.py
then
  python ms_collect.py nhl|soccer|tennis
  python ms_analyze.py
"""
import json, os, re, time
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
GAMMA = "https://gamma-api.polymarket.com"
S = requests.Session()

TEAM_SERIES = {"nhl": 10346, "epl": 10188, "lal": 10193, "sea": 10203, "bun": 10194, "fl1": 10195, "ucl": 10204}
TENNIS_SERIES = {"atp": 10365, "wta": 10366}
BIG = ["Australian Open", "Roland Garros", "French Open", "Wimbledon", "US Open", "Indian Wells", "BNP Paribas Open",
       "Miami Open", "Monte-Carlo", "Monte Carlo", "Madrid", "Internazionali", "Italian Open", "Rome", "National Bank Open",
       "Canadian Open", "Cincinnati", "Shanghai", "Paris Masters", "Rolex Paris", "China Open", "Wuhan", "Dubai", "Qatar", "Finals"]


def get(url, params):
    for i in range(6):
        try:
            js = S.get(url, params=params, timeout=60).json()
            if isinstance(js, (list, dict)):
                return js
        except Exception:
            pass
        time.sleep(3 + 3 * i)
    raise RuntimeError(f"failed: {url} {params}")


def is_main(m):
    q = m.get("question") or ""
    return m.get("sportsMarketType") in ("moneyline", "spreads", "tennis_first_set_winner") \
        or bool(re.match(r"Will .+ (win on|end in a draw)", q))


def slim(e):
    keys = ("question", "sportsMarketType", "outcomes", "outcomePrices", "clobTokenIds", "conditionId",
            "gameStartTime", "line", "closedTime", "feesEnabled")
    return {"slug": e.get("slug"), "title": e.get("title"), "startTime": e.get("startTime"), "score": e.get("score"),
            "teams": [{k: t.get(k) for k in ("name", "alias", "abbreviation", "ordering")}
                      for t in (e.get("teams") or []) if isinstance(t, dict)],
            "markets": [{k: m.get(k) for k in keys} for m in (e.get("markets") or []) if isinstance(m, dict) and is_main(m)]}


def team_sports():
    for k, sid in TEAM_SERIES.items():
        out, off = [], 0
        while True:
            js = get(f"{GAMMA}/events", {"series_id": sid, "closed": "true", "limit": 100, "offset": off,
                                         "order": "startDate", "ascending": "true"})
            if not js:
                break
            out += [slim(e) for e in js if isinstance(e, dict)]
            off += len(js)
            if len(js) < 100:
                break
        out = [e for e in out if any(m.get("sportsMarketType") == "moneyline" or "win on" in (m.get("question") or "")
                                     for m in e["markets"])]
        json.dump(out, open(os.path.join(HERE, f"slim_{k}.json"), "w"))
        print(k, len(out))


def tennis():
    for k, sid in TENNIS_SERIES.items():
        keep, cur = [], None
        while True:   # deep pagination needs the keyset endpoint
            p = {"series_id": sid, "closed": "true", "limit": 100}
            if cur:
                p["after_cursor"] = cur
            js = get(f"{GAMMA}/events/keyset", p)
            for e in js.get("events", []):
                s = slim(e)
                ml = [m for m in s["markets"] if m["sportsMarketType"] == "moneyline"]
                if not ml or not any(m["sportsMarketType"] == "tennis_first_set_winner" for m in s["markets"]):
                    continue
                tourn = ml[0]["question"].split(":")[0]
                if any(x in tourn for x in ("Qualification", "Juniors", "Doubles")) or not any(b in tourn for b in BIG):
                    continue
                keep.append(s)
            cur = js.get("next_cursor")
            if not cur or not js.get("events"):
                break
        json.dump(keep, open(os.path.join(HERE, f"tennis_{k}.json"), "w"))
        print(k, len(keep))


if __name__ == "__main__":
    team_sports()
    tennis()
