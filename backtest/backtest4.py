#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest4.py — Option 2 : tendances LENTES (bougies journalieres) pour limiter l'effet des frais.

Hypotheses fixees d'avance (aucun reglage ajuste) :
  daily20   : cassure du plus haut/bas de 20 jours, stop 2.5 ATR(j), suiveur 3 ATR(j)
  daily50   : cassure de 50 jours, stop 3 ATR(j), suiveur 4 ATR(j)
  emacross  : croisement EMA20/EMA50 journalieres, stop 3 ATR(j), suiveur 4 ATR(j)
Deux univers : 7 paires, et BTC+ETH seulement (les plus liquides).
Deux jeux de couts : reels (taker) et « maker » (ordres limites 0.02 %, sans glissement a l'entree).
Critere : t-stat > 2 ET majorite de semestres positifs. Sinon : rien de retenu.
Journee = jour UTC (cloture 00:00 UTC).
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest as B  # noqa: E402
import backtest2 as B2  # noqa: E402
import strategie as S  # noqa: E402

H = S.H
DAY = 24 * H


def prepare4(d):
    h1 = S.prepare(d)
    h4 = d["4h"].copy()
    h4["day"] = h4["open_time"] // DAY
    g = h4.groupby("day")
    dj = pd.DataFrame({"open_time": g["open_time"].first(), "open": g["open"].first(),
                       "high": g["high"].max(), "low": g["low"].min(),
                       "close": g["close"].last(), "n": g["open"].count()})
    dj = dj[dj["n"] == 6].reset_index(drop=True)          # jours complets uniquement
    dj["close_time"] = dj["open_time"] + DAY
    dj["atr_d"] = S.atr(dj)
    for n in (20, 50):
        dj[f"dh{n}"] = dj["high"].shift(1).rolling(n).max()
        dj[f"dl{n}"] = dj["low"].shift(1).rolling(n).min()
    dj["e20"], dj["e50"] = S.ema(dj["close"], 20), S.ema(dj["close"], 50)
    dj["cross_up"] = (dj["e20"] > dj["e50"]) & (dj["e20"].shift(1) <= dj["e50"].shift(1))
    dj["cross_dn"] = (dj["e20"] < dj["e50"]) & (dj["e20"].shift(1) >= dj["e50"].shift(1))
    dj["cd"] = dj["close"]
    cols = ["atr_d", "dh20", "dl20", "dh50", "dl50", "cross_up", "cross_dn", "cd"]
    a = S.align(h1, dj.assign(**{c: dj[c].astype(float) for c in cols}), cols)
    h1 = pd.concat([h1, a], axis=1)
    h1["newday"] = (h1["close_time"] % DAY) == 0
    return h1


def _make(lg, sh, h1, sl_k, tr_k, maxh=2160):
    out = []
    for i in np.flatnonzero((lg | sh).to_numpy()):
        r = h1.iloc[i]
        d = 1 if lg.iat[i] else -1
        e, a = r["close"], r["atr_d"]
        if not np.isfinite(a) or a <= 0:
            continue
        out.append((i, d, e - d * sl_k * a, None, maxh, tr_k * a))
    return out


def daily20(h1, p):
    return _make(h1["newday"] & (h1["cd"] > h1["dh20"]), h1["newday"] & (h1["cd"] < h1["dl20"]), h1, 2.5, 3.0)


def daily50(h1, p):
    return _make(h1["newday"] & (h1["cd"] > h1["dh50"]), h1["newday"] & (h1["cd"] < h1["dl50"]), h1, 3.0, 4.0)


def emacross(h1, p):
    return _make(h1["newday"] & (h1["cross_up"] > 0.5), h1["newday"] & (h1["cross_dn"] > 0.5), h1, 3.0, 4.0)


S.STRATS.update({"daily20": daily20, "daily50": daily50, "emacross": emacross})
NAMES = ["daily20", "daily50", "emacross"]
UNIVERS = {"7 paires": None, "BTC+ETH": ["BTCUSDT", "ETHUSDT"]}
COSTS = {
    "reels": dict(FEE=0.0005, SLIP_IN=0.0002, SLIP_TP=0.0002, SLIP_SL=0.0005, FUNDING_PER_HOUR=0.0001 / 8),
    "maker": dict(FEE=0.0002, SLIP_IN=0.0, SLIP_TP=0.0, SLIP_SL=0.0005, FUNDING_PER_HOUR=0.0001 / 8),
}


def evaluate(prepared, name, per):
    rows, allR = [], []
    for label, a, b in per:
        tr, _, _ = B.simulate(prepared, name, {}, a, b)
        R = [t["R"] for t in tr]
        rows.append((label, len(R), float(np.mean(R)) if R else 0.0))
        allR += R
    pos = sum(1 for _, n, m in rows if n >= 2 and m > 0)
    val = sum(1 for _, n, _ in rows if n >= 2)
    return {"n": len(allR), "avgR": float(np.mean(allR)) if allR else 0.0, "t": B2.tstat(allR),
            "pos": pos, "val": val, "rows": rows}


def main(out, data=None):
    if data is None:
        if "--fetch" in sys.argv:
            B2.fetch_all()
        data = B2.load_all()
    prep_all = {p: prepare4(data[p]) for p in data}
    t_end = max(int(prep_all[p]["close_time"].iat[-1]) for p in prep_all) + 1
    per = B2.periods(t_end)
    L = ["# Backtest 4 — tendances lentes (journalier), hypotheses figees\n",
         "Janvier 2022 → aujourd'hui. Esperance en R par trade [t-stat]. Validation : t > 2 ET majorite de semestres positifs.\n",
         "| Strategie | Univers | Couts | Trades | Esperance (R) | t-stat | Semestres positifs |", "|---|---|---|---|---|---|---|"]
    detail = {}
    for name in NAMES:
        for uname, plist in UNIVERS.items():
            prepared = prep_all if plist is None else {p: prep_all[p] for p in plist if p in prep_all}
            for cname, c in COSTS.items():
                for k, v in c.items():
                    setattr(B, k, v)
                r = evaluate(prepared, name, per)
                detail[(name, uname, cname)] = r
                L.append(f"| {name} | {uname} | {cname} | {r['n']} | {r['avgR']:+.3f} | {r['t']:+.2f} | {r['pos']}/{r['val']} |")
                print(L[-1], flush=True)
    L += ["", "## Par semestre (7 paires, couts reels) — esperance [trades]\n",
          "| Semestre | " + " | ".join(NAMES) + " |", "|---|" + "---|" * len(NAMES)]
    for k, (label, _, _) in enumerate(per):
        cells = []
        for n in NAMES:
            _, cnt, m = detail[(n, "7 paires", "reels")]["rows"][k]
            cells.append(f"{m:+.2f} [{cnt}]")
        L.append(f"| {label} | " + " | ".join(cells) + " |")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "resultats_backtest4.md")
