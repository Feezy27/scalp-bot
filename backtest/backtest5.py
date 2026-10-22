#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest5.py — Les periodes du marche, et quelle strategie marche (ou non) dans chacune.

1) Decoupage OBJECTIF des phases de BTC (algorithme « zigzag » : un changement de phase =
   un retournement d'au moins 20 % depuis le dernier extreme), sans rien choisir a la main.
2) Pour chaque phase : mouvement, duree, volatilite, et esperance (R/trade) de chaque strategie.
3) Regime « en temps reel » (utilisable par un bot) : tendance (prix vs EMA200 jours, EMA50 vs EMA200)
   x volatilite (30 j vs mediane passee). On regarde si une strategie est rentable de facon
   REPETEE dans un meme regime (plusieurs episodes distincts), seule facon d'y croire.
Hypotheses des strategies figees (backtests 2 et 4). Couts reels.
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
import backtest4 as B4  # noqa: E402,F401  (enregistre les strategies journalieres)
import strategie as S  # noqa: E402

DAY = 24 * S.H
NAMES = ["turtle55", "breakout_fixe", "pullback_fixe", "squeeze", "daily20", "daily50", "emacross", "aleatoire2"]


def btc_daily(h4):
    h4 = h4.copy()
    h4["day"] = h4["open_time"] // DAY
    g = h4.groupby("day")
    d = pd.DataFrame({"t0": g["open_time"].first() , "close": g["close"].last(), "n": g["open"].count()})
    d = d[d["n"] == 6].reset_index(drop=True)
    d["date"] = pd.to_datetime(d["t0"], unit="ms").dt.strftime("%Y-%m-%d")
    return d


def zigzag(close, thr=0.20):
    """Retourne la liste des phases [(i0, i1, 'hausse'|'baisse')] par retournements >= thr."""
    pivots = [0]
    direction = 0
    ext_i = 0
    for i in range(1, len(close)):
        if direction >= 0 and close[i] >= close[ext_i]:
            ext_i = i if direction >= 0 else ext_i
        if direction == 0:
            if close[i] >= close[0] * (1 + thr):
                direction, ext_i = 1, i
            elif close[i] <= close[0] * (1 - thr):
                direction, ext_i = -1, i
            continue
        if direction == 1:
            if close[i] > close[ext_i]:
                ext_i = i
            elif close[i] <= close[ext_i] * (1 - thr):
                pivots.append(ext_i)
                direction, ext_i = -1, i
        else:
            if close[i] < close[ext_i]:
                ext_i = i
            elif close[i] >= close[ext_i] * (1 + thr):
                pivots.append(ext_i)
                direction, ext_i = 1, i
    pivots.append(len(close) - 1)
    out = []
    for a, b in zip(pivots[:-1], pivots[1:]):
        if b > a:
            out.append((a, b, "hausse" if close[b] > close[a] else "baisse"))
    return out


def main(out, data=None):
    if data is None:
        if "--fetch" in sys.argv:
            B2.fetch_all()
        data = B2.load_all()
    prepared = {p: B2.prepare2(data[p]) for p in data}
    # prepare2 ne porte pas les colonnes journalieres : on prepare 4 par-dessus
    prep4 = {p: B4.prepare4(data[p]) for p in data}
    # fusion des colonnes des deux preparations
    for p in prepared:
        extra = [c for c in prep4[p].columns if c not in prepared[p].columns]
        prepared[p] = pd.concat([prepared[p], prep4[p][extra]], axis=1)

    for k, v in B4.COSTS["reels"].items():
        setattr(B, k, v)

    # --- phases de BTC ---
    btc = btc_daily(data["BTCUSDT"]["4h"])
    btc = btc[btc["date"] >= B2.START].reset_index(drop=True)
    c = btc["close"].to_numpy()
    ph = zigzag(c, 0.20)
    ret = pd.Series(c).pct_change()
    L = ["# Backtest 5 — phases du marche et strategies\n",
         "## 1. Phases de BTC (retournements de 20 % ou plus, detectes automatiquement)\n",
         "| # | Du | Au | Type | Mouvement | Jours | Volatilite (annualisee) |", "|---|---|---|---|---|---|---|"]
    bounds = []
    for k, (a, b, typ) in enumerate(ph, 1):
        mv = (c[b] / c[a] - 1) * 100
        vol = float(ret.iloc[a + 1:b + 1].std() * np.sqrt(365) * 100) if b > a + 1 else 0.0
        t0 = int(btc["t0"].iat[a]); t1 = int(btc["t0"].iat[b]) + DAY
        bounds.append((k, t0, t1, typ, btc["date"].iat[a], btc["date"].iat[b]))
        L.append(f"| {k} | {btc['date'].iat[a]} | {btc['date'].iat[b]} | {typ} | {mv:+.0f} % | {b - a} | {vol:.0f} % |")
    bounds[0] = (bounds[0][0], B.ms(B2.START), *bounds[0][2:])

    # --- trades de chaque strategie (toute la periode, une seule simulation) ---
    t_start = B.ms(B2.START)
    t_end = max(int(prepared[p]["close_time"].iat[-1]) for p in prepared) + 1
    trades = {}
    for n in NAMES:
        tr, _, _ = B.simulate(prepared, n, {}, t_start, t_end)
        trades[n] = tr
        print(n, len(tr), flush=True)

    def cell(R):
        if len(R) == 0:
            return "–"
        return f"{np.mean(R):+.2f} [{len(R)}]"

    L += ["", "## 2. Esperance (R/trade) [nb de trades] par phase\n",
          "| # | Phase | " + " | ".join(NAMES) + " |", "|---|---|" + "---|" * len(NAMES)]
    for k, t0, t1, typ, d0, d1 in bounds:
        cells = []
        for n in NAMES:
            R = [t["R"] for t in trades[n] if t0 <= t["time"] < t1]
            cells.append(cell(R))
        L.append(f"| {k} | {d0} → {d1} ({typ}) | " + " | ".join(cells) + " |")

    # --- regime causal (utilisable en direct) ---
    s = pd.Series(c)
    e50, e200 = s.ewm(span=50, adjust=False).mean(), s.ewm(span=200, adjust=False).mean()
    trend = np.where((s > e200) & (e50 > e200), "haussier", np.where((s < e200) & (e50 < e200), "baissier", "neutre"))
    vol30 = ret.rolling(30).std()
    med = vol30.expanding(min_periods=90).median()
    volreg = np.where(vol30 > med, "agitee", "calme")
    lab = pd.Series([f"{a}/{b}" for a, b in zip(trend, volreg)])
    lab[vol30.isna() | med.isna()] = "chauffe"
    day0 = btc["t0"].to_numpy()

    def regime_at(ts):
        j = int(np.searchsorted(day0, ts // DAY * DAY, side="right")) - 2   # dernier jour CLOTURE avant l'entree
        return lab.iat[j] if 0 <= j < len(lab) else "chauffe"

    regs = sorted(set(lab) - {"chauffe"})
    L += ["", "## 3. Regime connu EN DIRECT (tendance / volatilite) — esperance [trades] ; t-stat entre parentheses\n",
          "| Regime | Jours | " + " | ".join(NAMES) + " |", "|---|---|" + "---|" * len(NAMES)]
    for rg in regs:
        cells = []
        for n in NAMES:
            R = [t["R"] for t in trades[n] if regime_at(t["time"]) == rg]
            cells.append(cell(R) + (f" ({B2.tstat(R):+.1f})" if len(R) >= 5 else ""))
        L.append(f"| {rg} | {(lab == rg).sum()} | " + " | ".join(cells) + " |")

    # --- repetition : le regime « rentable » l'est-il dans plusieurs episodes distincts ? ---
    L += ["", "## 4. Constance : par regime, nb d'annees civiles ou la strategie est positive (annees avec >= 3 trades)\n",
          "| Regime | " + " | ".join(NAMES) + " |", "|---|" + "---|" * len(NAMES)]
    for rg in regs:
        cells = []
        for n in NAMES:
            by = {}
            for t in trades[n]:
                if regime_at(t["time"]) == rg:
                    y = pd.to_datetime(t["time"], unit="ms").year
                    by.setdefault(y, []).append(t["R"])
            ok = [y for y, R in by.items() if len(R) >= 3]
            cells.append(f"{sum(1 for y in ok if np.mean(by[y]) > 0)}/{len(ok)}")
        L.append(f"| {rg} | " + " | ".join(cells) + " |")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "resultats_backtest5.md")
