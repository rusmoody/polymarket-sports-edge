"""
Бэктест на ИСПОЛНИМЫХ ценах: средняя цена реальных покупок исхода на перерыве / после 3-й четверти.
Одна ставка = $1 по этой цене (+ комиссия). Сравниваем стратегии «за лидера» и «за камбэк».

  python analyze2.py
"""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
FEE_RATE = 0.05
MIN_VOL = 20          # минимум 20 акций куплено в окне — иначе рынок считаем неликвидным


def load(moment):
    games = {}
    for l in open(os.path.join(HERE, "games_pm.jsonl")):
        g = json.loads(l)
        if g.get("season_type") != 1 and moment in g:
            games[g["slug"]] = g
    rows = []
    for l in open(os.path.join(HERE, "trades_pm.jsonl")):
        t = json.loads(l)
        g = games.get(t["slug"])
        if not g:
            continue
        dh = g[moment]["hs"] - g[moment]["as"]
        fh = g["hf"] - g["af"]
        for r in t["rows"]:
            p, vol = r.get(f"{moment}_buy"), r.get(f"{moment}_vol", 0)
            if p is None or vol < MIN_VOL or not (0.02 <= p <= 0.98):
                continue
            if r["team"] == g["home"]:
                lead, fm = dh, fh
            elif r["team"] == g["away"]:
                lead, fm = -dh, -fh
            else:
                continue
            rows.append({"slug": t["slug"], "month": g["start"][:7], "type": r["type"], "team": r["team"],
                         "h": r["handicap"], "lead": lead, "price": p, "vol": vol,
                         "cushion": lead + r["handicap"],   # запас по форе прямо сейчас
                         "win": fm + r["handicap"] > 0})
    d = pd.DataFrame(rows)
    d["cost"] = d["price"] + FEE_RATE * d["price"] * (1 - d["price"])
    d["roi"] = d["win"] / d["cost"] - 1
    return d


def band(x):
    a = abs(x)
    for lo, hi in [(0, 0), (1, 4), (5, 9), (10, 14), (15, 19), (20, 99)]:
        if lo <= a <= hi:
            return f"{lo:02d}-{hi}" if hi < 99 else "20+"


def boot_ci(x, n=2000, seed=0):
    """95% интервал ROI, бутстрэп по матчам (ставки внутри матча коррелированы)."""
    rng = np.random.default_rng(seed)
    by = x.groupby("slug")["roi"].agg(["sum", "size"])
    s, c = by["sum"].values, by["size"].values
    if len(s) < 5:
        return (np.nan, np.nan)
    idx = rng.integers(0, len(s), (n, len(s)))
    r = s[idx].sum(1) / c[idx].sum(1)
    return tuple(np.round(100 * np.percentile(r, [2.5, 97.5]), 1))


def summ(x):
    lo, hi = boot_ci(x)
    return pd.Series({"ставок": len(x), "матчей": x.slug.nunique(),
                      "цена%": round(100 * x.price.mean(), 1), "выигр%": round(100 * x.win.mean(), 1),
                      "ROI%": round(100 * x.roi.mean(), 1), "95% ДИ": f"[{lo}; {hi}]"})


def one_per_game(x):
    """Одна ставка на матч — самый ликвидный рынок (убираем корреляцию)."""
    return x.sort_values("vol", ascending=False).groupby("slug").head(1)


def report(moment, title):
    d = load(moment)
    print("=" * 100, f"\n{title}: матчей {d.slug.nunique()}, исполнимых ставок {len(d)}\n", "=" * 100, sep="")
    ml = d[d.type == "moneyline"].copy()
    ml["цена"] = pd.cut(ml.price, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1])
    print("\nКАЛИБРОВКА манилайна (реальные покупки):")
    print(ml.groupby("цена", observed=True).apply(summ, include_groups=False).to_string())

    for name, x in [("МАНИЛАЙН", ml), ("ФОРЫ", d[d.type == "spreads"])]:
        x = x[x.lead != 0].copy()
        x["кто"] = np.where(x.lead > 0, "лидер", "отстающий")
        x["разрыв"] = x.lead.apply(band)
        print(f"\n{name}: лидер vs отстающий по разрыву (все ставки)")
        print(x.groupby(["кто", "разрыв"]).apply(summ, include_groups=False).to_string())
        if name == "ФОРЫ":
            y = one_per_game(x[(x["кто"] == "лидер") & (x.lead >= 10)])
            print("\nФОРЫ, лидер +10 и больше, ОДНА ставка на матч (самый ликвидный рынок):")
            print(summ(y).to_string())
            y = y.copy(); y["запас"] = pd.cut(y.cushion, [-50, -5, 0, 5, 10, 50])
            print("   …по запасу (разрыв + фора):")
            print(y.groupby("запас", observed=True).apply(summ, include_groups=False).to_string())
            y["период"] = np.where(y.month < "2026-02", "окт-янв", np.where(y.month < "2026-09", "фев-июн", "сезон 26/27"))
            print("   …по периодам:")
            print(y.groupby("период").apply(summ, include_groups=False).to_string())


if __name__ == "__main__":
    pd.set_option("display.width", 220)
    report("ht", "ПЕРЕРЫВ")
    report("q3", "ПОСЛЕ 3-Й ЧЕТВЕРТИ")
