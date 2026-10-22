#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest3.py — D'ou vient la perte ? Direction ou frais ?

Pour chaque strategie de backtest2 (reglages figes), on mesure l'esperance (R/trade) :
  * normale, avec les couts reels (taker 0.05 %, glissement, funding)
  * INVERSEE (direction opposee, stop/objectif symetriques), avec les couts reels
  * normale SANS aucun cout  -> « gain brut » : y a-t-il un signal avant frais ?
  * inversee SANS aucun cout
  * normale avec frais « maker » 0.02 % (ordres limites) et sans glissement
Meme periode que backtest2 (janv. 2022 -> aujourd'hui), meme donnees.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import backtest as B  # noqa: E402
import backtest2 as B2  # noqa: E402
import strategie as S  # noqa: E402

BASE = ["turtle55", "breakout_fixe", "pullback_fixe", "squeeze", "aleatoire2"]


def inverse(fn):
    def w(h1, p):
        out = []
        for (i, d, sl, tp, mh, tr) in fn(h1, p):
            e = h1["close"].iat[i]
            out.append((i, -d, 2 * e - sl, None if tp is None else 2 * e - tp, mh, tr))
        return out
    return w


for n in BASE:
    S.STRATS[n + "_inv"] = inverse(S.STRATS[n])

COSTS = {
    "reel": dict(FEE=B.FEE, SLIP_IN=B.SLIP_IN, SLIP_TP=B.SLIP_TP, SLIP_SL=B.SLIP_SL, FUNDING_PER_HOUR=B.FUNDING_PER_HOUR),
    "sans_couts": dict(FEE=0.0, SLIP_IN=0.0, SLIP_TP=0.0, SLIP_SL=0.0, FUNDING_PER_HOUR=0.0),
    "maker": dict(FEE=0.0002, SLIP_IN=0.0, SLIP_TP=0.0, SLIP_SL=0.0005, FUNDING_PER_HOUR=B.FUNDING_PER_HOUR),
}


def avg_R(prepared, name, t0, t1):
    tr, _, _ = B.simulate(prepared, name, {}, t0, t1)
    R = [t["R"] for t in tr]
    return len(R), (float(np.mean(R)) if R else 0.0), B2.tstat(R)


def main(out, data=None):
    if data is None:
        if "--fetch" in sys.argv:
            B2.fetch_all()
        data = B2.load_all()
    prepared = {p: B2.prepare2(data[p]) for p in data}
    t_end = max(int(prepared[p]["close_time"].iat[-1]) for p in prepared) + 1
    t0 = B.ms(B2.START)
    rows = []
    for n in BASE:
        for variant in (n, n + "_inv"):
            line = {"strat": variant}
            for cname, c in COSTS.items():
                for k, v in c.items():
                    setattr(B, k, v)
                line[cname] = avg_R(prepared, variant, t0, t_end)
            rows.append(line)
            print(variant, {k: (v[0], round(v[1], 3), round(v[2], 2)) for k, v in line.items() if k != "strat"}, flush=True)
    L = ["# Backtest 3 — la perte vient-elle de la direction ou des frais ?\n",
         "Esperance en R par trade [t-stat], janvier 2022 → aujourd'hui, memes donnees que backtest 2.\n",
         "| Strategie | Trades | Couts reels | Sans aucun cout | Frais maker 0.02 % |", "|---|---|---|---|---|"]
    for r in rows:
        f = lambda x: f"{x[1]:+.3f} [{x[2]:+.1f}]"
        L.append(f"| {r['strat']} | {r['reel'][0]} | {f(r['reel'])} | {f(r['sans_couts'])} | {f(r['maker'])} |")
    open(out, "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "resultats_backtest3.md")
