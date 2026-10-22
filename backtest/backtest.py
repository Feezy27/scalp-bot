#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest.py — Banc d'essai honnete des strategies ScalpRadar.

Principes (pour ne pas se mentir) :
  * Bougies CLOTUREES uniquement. Signal sur la bougie t, entree a l'ouverture de t+1.
  * Multi-timeframe aligne sur l'heure de cloture : jamais d'information future.
  * Frais BingX taker 0.05 % par cote + glissement (0.02 % entree/TP, 0.05 % sur SL)
    + funding forfaitaire 0.01 % / 8 h.
  * Si SL et TP sont touches dans la meme bougie : on suppose le SL (pessimiste).
  * Une seule position a la fois sur l'ensemble des paires (comme le bot reel).
  * Risque fixe en % du capital, levier/notionnel plafonne.
  * Walk-forward : parametres choisis sur la periode d'apprentissage,
    jugement UNIQUEMENT sur la periode de test jamais vue.

Usage :
  python backtest.py --fetch            # telecharge les donnees Binance (GitHub Actions)
  python backtest.py                    # lance le banc d'essai sur data/
  python backtest.py --synthetic        # auto-test sur donnees synthetiques
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT"]
START = "2025-07-01"
SPLIT = "2026-04-01"          # avant = apprentissage, apres = test (6+ mois jamais vus)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

FEE = 0.0005                   # taker BingX par cote
SLIP_IN = 0.0002
SLIP_TP = 0.0002
SLIP_SL = 0.0005
FUNDING_PER_HOUR = 0.0001 / 8  # 0.01 % par 8 h
RISK = 0.02                    # 2 % du capital risque par trade
NOTIONAL_CAP = 5.0             # position <= 5x le capital



# ══════════════════════════════════════════════════════════════════
# DONNEES
# ══════════════════════════════════════════════════════════════════
def fetch(pair, interval, start_ms, end_ms):
    rows, cur = [], start_ms
    step = INTERVALS[interval] * 60_000
    while cur < end_ms:
        url = (f"https://data-api.binance.vision/api/v3/klines?symbol={pair}"
               f"&interval={interval}&startTime={cur}&limit=1000")
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    batch = json.loads(r.read())
                break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(2 + attempt * 2)
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + step
        if len(batch) < 1000:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume",
                                     "close_time", "q", "n", "tb", "tq", "i"])
    df = df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    # close_time Binance = ouverture suivante - 1 ms ; on normalise a l'ouverture suivante
    df["close_time"] = df["open_time"] + step
    return df.drop_duplicates("open_time").reset_index(drop=True)


def fetch_all():
    os.makedirs(DATA_DIR, exist_ok=True)
    start = int(datetime.fromisoformat(START).replace(tzinfo=timezone.utc).timestamp() * 1000)
    start -= 60 * 24 * H  # 60 jours de chauffe pour les indicateurs longs
    end = int(time.time() * 1000)
    for p in PAIRS:
        for itv in INTERVALS:
            df = fetch(p, itv, start, end)
            now = int(time.time() * 1000)
            df = df[df["close_time"] <= now]  # retire la bougie en cours
            df.to_csv(os.path.join(DATA_DIR, f"{p}_{itv}.csv"), index=False)
            print(f"  {p} {itv}: {len(df)} bougies", flush=True)


def load_all():
    data = {}
    for p in PAIRS:
        data[p] = {}
        for itv in INTERVALS:
            data[p][itv] = pd.read_csv(os.path.join(DATA_DIR, f"{p}_{itv}.csv"))
    return data


from strategie import *  # noqa: F401,F403 — moteur partage avec le bot reel


# ══════════════════════════════════════════════════════════════════
# SIMULATION DE PORTEFEUILLE
# ══════════════════════════════════════════════════════════════════
def simulate(prepared, strat, params, t0, t1, equity0=100.0):
    pairs = list(prepared.keys())
    sigs = {}
    for p in pairs:
        for (i, d, sl, tp, mh, tr) in STRATS[strat](prepared[p], params):
            ct = int(prepared[p]["close_time"].iat[i])
            if t0 <= ct < t1 and np.isfinite(sl) and (tp is None or np.isfinite(tp)):
                sigs.setdefault(ct, []).append((p, i, d, sl, tp, mh, tr))

    arr = {p: {k: prepared[p][k].to_numpy() for k in ("open", "high", "low", "close", "close_time")}
           for p in pairs}
    idx = {p: {int(t): j for j, t in enumerate(arr[p]["close_time"])} for p in pairs}

    equity, peak, mdd = equity0, equity0, 0.0
    trades, busy_until = [], -1
    for ct in sorted(sigs):
        if ct <= busy_until:
            continue
        p, i, d, sl, tp, mh, trail = sorted(sigs[ct], key=lambda x: pairs.index(x[0]))[0]
        A = arr[p]
        j = i + 1
        if j >= len(A["open"]):
            continue
        entry = A["open"][j] * (1 + d * SLIP_IN)
        dist = (entry - sl) * d
        if dist <= 0:
            continue
        risk_cash = equity * RISK
        qty = risk_cash / dist
        notional = qty * entry
        if notional > equity * NOTIONAL_CAP:
            qty = equity * NOTIONAL_CAP / entry
            notional = qty * entry
        if tp is not None and (tp - entry) * d <= 0:
            continue

        stop, best = sl, entry
        exit_px, reason, k = None, None, j
        last = min(len(A["open"]) - 1, j + mh)
        for k in range(j, last + 1):
            o, hi, lo = A["open"][k], A["high"][k], A["low"][k]
            if d > 0:
                if o <= stop:
                    exit_px, reason = o * (1 - SLIP_SL), "SL"
                elif lo <= stop:
                    exit_px, reason = stop * (1 - SLIP_SL), "SL"
                elif tp is not None and hi >= tp:
                    exit_px, reason = tp * (1 - SLIP_TP), "TP"
            else:
                if o >= stop:
                    exit_px, reason = o * (1 + SLIP_SL), "SL"
                elif hi >= stop:
                    exit_px, reason = stop * (1 + SLIP_SL), "SL"
                elif tp is not None and lo <= tp:
                    exit_px, reason = tp * (1 + SLIP_TP), "TP"
            if exit_px is not None:
                break
            if trail is not None:  # stop suiveur mis a jour a la cloture
                best = max(best, A["high"][k]) if d > 0 else min(best, A["low"][k])
                new_stop = best - d * trail
                stop = max(stop, new_stop) if d > 0 else min(stop, new_stop)
        if exit_px is None:
            exit_px, reason = A["close"][k] * (1 - d * SLIP_TP), "TEMPS"

        hours = k - j + 1
        gross = qty * (exit_px - entry) * d
        costs = FEE * (notional + qty * exit_px) + FUNDING_PER_HOUR * hours * notional
        pnl = gross - costs
        equity += pnl
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
        trades.append({"time": int(A["close_time"][j]), "pair": p, "dir": d,
                       "R": pnl / risk_cash, "pnl_pct": pnl / (equity - pnl) * 100,
                       "reason": reason, "hours": hours})
        busy_until = int(A["close_time"][k])
    return trades, equity / equity0 - 1, mdd


def stats(trades, ret, mdd, months):
    n = len(trades)
    if n == 0:
        return {"n": 0, "win": 0, "avgR": 0, "pf": 0, "ret": 0, "mdd": 0, "tpm": 0, "hold": 0}
    R = np.array([t["R"] for t in trades])
    gains, losses = R[R > 0].sum(), -R[R < 0].sum()
    return {"n": n, "win": float((R > 0).mean() * 100), "avgR": float(R.mean()),
            "pf": float(gains / losses) if losses > 0 else float("inf"),
            "ret": float(ret * 100), "mdd": float(mdd * 100), "tpm": n / months,
            "hold": float(np.mean([t["hours"] for t in trades]))}


GRIDS = {
    "actuelle": [{}],
    "pullback": [{"tp_r": r, "adx": a} for r in (1.5, 2.0, 2.5) for a in (20, 25)],
    "breakout": [{"adx": a, "trail": t} for a in (20, 25) for t in (2.5, 3.5)],
    "range": [{"adx_max": a} for a in (15, 20)],
    "regime": [{"tp_r": r, "adx": 25, "adx_max": m} for r in (2.0, 2.5) for m in (15, 18)],
    "aleatoire": [{"seed": s} for s in (1,)],
}


def ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def run(data):
    prepared = {p: prepare(data[p]) for p in data}
    t_start, t_split = ms(START), ms(SPLIT)
    t_end = max(int(prepared[p]["close_time"].iat[-1]) for p in prepared) + 1
    m_is = (t_split - t_start) / (30.44 * 24 * H)
    m_oos = (t_end - t_split) / (30.44 * 24 * H)

    results = {}
    for name, grid in GRIDS.items():
        best, best_key = None, None
        for params in grid:
            tr, ret, mdd = simulate(prepared, name, params, t_start, t_split)
            st = stats(tr, ret, mdd, m_is)
            key = st["avgR"] if st["n"] >= 25 else -9
            if best is None or key > best_key:
                best, best_key = (params, st), key
        params, st_is = best
        tr, ret, mdd = simulate(prepared, name, params, t_split, t_end)
        st_oos = stats(tr, ret, mdd, m_oos)
        per_pair = {}
        for p in prepared:
            trp, retp, mddp = simulate({p: prepared[p]}, name, params, t_split, t_end)
            per_pair[p] = stats(trp, retp, mddp, m_oos)
        results[name] = {"params": params, "is": st_is, "oos": st_oos,
                         "oos_pairs": per_pair,
                         "oos_trades": tr}
        print(f"{name:10} params={params}  IS avgR={st_is['avgR']:+.2f} n={st_is['n']}"
              f"  |  OOS avgR={st_oos['avgR']:+.2f} n={st_oos['n']} ret={st_oos['ret']:+.1f}%"
              f" mdd={st_oos['mdd']:.1f}%", flush=True)

    # Reference : achat-conservation BTC sur la periode de test
    b = prepared["BTCUSDT"] if "BTCUSDT" in prepared else next(iter(prepared.values()))
    sel = b[(b["close_time"] >= t_split)]
    bh = sel["close"].iat[-1] / sel["open"].iat[0] - 1 if len(sel) else 0
    return results, {"m_is": m_is, "m_oos": m_oos, "btc_bh_oos": bh * 100,
                     "end": datetime.fromtimestamp(t_end / 1000, timezone.utc).strftime("%Y-%m-%d")}


def report(results, meta, path):
    L = []
    L.append("# Backtest ScalpRadar — resultats\n")
    L.append(f"Periode d'apprentissage : {START} → {SPLIT} ({meta['m_is']:.1f} mois)  ")
    L.append(f"Periode de TEST (jamais vue) : {SPLIT} → {meta['end']} ({meta['m_oos']:.1f} mois)  ")
    L.append(f"Risque 2 % par trade, frais + glissement + funding inclus, 1 position a la fois.  ")
    L.append(f"Reference : BTC achat-conservation sur la periode de test = {meta['btc_bh_oos']:+.1f} %\n")
    L.append("## Periode de test (le seul chiffre qui compte)\n")
    L.append("| Strategie | Trades | Trades/mois | Gagnants | Esperance (R) | Profit factor | Rendement | Pire baisse |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k, v in results.items():
        o = v["oos"]
        L.append(f"| {k} | {o['n']} | {o['tpm']:.1f} | {o['win']:.0f} % | {o['avgR']:+.2f} | "
                 f"{o['pf']:.2f} | {o['ret']:+.1f} % | {o['mdd']:.1f} % |")
    L.append("\n## Periode d'apprentissage\n")
    L.append("| Strategie | Parametres retenus | Trades | Gagnants | Esperance (R) | Profit factor | Rendement |")
    L.append("|---|---|---|---|---|---|---|")
    for k, v in results.items():
        i = v["is"]
        L.append(f"| {k} | `{v['params']}` | {i['n']} | {i['win']:.0f} % | {i['avgR']:+.2f} | "
                 f"{i['pf']:.2f} | {i['ret']:+.1f} % |")
    L.append("\n## Test par paire (stabilite)\n")
    for k, v in results.items():
        if k == "aleatoire":
            continue
        cells = " · ".join(f"{p.replace('USDT','')} {s['avgR']:+.2f}R/{s['n']}"
                           for p, s in v["oos_pairs"].items())
        L.append(f"- **{k}** : {cells}")
    open(path, "w").write("\n".join(L) + "\n")
    slim = {k: {kk: vv for kk, vv in v.items() if kk != "oos_trades"} for k, v in results.items()}
    json.dump({"meta": meta, "results": slim}, open(path.replace(".md", ".json"), "w"), indent=1)


# ══════════════════════════════════════════════════════════════════
# AUTO-TEST SYNTHETIQUE
# ══════════════════════════════════════════════════════════════════
def synthetic(seed=0):
    rng = np.random.default_rng(seed)
    t0 = ms(START) - 60 * 24 * H
    n15 = int((ms("2026-10-21") - t0) / (15 * 60_000))
    drift = np.repeat(rng.normal(0, 0.0004, n15 // 2000 + 1), 2000)[:n15]
    r = drift + rng.normal(0, 0.003, n15)
    close = 100 * np.exp(np.cumsum(r))
    m15 = pd.DataFrame({"open_time": t0 + np.arange(n15) * 900_000,
                        "close": close})
    m15["open"] = m15["close"].shift(1).fillna(100)
    noise = np.abs(rng.normal(0, 0.0015, n15))
    m15["high"] = np.maximum(m15["open"], m15["close"]) * (1 + noise)
    m15["low"] = np.minimum(m15["open"], m15["close"]) * (1 - noise)
    m15["volume"] = rng.lognormal(8, 0.4, n15)

    def resample(df, k):
        g = df.groupby(np.arange(len(df)) // k)
        out = pd.DataFrame({"open_time": g["open_time"].first(), "open": g["open"].first(),
                            "high": g["high"].max(), "low": g["low"].min(),
                            "close": g["close"].last(), "volume": g["volume"].sum()})
        return out.reset_index(drop=True)

    d = {"15m": m15, "1h": resample(m15, 4), "4h": resample(m15, 16)}
    for itv, df in d.items():
        df["close_time"] = df["open_time"] + INTERVALS[itv] * 60_000
    return d


def lookahead_test(d):
    """Les signaux a l'instant t ne doivent PAS changer si on retire le futur."""
    full = prepare(d)
    cut = int(full["close_time"].iat[len(full) * 2 // 3])
    trunc = {k: v[v["close_time"] <= cut].copy() for k, v in d.items()}
    part = prepare(trunc)
    cols = ["score1", "score4", "score15", "adx", "ema50", "atr", "sw_lo", "sw_hi", "rsi", "dc_hi"]
    a = full[full["close_time"] <= cut][cols].iloc[:-2].reset_index(drop=True)
    b = part[cols].iloc[:-2].reset_index(drop=True)
    diff = (a - b).abs().max().max()
    return diff


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="resultats_backtest.md")
    a = ap.parse_args()
    if a.fetch:
        print("Telechargement des donnees Binance…", flush=True)
        fetch_all()
    if a.synthetic:
        d = synthetic()
        print(f"Test anti-triche (ecart max en retirant le futur) : {lookahead_test(d):.2e}")
        data = {"BTCUSDT": d, "ETHUSDT": synthetic(1)}
    else:
        data = load_all()
    res, meta = run(data)
    report(res, meta, a.out)
    print(f"\nRapport ecrit : {a.out}")
