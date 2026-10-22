# -*- coding: utf-8 -*-
"""
donnees.py — Bougies de marche (Binance, meme source que le backtest).
Ne renvoie que des bougies CLOTUREES.
"""
import json
import time
import urllib.request

import pandas as pd

from strategie import INTERVALS

ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
]


def klines(pair, interval, limit=600):
    last = None
    for base in ENDPOINTS:
        url = f"{base}?symbol={pair}&interval={interval}&limit={limit}"
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                rows = json.loads(r.read())
            break
        except Exception as e:
            last = e
            rows = None
    if not rows:
        raise RuntimeError(f"donnees indisponibles pour {pair} {interval} ({type(last).__name__})")
    step = INTERVALS[interval] * 60_000
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=["open_time", "open", "high", "low", "close", "volume"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["open_time"] + step
    now = int(time.time() * 1000)
    return df[df["close_time"] <= now].reset_index(drop=True)


def jeu_complet(pair):
    return {"15m": klines(pair, "15m", 1000), "1h": klines(pair, "1h", 1000),
            "4h": klines(pair, "4h", 1000)}
