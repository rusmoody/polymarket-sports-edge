"""
Анализ исполнимых цен Polymarket: НХЛ (после 1-го/2-го периода), футбол (перерыв), теннис (после 1-го сета).
ROI на $1 по цене реальных покупок + комиссия 5%·p·(1−p) (с запасом), 95% ДИ бутстрэпом по матчам.

  python ms_analyze.py
"""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.05
MINVOL = 20


def load(fname, flat):
    rows = []
    for l in open(os.path.join(HERE, fname)):
        g = json.loads(l)
        for r in g["rows"]:
            rows.append({**flat(g), **r})
    d = pd.DataFrame(rows)
    d = d[(d.vol >= MINVOL) & d.price.between(0.02, 0.98)].copy()
    d["cost"] = d.price + FEE * d.price * (1 - d.price)
    d["roi"] = d.win / d.cost - 1
    return d


def ci(x, n=2000):
    by = x.groupby("slug")["roi"].agg(["sum", "size"])
    s, c = by["sum"].values, by["size"].values
    if len(s) < 8:
        return "—"
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(s), (n, len(s)))
    r = s[idx].sum(1) / c[idx].sum(1)
    lo, hi = np.percentile(r, [2.5, 97.5]) * 100
    flag = " ✅" if lo > 0 else (" ❌" if hi < 0 else "")
    return f"[{lo:+.1f}; {hi:+.1f}]{flag}"


def summ(x):
    return pd.Series({"ставок": len(x), "матчей": x.slug.nunique(), "цена%": round(100 * x.price.mean(), 1),
                      "выигр%": round(100 * x.win.mean(), 1), "ROI%": round(100 * x.roi.mean(), 1), "95% ДИ": ci(x)})


def show(title, d, by):
    print(f"\n— {title}")
    if d.empty:
        print("  нет данных"); return
    print(d.groupby(by, observed=True).apply(summ, include_groups=False).to_string())


def calib(d, title):
    x = d.copy(); x["цена"] = pd.cut(x.price, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1])
    show(f"{title}: КАЛИБРОВКА (цена vs реальная частота)", x, "цена")


def segpre(p):
    if p is None or p != p: return "?"
    return "андердог <40%" if p < .4 else ("равные 40-60%" if p <= .6 else "фаворит >60%")


def nhl():
    d = load("nhl.jsonl", lambda g: {"slug": g["slug"], "month": g["start"][:7]})
    print("=" * 100, f"\nНХЛ: матчей {d.slug.nunique()}, ставок {len(d)}\n" + "=" * 100)
    ml = d[d.type == "moneyline"].copy()
    calib(ml[ml.moment == "p2"], "НХЛ после 2-го периода, победа")
    ml["кто"] = np.select([ml.lead > 0, ml.lead < 0], ["лидер", "отстающий"], "ничья")
    ml["разрыв"] = ml.lead.abs().clip(upper=3).astype(int).astype(str).replace({"3": "3+"})
    for mo in ("p1", "p2"):
        show(f"НХЛ после {mo[1]}-го периода, ПОБЕДА: лидер/отстающий по разрыву в шайбах", ml[ml.moment == mo], ["кто", "разрыв"])
    x = ml[(ml.moment == "p2") & (ml.lead != 0)].copy(); x["до игры"] = x.pre.apply(segpre)
    show("НХЛ после 2-го, по статусу до игры", x, ["кто", "до игры"])
    sp = d[d.type == "spreads"].copy()
    sp["кто"] = np.select([sp.lead > 0, sp.lead < 0], ["лидер", "отстающий"], "ничья")
    sp["фора"] = sp.h.map(lambda h: f"{h:+.1f}")
    show("НХЛ после 2-го, ФОРЫ ±1.5", sp[sp.moment == "p2"], ["кто", "фора"])


def soccer():
    d = load("soccer.jsonl", lambda g: {"slug": g["slug"], "league": g["league"], "month": g["start"][:7]})
    print("\n" + "=" * 100, f"\nФУТБОЛ (перерыв): матчей {d.slug.nunique()}, ставок {len(d)}\n" + "=" * 100)
    t = d[d.type == "team"].copy()
    calib(t[t.outcome == "Yes"], "Футбол, «команда победит — Да»")
    t["ситуация"] = np.select([t.lead >= 2, t.lead == 1, t.lead == 0, t.lead == -1, t.lead <= -2],
                              ["ведёт 2+", "ведёт 1", "ничья", "проигр. 1", "проигр. 2+"], "?")
    show("Футбол: ставка «команда ПОБЕДИТ» (Yes) / «НЕ победит» (No) по счёту на перерыве", t, ["outcome", "ситуация"])
    x = t[(t.outcome == "Yes") & (t.lead == 1)].copy(); x["до игры"] = x.pre_yes.apply(segpre)
    show("Футбол: ведёт 1 на перерыве, победа — по статусу до игры", x, "до игры")
    dr = d[d.type == "draw"].copy()
    dr["счёт"] = dr.lead.map(lambda v: "ничья на перерыве" if v == 0 else ("разница 1" if v == -1 else "разница 2+"))
    show("Футбол: рынок НИЧЬЯ (Yes/No) по счёту на перерыве", dr, ["outcome", "счёт"])
    show("Футбол: ведущий на 1 гол, «победит — Да», по лигам", t[(t.outcome == "Yes") & (t.lead == 1)], "league")


def fix_tennis():
    """Имена в рынке 1-го сета короткие (фамилии) — пересчитываем, кто выиграл 1-й сет, по порядку исходов."""
    ev = {g["slug"]: g for f in ("tennis_atp.json", "tennis_wta.json") for g in json.load(open(os.path.join(HERE, f)))}
    out = []
    for l in open(os.path.join(HERE, "tennis.jsonl")):
        g = json.loads(l); e = ev[g["slug"]]
        ml = next(m for m in e["markets"] if m["sportsMarketType"] == "moneyline")
        s1 = next(m for m in e["markets"] if m["sportsMarketType"] == "tennis_first_set_winner")
        mo, so = json.loads(ml["outcomes"]), json.loads(s1["outcomes"])
        res = [float(x) for x in json.loads(s1["outcomePrices"])]
        w = so[0] if res[0] > 0.5 else so[1]
        for r in g["rows"]:
            r["won_set1"] = w.lower() in r["player"].lower() or r["player"].lower() in w.lower()
        out.append(json.dumps(g))
    open(os.path.join(HERE, "tennis_fixed.jsonl"), "w").write("\n".join(out) + "\n")


def tennis():
    fix_tennis()
    d = load("tennis_fixed.jsonl", lambda g: {"slug": g["slug"], "tour": g["tour"], "tourn": g["tourn"],
                                        "slam": any(s in g["tourn"] for s in ("Australian Open", "Roland", "French Open", "Wimbledon", "US Open"))})
    print("\n" + "=" * 100, f"\nТЕННИС (после 1-го сета): матчей {d.slug.nunique()}, ставок {len(d)}\n" + "=" * 100)
    calib(d, "Теннис после 1-го сета, победа в матче")
    d["кто"] = np.where(d.won_set1, "выиграл 1-й сет", "проиграл 1-й сет")
    d["до игры"] = d.pre.apply(segpre)
    show("Теннис: по итогу 1-го сета и статусу до игры", d, ["кто", "до игры"])
    show("Теннис: по туру", d, ["tour", "кто"])
    d["тип"] = np.where(d.slam & (d.tour == "atp"), "ТБШ мужчины (до 3 сетов)", "остальные (до 2 сетов)")
    show("Теннис: Шлемы у мужчин vs остальное", d, ["тип", "кто"])


if __name__ == "__main__":
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    nhl(); soccer(); tennis()
