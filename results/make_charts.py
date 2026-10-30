"""
Builds the charts used in RESEARCH.md from the collected datasets.

Expects the outputs of the research scripts:
  research/polymarket_nba/games_pm.jsonl, trades_pm.jsonl
  research/multisport/nhl.jsonl, soccer.jsonl, tennis_fixed.jsonl
  bot/games.csv                     (ESPN history, 4 NBA seasons)

  python results/make_charts.py      -> results/img/*.png
"""
import json, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "results", "img"); os.makedirs(IMG, exist_ok=True)
FEE, MINVOL = 0.05, 20

plt.rcParams.update({"figure.dpi": 150, "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.alpha": 0.25, "axes.titleweight": "bold"})
BLUE, RED, GREY, GREEN = "#2f6fdf", "#d9534f", "#888888", "#2e9e5b"


# ------------------------------------------------------------------ helpers
def roi_frame(rows):
    d = pd.DataFrame(rows)
    d = d[(d.vol >= MINVOL) & d.price.between(0.02, 0.98)].copy()
    d["cost"] = d.price + FEE * d.price * (1 - d.price)
    d["roi"] = d.win / d.cost - 1
    return d


def boot(x, n=2000):
    by = x.groupby("slug")["roi"].agg(["sum", "size"])
    s, c = by["sum"].values, by["size"].values
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(s), (n, len(s)))
    r = s[idx].sum(1) / c[idx].sum(1)
    return 100 * x.roi.mean(), 100 * np.percentile(r, 2.5), 100 * np.percentile(r, 97.5)


def load_nba(moment="ht"):
    games = {}
    for l in open(os.path.join(ROOT, "research/polymarket_nba/games_pm.jsonl")):
        g = json.loads(l)
        if g.get("season_type") != 1 and moment in g:
            games[g["slug"]] = g
    rows = []
    for l in open(os.path.join(ROOT, "research/polymarket_nba/trades_pm.jsonl")):
        t = json.loads(l); g = games.get(t["slug"])
        if not g:
            continue
        dh, fh = g[moment]["hs"] - g[moment]["as"], g["hf"] - g["af"]
        for r in t["rows"]:
            p, v = r.get(f"{moment}_buy"), r.get(f"{moment}_vol", 0)
            if p is None:
                continue
            if r["team"] == g["home"]: lead, fm = dh, fh
            elif r["team"] == g["away"]: lead, fm = -dh, -fh
            else: continue
            rows.append({"slug": t["slug"], "type": r["type"], "lead": lead, "h": r["handicap"], "price": p, "vol": v,
                         "win": fm + r["handicap"] > 0})
    return roi_frame(rows)


def load_mid_nba():
    """Same NBA halftime bets but priced with prices-history midpoints (the artifact)."""
    rows = []
    for l in open(os.path.join(ROOT, "research/polymarket_nba/games_pm.jsonl")):
        g = json.loads(l)
        if g.get("season_type") == 1 or "ht" not in g:
            continue
        dh, fh = g["ht"]["hs"] - g["ht"]["as"], g["hf"] - g["af"]
        for p in g["prices"]:
            if p.get("ht") is None:
                continue
            if p["team"] == g["home"]: lead, fm = dh, fh
            elif p["team"] == g["away"]: lead, fm = -dh, -fh
            else: continue
            rows.append({"slug": g["slug"], "type": p["type"], "lead": lead, "price": p["ht"] + 0.01, "vol": 999,
                         "win": fm + p["handicap"] > 0})
    return roi_frame(rows)


def load_ms(fname, flat):
    rows = []
    for l in open(os.path.join(ROOT, "research/multisport", fname)):
        g = json.loads(l)
        for r in g["rows"]:
            rows.append({**flat(g), **r})
    return roi_frame(rows)


# ------------------------------------------------------------------ charts
def chart_calibration(nba, nhl, soc, ten):
    fig, ax = plt.subplots(figsize=(6.2, 5.4))
    ax.plot([0, 1], [0, 1], color=GREY, ls="--", lw=1, label="perfect calibration")
    sets = [("NBA, halftime", nba[nba.type == "moneyline"], BLUE, "o"),
            ("NHL, after P2", nhl[(nhl.type == "moneyline") & (nhl.moment == "p2")], RED, "s"),
            ("Soccer, halftime", soc[(soc.type == "team") & (soc.outcome == "Yes")], GREEN, "^"),
            ("Tennis, after set 1", ten, "#8a5cd1", "D")]
    bins = np.linspace(0, 1, 11)
    for name, d, c, m in sets:
        d = d.copy(); d["b"] = pd.cut(d.price, bins)
        g = d.groupby("b", observed=True).agg(p=("price", "mean"), w=("win", "mean"), n=("win", "size"))
        g = g[g.n >= 30]
        ax.plot(g.p, g.w, marker=m, color=c, lw=1.4, ms=5, label=f"{name} (n={len(d):,})")
    ax.set_xlabel("Price paid (real taker buys)"); ax.set_ylabel("Actual win rate")
    ax.set_title("In-play markets are well calibrated")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout(); fig.savefig(os.path.join(IMG, "calibration.png")); plt.close(fig)


def chart_comeback_vs_leader(nba, nhl, soc, ten):
    items = []
    x = nba[nba.type == "moneyline"]
    items += [("NBA\nhalftime", "trailing 10–14", x[x.lead.between(-14, -10)]), ("NBA\nhalftime", "leading 10–14", x[x.lead.between(10, 14)])]
    x = nhl[(nhl.type == "moneyline") & (nhl.moment == "p2")]
    items += [("NHL\nafter P2", "trailing by 1", x[x.lead == -1]), ("NHL\nafter P2", "leading by 1", x[x.lead == 1])]
    x = soc[(soc.type == "team") & (soc.outcome == "Yes")]
    items += [("Soccer\nhalftime", "trailing by 1", x[x.lead == -1]), ("Soccer\nhalftime", "leading by 1", x[x.lead == 1])]
    items += [("Tennis\nafter set 1", "lost set 1", ten[~ten.won_set1]), ("Tennis\nafter set 1", "won set 1", ten[ten.won_set1])]
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    labels, pos = [], []
    for i, (sport, what, d) in enumerate(items):
        m, lo, hi = boot(d)
        c = RED if "trail" in what or "lost" in what else BLUE
        ax.bar(i, m, color=c, alpha=0.85, width=0.7)
        ax.errorbar(i, m, yerr=[[m - lo], [hi - m]], color="black", capsize=4, lw=1)
        ax.text(i, (hi if m >= 0 else lo) + (1.5 if m >= 0 else -3.5), f"{m:+.1f}%", ha="center", fontsize=8)
        labels.append(f"{sport}\n{what}"); pos.append(i)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(pos); ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel("ROI per $1 after fees, %  (95% CI)")
    ax.set_title("Comebacks (red) mostly lose money; leaders (blue) are priced about right")
    fig.tight_layout(); fig.savefig(os.path.join(IMG, "comeback_vs_leader.png")); plt.close(fig)


def chart_nba_gap(nba):
    x = nba[(nba.type == "moneyline") & (nba.lead < 0)].copy()
    x["gap"] = pd.cut(-x.lead, [0, 4, 9, 14, 19, 60], labels=["1–4", "5–9", "10–14", "15–19", "20+"])
    g = x.groupby("gap", observed=True).agg(price=("price", "mean"), win=("win", "mean"), n=("win", "size"))
    fig, ax = plt.subplots(figsize=(6.6, 4))
    i = np.arange(len(g))
    ax.bar(i - 0.18, 100 * g.price, 0.36, color=GREY, label="market price (implied %)")
    ax.bar(i + 0.18, 100 * g.win, 0.36, color=RED, label="actual comeback rate %")
    for k, n in enumerate(g.n):
        ax.text(k, max(100 * g.price.iloc[k], 100 * g.win.iloc[k]) + 1.2, f"n={n}", ha="center", fontsize=8)
    ax.set_xticks(i); ax.set_xticklabels(g.index); ax.set_xlabel("Trailing at halftime by (points)")
    ax.set_ylabel("%"); ax.set_title("NBA halftime: market price vs real comeback rate\n(clearly overpriced only in the 10–14 zone)", fontsize=10)
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(os.path.join(IMG, "nba_comebacks.png")); plt.close(fig)


def chart_artifact(nba, mid):
    def lead_spread(d):
        return d[(d.type == "spreads") & (d.lead >= 10)]
    a, b = boot(lead_spread(mid)), boot(lead_spread(nba))
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for i, (lab, (m, lo, hi), c) in enumerate([("prices-history\n(order-book midpoints)", a, "#f0ad4e"),
                                               ("real taker buys\n(executable)", b, BLUE)]):
        ax.bar(i, m, color=c, width=0.55)
        ax.errorbar(i, m, yerr=[[m - lo], [hi - m]], color="black", capsize=5, lw=1)
        ax.text(i, hi + 1.2, f"{m:+.1f}%", ha="center")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks([0, 1]); ax.set_xticklabels(["prices-history\n(order-book midpoints)", "real taker buys\n(executable)"])
    ax.set_ylabel("ROI, %  (95% CI)")
    ax.set_title("Same NBA bets, two price sources:\nthe 'edge' was a data artifact", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(IMG, "midprice_artifact.png")); plt.close(fig)


def chart_tennis(ten):
    def seg(p):
        return "underdog (<40%)" if p < .4 else ("even (40–60%)" if p <= .6 else "favourite (>60%)")
    ten = ten[ten.pre.notna()].copy(); ten["seg"] = ten.pre.apply(seg)
    order = ["underdog (<40%)", "even (40–60%)", "favourite (>60%)"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    w = 0.38
    for j, (won, c, lab) in enumerate([(True, BLUE, "won set 1"), (False, RED, "lost set 1")]):
        for i, s in enumerate(order):
            d = ten[(ten.won_set1 == won) & (ten.seg == s)]
            m, lo, hi = boot(d)
            xpos = i + (j - 0.5) * w
            ax.bar(xpos, m, w * 0.95, color=c, alpha=0.85, label=lab if i == 0 else None)
            ax.errorbar(xpos, m, yerr=[[m - lo], [hi - m]], color="black", capsize=3, lw=1)
            ax.text(xpos, (hi + 1) if m >= 0 else (lo - 3), f"{m:+.1f}%", ha="center", fontsize=8)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(range(3)); ax.set_xticklabels(order); ax.set_xlabel("Player's pre-match status")
    ax.set_ylabel("ROI on match winner, %  (95% CI)")
    ax.set_title("Tennis after set 1: favourites who drop it are overpriced")
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(os.path.join(IMG, "tennis_set1.png")); plt.close(fig)


def chart_espn_history():
    d = pd.read_csv(os.path.join(ROOT, "bot", "games.csv"))
    d = d[d.ht_diff_home != 0].copy()
    trail_home = d.ht_diff_home < 0
    d["deficit"] = d.ht_diff_home.abs()
    fm = d.home_final - d.away_final
    d["won"] = np.where(trail_home, fm > 0, fm < 0)
    g = d[d.deficit <= 30].groupby("deficit").agg(rate=("won", "mean"), n=("won", "size"))
    g = g[g.n >= 25]          # drop points with tiny samples
    fig, ax = plt.subplots(figsize=(6.6, 3.8))
    ax.plot(g.index, 100 * g.rate, color=RED, marker="o", ms=3)
    ax.set_xlabel("Trailing at halftime by (points)"); ax.set_ylabel("Won the game, %")
    ax.set_title(f"NBA comeback rate by halftime deficit (ESPN, {len(d):,} games)")
    fig.tight_layout(); fig.savefig(os.path.join(IMG, "nba_history.png")); plt.close(fig)


if __name__ == "__main__":
    nba = load_nba("ht"); mid = load_mid_nba()
    nhl = load_ms("nhl.jsonl", lambda g: {"slug": g["slug"]})
    soc = load_ms("soccer.jsonl", lambda g: {"slug": g["slug"]})
    ten = load_ms("tennis_fixed.jsonl", lambda g: {"slug": g["slug"]})
    chart_espn_history()
    chart_calibration(nba, nhl, soc, ten)
    chart_comeback_vs_leader(nba, nhl, soc, ten)
    chart_nba_gap(nba)
    chart_artifact(nba, mid)
    chart_tennis(ten)
    print("saved to", IMG)
