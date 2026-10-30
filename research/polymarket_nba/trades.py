"""
Реальные сделки Polymarket в окне перерыва и паузы после 3-й четверти.
История цен (prices-history) на неликвидных рынках показывает середину пустого стакана —
по такой цене купить нельзя. Поэтому для бэктеста берём ТОЛЬКО реальные сделки-покупки:
средневзвешенная цена покупок исхода в окне = цена, по которой реально можно было войти.

  python trades.py   -> trades_pm.jsonl
"""
import json, os, sys
from concurrent.futures import ThreadPoolExecutor
from collect import jget, cached, CACHE, HERE, GAMMA

DATA = "https://data-api.polymarket.com/trades"


def condition_ids():
    def fetch():
        out, off = {}, 0
        while True:
            js = jget(f"{GAMMA}/events", {"series_id": 10345, "closed": "true", "limit": 100,
                                          "offset": off, "order": "startDate", "ascending": "true"})
            if not js:
                break
            for e in js:
                for m in e["markets"]:
                    if m.get("sportsMarketType") in ("moneyline", "spreads"):
                        out.setdefault(e["slug"], []).append(
                            {"q": m["question"], "type": m["sportsMarketType"], "line": m.get("line"),
                             "cid": m["conditionId"], "outcomes": json.loads(m["outcomes"])})
            off += len(js)
            if len(js) < 100:
                break
        return out
    return cached("cids.json", fetch)


def window_trades(cid, t0, t1):
    tr = jget(DATA, {"market": cid, "start": t0, "end": t1, "limit": 1000})
    return tr if isinstance(tr, list) else []


def vwap_buys(trades, outcome):
    b = [(float(x["price"]), float(x["size"])) for x in trades
         if x.get("outcome") == outcome and x.get("side") == "BUY"]
    vol = sum(s for _, s in b)
    if vol <= 0:
        return None, 0
    return round(sum(p * s for p, s in b) / vol, 4), round(vol, 1)


def process(args):
    slug, mkts = args
    path = os.path.join(CACHE, f"t_{slug}.json")
    if os.path.exists(path):
        return json.load(open(path))
    rp = os.path.join(CACHE, f"r_{slug}.json")
    if not os.path.exists(rp):
        return None
    g = json.load(open(rp))
    try:
        rows = []
        for m in mkts:
            win = {}
            for tag, span in [("ht", 600), ("q3", 180)]:
                if tag in g:
                    win[tag] = window_trades(m["cid"], g[tag]["ts"], g[tag]["ts"] + span)
            for i, o in enumerate(m["outcomes"]):
                h = 0.0 if m["type"] == "moneyline" else (float(m["line"] or 0) if i == 0 else -float(m["line"] or 0))
                row = {"type": m["type"], "team": o, "handicap": h}
                for tag, tr in win.items():
                    row[f"{tag}_buy"], row[f"{tag}_vol"] = vwap_buys(tr, o)
                rows.append(row)
        r = {"slug": slug, "rows": rows}
        json.dump(r, open(path, "w"))
        return r
    except Exception as ex:
        print("ERR", slug, ex, file=sys.stderr)
        return None


def main():
    cids = condition_ids()
    items = list(cids.items())
    print("матчей:", len(items))
    n = 0
    with ThreadPoolExecutor(8) as ex, open(os.path.join(HERE, "trades_pm.jsonl"), "w") as f:
        for r in ex.map(process, items):
            n += 1
            if r:
                f.write(json.dumps(r) + "\n")
            if n % 100 == 0:
                print(f"  {n}/{len(items)}", flush=True)
    print("Готово")


if __name__ == "__main__":
    main()
