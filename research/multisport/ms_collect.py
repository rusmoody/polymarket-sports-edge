"""
Сбор исполнимых цен Polymarket в ключевые моменты матчей:
  НХЛ    — после 1-го и 2-го периода (перерыв), состояние из ESPN;
  футбол — перерыв (АПЛ, Ла Лига, Серия А, Бундеслига, Лига 1, ЛЧ), состояние из ESPN;
  теннис — сразу после 1-го сета (крупные турниры), состояние из рынка «Победитель 1-го сета».
Цена = средневзвешенная цена реальных ПОКУПОК исхода в окне (data-api trades).

  python ms_collect.py nhl|soccer|tennis
"""
import json, os, sys, time, re, statistics, difflib, unicodedata, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache"); os.makedirs(CACHE, exist_ok=True)
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com/trades"
S = requests.Session()


def jget(url, params=None, tries=5):
    for i in range(tries):
        try:
            r = S.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(2 + 2 * i); continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1 + i)


def cached(name, fn):
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    v = fn()
    tmp = p + f".{os.getpid()}.{time.time_ns()}"
    json.dump(v, open(tmp, "w")); os.replace(tmp, p)
    return v


def ts(s):
    s = s.replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"):
        s += ":00"
    return int(dt.datetime.fromisoformat(s).timestamp())


def trades(cid, t0, t1):
    tr = jget(DATA, {"market": cid, "start": t0, "end": t1, "limit": 1000})
    return tr if isinstance(tr, list) else []


def vwap_buys(tr, outcome):
    b = [(float(x["price"]), float(x["size"])) for x in tr if x.get("outcome") == outcome and x.get("side") == "BUY"]
    v = sum(s for _, s in b)
    return (round(sum(p * s for p, s in b) / v, 4), round(v, 1)) if v > 0 else (None, 0)


def last_before(token, t):
    h = (jget(f"{CLOB}/prices-history", {"market": token, "startTs": t - 4 * 3600, "endTs": t, "fidelity": 5}) or {}).get("history", [])
    ps = [float(x["p"]) for x in h if int(x["t"]) <= t]
    return ps[-1] if ps else None


def resolved(m):
    try:
        return [float(x) for x in json.loads(m["outcomePrices"])]
    except Exception:
        return None


# ------------------------------------------------------------------ НХЛ
def nhl_state(g):
    ml = next(m for m in g["markets"] if m["sportsMarketType"] == "moneyline")
    teams = {o.lower() for o in json.loads(ml["outcomes"])}
    start = ts(ml["gameStartTime"])
    ev = None
    for shift in (-5, -4, 0):
        day = dt.datetime.utcfromtimestamp(start + shift * 3600).strftime("%Y%m%d")
        sb = cached(f"nhl_sb_{day}.json", lambda: (jget(f"{ESPN}/hockey/nhl/scoreboard", {"dates": day}) or {}).get("events", []))
        for e in sb:
            names = {(t["team"].get("shortDisplayName") or t["team"].get("name") or "").lower() for t in e["competitions"][0]["competitors"]}
            if names == teams and abs(ts(e["date"]) - start) < 6 * 3600:
                ev = e; break
        if ev:
            break
    if not ev:
        return None

    def fetch():
        s = jget(f"{ESPN}/hockey/nhl/summary", {"event": ev["id"]}) or {}
        comp = s["header"]["competitions"][0]
        t = {x["homeAway"]: x for x in comp["competitors"]}
        nm = lambda x: x["team"].get("shortDisplayName") or x["team"].get("name")
        res = {"home": nm(t["home"]), "away": nm(t["away"]), "st": s["header"].get("season", {}).get("type")}
        for per in (1, 2):
            pp = [p for p in s.get("plays", []) if p["period"]["number"] == per and p.get("wallclock")]
            if pp:
                res[f"p{per}"] = {"hs": pp[-1]["homeScore"], "as": pp[-1]["awayScore"], "ts": ts(pp[-1]["wallclock"])}
        return res
    return cached(f"nhl_g_{ev['id']}.json", fetch)


def nhl_process(g):
    def fetch():
        st = nhl_state(g)
        if not st or "p2" not in st:
            return None
        ml = next(m for m in g["markets"] if m["sportsMarketType"] == "moneyline")
        start = ts(ml["gameStartTime"])
        pre = {o: last_before(t, start) for o, t in zip(json.loads(ml["outcomes"]), json.loads(ml["clobTokenIds"]))}
        rows = []
        for m in g["markets"]:
            res = resolved(m)
            if not res or max(res) < 0.99:
                continue
            outs = json.loads(m["outcomes"])
            for tag in ("p1", "p2"):
                if tag not in st:
                    continue
                tr = trades(m["conditionId"], st[tag]["ts"], st[tag]["ts"] + 600)
                for i, o in enumerate(outs):
                    h = 0.0 if m["sportsMarketType"] == "moneyline" else (float(m["line"] or 0) if i == 0 else -float(m["line"] or 0))
                    p, v = vwap_buys(tr, o)
                    if p is None:
                        continue
                    lead = (st[tag]["hs"] - st[tag]["as"]) * (1 if o == st["home"] else -1 if o == st["away"] else 0)
                    rows.append({"moment": tag, "type": m["sportsMarketType"], "team": o, "h": h, "lead": lead,
                                 "pre": pre.get(o), "price": p, "vol": v, "win": res[i] > 0.5})
        return {"slug": g["slug"], "start": ml["gameStartTime"], "season_type": st.get("st"), "rows": rows}
    return cached(f"nhl_r_{g['slug']}.json", fetch)


# ------------------------------------------------------------------ футбол
LEAGUES = {"epl": "eng.1", "lal": "esp.1", "sea": "ita.1", "bun": "ger.1", "fl1": "fra.1", "ucl": "uefa.champions"}
STOP = {"fc", "afc", "cf", "sc", "ac", "ss", "us", "as", "cd", "rc", "rcd", "ud", "sd", "ca", "club", "de", "1.", "fk", "sv", "vfb", "vfl", "tsg", "bv", "borussia", "09", "1899", "04", "05"}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in STOP)


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def soccer_markets(g):
    """home/away/draw рынки «Will X win on…» / «…end in a draw»."""
    win, draw = [], None
    for m in g["markets"]:
        q = m.get("question") or ""
        if "end in a draw" in q:
            draw = m
        elif q.startswith("Will ") and " win on " in q:
            win.append((q[5:q.index(" win on ")], m))
    return win, draw


def soccer_state(lg, g):
    win, draw = soccer_markets(g)
    if len(win) != 2 or not draw:
        return None
    start = ts(win[0][1]["gameStartTime"])
    names = [w[0] for w in win] + [t.get("alias") or t.get("name") or "" for t in g.get("teams", [])]
    best, bs = None, 0
    for shift in (0, -6, 6):
        day = dt.datetime.utcfromtimestamp(start + shift * 3600).strftime("%Y%m%d")
        sb = cached(f"soc_{lg}_{day}.json", lambda: (jget(f"{ESPN}/soccer/{LEAGUES[lg]}/scoreboard", {"dates": day}) or {}).get("events", []))
        for e in sb:
            if abs(ts(e["date"]) - start) > 3 * 3600:
                continue
            en = [t["team"]["displayName"] for t in e["competitions"][0]["competitors"]]
            sc = sum(max(sim(n, x) for x in names) for n in en) / 2
            if sc > bs:
                best, bs = e, sc
    if not best or bs < 0.6:
        return None
    ev = best

    def fetch():
        s = jget(f"{ESPN}/soccer/{LEAGUES[lg]}/summary", {"event": ev["id"]}) or {}
        comp = s["header"]["competitions"][0]
        t = {x["homeAway"]: x for x in comp["competitors"]}
        ids = {t["home"]["team"]["id"]: "home", t["away"]["team"]["id"]: "away"}
        res = {"home": t["home"]["team"]["displayName"], "away": t["away"]["team"]["displayName"]}
        ke = s.get("keyEvents", [])
        ht = [k for k in ke if (k.get("type", {}).get("text") or "").lower().startswith("halftime") and k.get("wallclock")]
        if not ht:
            return res
        hs = as_ = 0
        for k in ke:
            if k.get("scoringPlay") and k.get("period", {}).get("number") == 1:
                side = ids.get((k.get("team") or {}).get("id"))
                if side == "home": hs += 1
                elif side == "away": as_ += 1
        res["ht"] = {"hs": hs, "as": as_, "ts": ts(ht[0]["wallclock"])}
        return res
    st = cached(f"soc_g_{ev['id']}.json", fetch)
    st["match"] = round(bs, 2)
    return st


def soccer_process(args):
    lg, g = args
    def fetch():
        st = soccer_state(lg, g)
        if not st or "ht" not in st:
            return None
        win, draw = soccer_markets(g)
        # какой рынок — хозяева, какой — гости
        side = {}
        for name, m in win:
            side[m["conditionId"]] = "home" if sim(name, st["home"]) >= sim(name, st["away"]) else "away"
        if len(set(side.values())) != 2:
            return None
        diff = st["ht"]["hs"] - st["ht"]["as"]
        start = ts(win[0][1]["gameStartTime"])
        rows = []
        for name, m in win + [("draw", draw)]:
            res = resolved(m)
            if not res or max(res) < 0.99:
                continue
            tr = trades(m["conditionId"], st["ht"]["ts"], st["ht"]["ts"] + 600)
            toks = json.loads(m["clobTokenIds"])
            pre_yes = last_before(toks[0], start)
            if name == "draw":
                lead, what = -abs(diff), "draw"     # для ничьей: 0 = сейчас ничья
            else:
                lead = diff if side[m["conditionId"]] == "home" else -diff
                what = "team"
            for i, o in enumerate(json.loads(m["outcomes"])):   # Yes / No
                p, v = vwap_buys(tr, o)
                if p is None:
                    continue
                rows.append({"moment": "ht", "type": what, "outcome": o, "lead": lead, "pre_yes": pre_yes,
                             "price": p, "vol": v, "win": res[i] > 0.5})
        return {"slug": g["slug"], "league": lg, "start": win[0][1]["gameStartTime"], "match": st["match"], "rows": rows}
    return cached(f"soc_r_{g['slug']}.json", fetch)


# ------------------------------------------------------------------ теннис
def tennis_process(g):
    def fetch():
        ml = next((m for m in g["markets"] if m["sportsMarketType"] == "moneyline"), None)
        s1 = next((m for m in g["markets"] if m["sportsMarketType"] == "tennis_first_set_winner"), None)
        if not ml or not s1 or not ml.get("gameStartTime"):
            return None
        rml, rs1 = resolved(ml), resolved(s1)
        if not rml or not rs1 or max(rml) < 0.99 or max(rs1) < 0.99:
            return None
        start = ts(ml["gameStartTime"])
        tok1 = json.loads(s1["clobTokenIds"])[0]
        h = (jget(f"{CLOB}/prices-history", {"market": tok1, "startTs": start - 600, "endTs": start + 6 * 3600, "fidelity": 1}) or {}).get("history", [])
        h = [x for x in h if start - 600 <= int(x["t"]) <= start + 6 * 3600]
        end1 = next((int(x["t"]) for x in h if float(x["p"]) >= 0.97 or float(x["p"]) <= 0.03), None)
        if not end1:
            return None
        outs = json.loads(ml["outcomes"]); s1outs = json.loads(s1["outcomes"])
        set1_winner = s1outs[0] if rs1[0] > 0.5 else s1outs[1]
        tr = trades(ml["conditionId"], end1, end1 + 240)
        rows = []
        for i, (o, tok) in enumerate(zip(outs, json.loads(ml["clobTokenIds"]))):
            p, v = vwap_buys(tr, o)
            if p is None:
                continue
            rows.append({"moment": "set1", "type": "moneyline", "player": o, "won_set1": o == set1_winner,
                         "pre": last_before(tok, start), "price": p, "vol": v, "win": rml[i] > 0.5})
        return {"slug": g["slug"], "tour": g["slug"].split("-")[0], "tourn": ml["question"].split(":")[0],
                "start": ml["gameStartTime"], "rows": rows}
    return cached(f"ten_r_{g['slug']}.json", fetch)


def run(items, fn, out, workers=8):
    n = 0; ok = 0
    with ThreadPoolExecutor(workers) as ex, open(os.path.join(HERE, out), "w") as f:
        for r in ex.map(lambda x: safe(fn, x), items):
            n += 1
            if r and r.get("rows"):
                f.write(json.dumps(r) + "\n"); ok += 1
            if n % 200 == 0:
                print(f"  {n}/{len(items)} ok={ok}", flush=True)
    print("готово", n, "ok", ok)


def safe(fn, x):
    try:
        return fn(x)
    except Exception as ex:
        print("ERR", str(ex)[:120], file=sys.stderr)
        return None


if __name__ == "__main__":
    what = sys.argv[1]
    if what == "nhl":
        run(json.load(open(os.path.join(HERE, "slim_nhl.json"))), nhl_process, "nhl.jsonl")
    elif what == "soccer":
        items = [(lg, g) for lg in LEAGUES for g in json.load(open(os.path.join(HERE, f"slim_{lg}.json")))]
        run(items, soccer_process, "soccer.jsonl")
    elif what == "tennis":
        items = json.load(open(os.path.join(HERE, "tennis_atp.json"))) + json.load(open(os.path.join(HERE, "tennis_wta.json")))
        run(items, tennis_process, "tennis.jsonl")
