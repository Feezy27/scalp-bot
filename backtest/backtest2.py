#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest2.py — Deuxieme serie de tests, sans AUCUN reglage ajuste sur les donnees.

Pourquoi : la 1re serie a montre qu'un reglage choisi sur une periode ne tient pas sur la
suivante (« breakout » : +0.28R appris, -0.21R test). Ici :
  * hypotheses fixees d'avance, parametres figes (valeurs classiques, pas de grille) ;
  * 4 ans et 10 mois de donnees (jan. 2022 -> aujourd'hui) : marche baissier 2022,
    reprise 2023-2024, sommet 2025, repli 2026 → plusieurs regimes ;
  * resultat decoupe par semestre : une strategie n'est retenue que si elle est positive
    dans la plupart des semestres ET au total (t-stat), pas sur un seul morceau chanceux.
Memes couts que backtest.py (frais, glissement, funding), memes regles anti-biais.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest as B  # noqa: E402
import strategie as S  # noqa: E402

START = "2022-01-01"
DATA2 = os.path.join(HERE, "data2")
H = S.H


# ───────────────────────── donnees (1h + 4h seulement) ─────────────────────────
def fetch_all():
    os.makedirs(DATA2, exist_ok=True)
    start = B.ms(START) - 60 * 24 * H
    for p in B.PAIRS:
        for itv in ("1h", "4h"):
            df = B.fetch(p, itv, start, int(time.time() * 1000))
            df = df[df["close_time"] <= int(time.time() * 1000)]
            df.to_csv(os.path.join(DATA2, f"{p}_{itv}.csv"), index=False)
            print(f"  {p} {itv}: {len(df)} bougies", flush=True)


def load_all():
    d = {}
    for p in B.PAIRS:
        h1 = pd.read_csv(os.path.join(DATA2, f"{p}_1h.csv"))
        h4 = pd.read_csv(os.path.join(DATA2, f"{p}_4h.csv"))
        d[p] = {"1h": h1, "4h": h4, "15m": h1.copy()}   # 15m inutile ici (maquette)
    return d


def prepare2(d):
    h1 = S.prepare(d)
    h4 = d["4h"].copy()
    a = S.atr(h4)
    h4["atr4b"] = a
    h4["dc55_hi"] = h4["high"].shift(1).rolling(55).max()
    h4["dc55_lo"] = h4["low"].shift(1).rolling(55).min()
    bm, bu, bl = S.bollinger(h4["close"], 20, 2.0)
    h4["bbw"] = (bu - bl) / bm
    h4["bbw_rank"] = h4["bbw"].rolling(100).apply(lambda x: (x[-1] >= x).mean(), raw=True)
    h4["bb_u4"], h4["bb_l4"] = bu, bl
    h4["c4b"] = h4["close"]
    cols = ["dc55_hi", "dc55_lo", "bbw_rank", "bb_u4", "bb_l4", "c4b"]
    h1 = pd.concat([h1, S.align(h1, h4, cols)], axis=1)
    h1["bbw_rank_prev"] = h1["bbw_rank"].shift(4)   # etat avant la bougie 4h courante
    return h1


# ───────────────────────── strategies (parametres FIGES) ─────────────────────────
def strat_turtle55(h1, p):
    """Tendance lente : cassure du plus haut/bas de 55 bougies 4h, long ET short.
    Stop initial 3 ATR(4h), stop suiveur 4 ATR(4h). Valeurs classiques, non ajustees."""
    new = h1["new4h"]
    lg = new & (h1["c4b"] > h1["dc55_hi"])
    sh = new & (h1["c4b"] < h1["dc55_lo"])
    out = []
    for i in np.flatnonzero((lg | sh).to_numpy()):
        r = h1.iloc[i]
        d = 1 if lg.iat[i] else -1
        e, a4 = r["close"], r["atr4"]
        out.append((i, d, e - d * 3.0 * a4, None, 720, 4.0 * a4))
    return out


def strat_squeeze(h1, p):
    """Compression de volatilite (largeur de Bollinger 4h parmi les 20 % les plus basses
    des 100 dernieres bougies) puis cassure de bande. Stop 2 ATR, suiveur 3 ATR."""
    new = h1["new4h"]
    squeezed = h1["bbw_rank_prev"] <= 0.20
    lg = new & squeezed & (h1["c4b"] > h1["bb_u4"])
    sh = new & squeezed & (h1["c4b"] < h1["bb_l4"])
    out = []
    for i in np.flatnonzero((lg | sh).to_numpy()):
        r = h1.iloc[i]
        d = 1 if lg.iat[i] else -1
        e, a4 = r["close"], r["atr4"]
        out.append((i, d, e - d * 2.0 * a4, None, 480, 3.0 * a4))
    return out


def strat_breakout_fixe(h1, p):
    return S.strat_breakout(h1, {"adx": 25, "trail": 3.5})


def strat_pullback_fixe(h1, p):
    return S.strat_pullback(h1, {"tp_r": 2.5, "adx": 25})


def strat_alea(h1, p):
    return S.strat_random(h1, {"seed": 1})


S.STRATS.update({"turtle55": strat_turtle55, "squeeze": strat_squeeze,
                 "breakout_fixe": strat_breakout_fixe, "pullback_fixe": strat_pullback_fixe,
                 "aleatoire2": strat_alea})
NAMES = ["turtle55", "squeeze", "breakout_fixe", "pullback_fixe", "aleatoire2"]


# ───────────────────────── execution ─────────────────────────
def periods(t_end):
    out = []
    y, h = 2022, 0
    while True:
        a = B.ms(f"{y}-{'01' if h == 0 else '07'}-01")
        y2, h2 = (y, 1) if h == 0 else (y + 1, 0)
        b = B.ms(f"{y2}-{'01' if h2 == 0 else '07'}-01")
        if a >= t_end:
            break
        out.append((f"{y}-S{h + 1}", a, min(b, t_end)))
        y, h = y2, h2
    return out


def tstat(R):
    R = np.asarray(R)
    if len(R) < 3 or R.std(ddof=1) == 0:
        return 0.0
    return float(R.mean() / (R.std(ddof=1) / np.sqrt(len(R))))


def run(data):
    prepared = {p: prepare2(data[p]) for p in data}
    t_end = max(int(prepared[p]["close_time"].iat[-1]) for p in prepared) + 1
    per = periods(t_end)
    res = {}
    for name in NAMES:
        rows, allR = [], []
        for label, a, b in per:
            tr, ret, mdd = B.simulate(prepared, name, {}, a, b)
            months = (b - a) / (30.44 * 24 * H)
            st = B.stats(tr, ret, mdd, months)
            st["label"] = label
            rows.append(st)
            allR += [t["R"] for t in tr]
        pairs = {}
        for p in prepared:
            tr, _, _ = B.simulate({p: prepared[p]}, name, {}, per[0][1], t_end)
            R = [t["R"] for t in tr]
            pairs[p] = {"n": len(R), "avgR": float(np.mean(R)) if R else 0.0}
        res[name] = {"periods": rows, "n": len(allR),
                     "avgR": float(np.mean(allR)) if allR else 0.0, "t": tstat(allR),
                     "pos_periods": sum(1 for r in rows if r["n"] >= 3 and r["avgR"] > 0),
                     "valid_periods": sum(1 for r in rows if r["n"] >= 3), "pairs": pairs}
        print(f"{name:14} n={res[name]['n']:4d} avgR={res[name]['avgR']:+.3f} t={res[name]['t']:+.2f} "
              f"semestres>0: {res[name]['pos_periods']}/{res[name]['valid_periods']}", flush=True)
    # reference BTC
    btc = prepared["BTCUSDT"]
    bh = []
    for label, a, b in per:
        sel = btc[(btc["close_time"] >= a) & (btc["close_time"] < b)]
        bh.append((label, float(sel["close"].iat[-1] / sel["open"].iat[0] - 1) * 100 if len(sel) else 0.0))
    return res, bh, per


def report(res, bh, path):
    L = ["# Backtest 2 — hypotheses figees, janvier 2022 → aujourd'hui\n",
         "Aucun reglage ajuste sur ces donnees. Risque 2 % par trade, frais + glissement + funding inclus.  ",
         "Critere de validation : esperance positive au total (t-stat > 2) ET dans la majorite des semestres.\n",
         "## Synthese\n",
         "| Strategie | Trades | Esperance (R) | t-stat | Semestres positifs |",
         "|---|---|---|---|---|"]
    for n in NAMES:
        r = res[n]
        L.append(f"| {n} | {r['n']} | {r['avgR']:+.3f} | {r['t']:+.2f} | {r['pos_periods']}/{r['valid_periods']} |")
    L += ["", "## Esperance (R) par semestre — [nb de trades]\n",
          "| Semestre | BTC achat-conserv. | " + " | ".join(NAMES) + " |",
          "|---|---|" + "---|" * len(NAMES)]
    for k, (label, b) in enumerate(bh):
        cells = []
        for n in NAMES:
            s = res[n]["periods"][k]
            cells.append(f"{s['avgR']:+.2f} [{s['n']}]")
        L.append(f"| {label} | {b:+.0f} % | " + " | ".join(cells) + " |")
    L += ["", "## Par paire (toute la periode)\n"]
    for n in NAMES:
        L.append(f"- **{n}** : " + " · ".join(f"{p[:-4]} {v['avgR']:+.2f}R/{v['n']}" for p, v in res[n]["pairs"].items()))
    open(path, "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    out = "resultats_backtest2.md"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    if "--fetch" in sys.argv:
        fetch_all()
    res, bh, per = run(load_all())
    report(res, bh, out)
    json.dump({"res": res, "bh": bh}, open(out.replace(".md", ".json"), "w"), default=float)
    print(open(out).read())
