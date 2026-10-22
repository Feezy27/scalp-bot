# -*- coding: utf-8 -*-
"""
strategie.py — Moteur d'analyse UNIQUE, partage par le backtest et le bot reel.
Ce qui est teste est exactement ce qui tourne.

Toutes les fonctions sont causales : la valeur a la bougie t n'utilise que
des bougies cloturees jusqu'a t (verifie par un test anti-triche).
"""
import numpy as np
import pandas as pd

INTERVALS = {"15m": 15, "1h": 60, "4h": 240}
H = 3_600_000  # 1 heure en ms


# ══════════════════════════════════════════════════════════════════
# INDICATEURS (vectorises, causaux)
# ══════════════════════════════════════════════════════════════════
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def true_range(df):
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
                     axis=1).max(axis=1)


def atr(df, n=14):
    return true_range(df).rolling(n).mean()


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df)
    a = 1 / n
    trs = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=a, adjust=False).mean() / trs
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=a, adjust=False).mean() / trs
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=a, adjust=False).mean()


def bollinger(close, n=20, k=2.0):
    m = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return m, m + k * sd, m - k * sd


def score_series(df):
    """Reproduction vectorisee de score_from() de la version actuelle (-9..+9)."""
    c = df["close"]
    r = rsi(c)
    s = pd.Series(0, index=df.index, dtype=float)
    s += np.select([r < 30, r < 42, r > 70, r > 58], [2, 1, -2, -1], 0)
    e9, e21 = ema(c, 9), ema(c, 21)
    pe9, pe21 = e9.shift(1), e21.shift(1)
    s += np.select([(pe9 < pe21) & (e9 > e21), (pe9 > pe21) & (e9 < e21), e9 > e21], [2, -2, 1], -1)
    macd_line = ema(c, 12) - ema(c, 26)
    hist = macd_line - ema(macd_line, 9)
    ph = hist.shift(1)
    s += np.select([(hist > 0) & (hist > ph), hist > 0, (hist < 0) & (hist < ph)], [2, 1, -2], -1)
    m, u, l = bollinger(c)
    pb = (c - l) / (u - l)
    s += np.select([pb < 0.1, pb < 0.3, pb > 0.9, pb > 0.7], [2, 1, -2, -1], 0)
    vr = df["volume"] / df["volume"].shift(1).rolling(20).mean()
    s += np.where(vr > 1.5, np.where(c > c.shift(1), 1, -1), 0)
    return s


def swing_low(df, look=20):
    l = df["low"]
    flag = (l < l.shift(1)) & (l < l.shift(-1))     # minimum local (connu a j+1)
    masked = l.where(flag, np.inf)
    m = masked.shift(1).rolling(look - 2).min()      # a t : minima confirmes jusqu'a t-1
    return pd.Series(np.where(np.isinf(m), l.rolling(look).min(), m), index=df.index)


def swing_high(df, look=20):
    h = df["high"]
    flag = (h > h.shift(1)) & (h > h.shift(-1))
    masked = h.where(flag, -np.inf)
    m = masked.shift(1).rolling(look - 2).max()
    return pd.Series(np.where(np.isinf(m), h.rolling(look).max(), m), index=df.index)


def align(base, other, cols):
    """Pour chaque bougie de `base`, valeurs de la derniere bougie CLOTUREE de `other`."""
    o = other[["close_time"] + cols].rename(columns={c: c for c in cols}).sort_values("close_time")
    b = base[["close_time"]].copy()
    b["_k"] = b["close_time"]
    o = o.rename(columns={"close_time": "_k"})
    merged = pd.merge_asof(b.sort_values("_k"), o, on="_k", direction="backward")
    return merged[cols].set_index(base.index)


# ══════════════════════════════════════════════════════════════════
# PREPARATION PAR PAIRE
# ══════════════════════════════════════════════════════════════════
def prepare(d):
    h1, h4, m15 = d["1h"].copy(), d["4h"].copy(), d["15m"].copy()

    # 4h
    h4["ema50"] = ema(h4["close"], 50)
    h4["ema200"] = ema(h4["close"], 200)
    h4["adx"] = adx(h4)
    h4["atr4"] = atr(h4)
    h4["score4"] = score_series(h4)
    h4["dc_hi"] = h4["high"].shift(1).rolling(20).max()
    h4["dc_lo"] = h4["low"].shift(1).rolling(20).min()
    h4["c4"] = h4["close"]
    # 15m
    m15["score15"] = score_series(m15)

    # 1h
    h1["atr"] = atr(h1)
    h1["ema20"] = ema(h1["close"], 20)
    h1["rsi"] = rsi(h1["close"])
    h1["score1"] = score_series(h1)
    bm, bu, bl = bollinger(h1["close"])
    h1["bb_m"], h1["bb_u"], h1["bb_l"] = bm, bu, bl
    h1["sw_lo"] = swing_low(h1)
    h1["sw_hi"] = swing_high(h1)
    h1["lo5"] = h1["low"].rolling(5).min()
    h1["hi5"] = h1["high"].rolling(5).max()
    rng = h1["close"].rolling(20).max() - h1["close"].rolling(20).min()
    h1["ranging"] = rng < 3.5 * h1["atr"]

    a4 = align(h1, h4, ["ema50", "ema200", "adx", "atr4", "score4", "dc_hi", "dc_lo", "c4"])
    a15 = align(h1, m15, ["score15"])
    h1 = pd.concat([h1, a4, a15], axis=1)
    # Nouvelle bougie 4h cloturee sur cette heure ?
    h1["new4h"] = (h1["close_time"] % (4 * H)) == 0
    return h1


# ══════════════════════════════════════════════════════════════════
# STRATEGIES : renvoient (direction, sl, tp, max_heures, trail_atr)
# ══════════════════════════════════════════════════════════════════
def clamp_sl(entry, raw_sl, a, lo, hi, direction):
    if direction > 0:
        sl = min(raw_sl, entry - lo * a)
        return max(sl, entry - hi * a)
    sl = max(raw_sl, entry + lo * a)
    return min(sl, entry + hi * a)


def strat_actuelle(h1, p):
    """Version actuellement deployee (swing 4h/1h/15m, score + filtres)."""
    long_ = (h1["score4"] >= 2) & (h1["score1"] >= 3) & (h1["score15"] > 0) \
        & (h1["c4"] > h1["ema50"]) & ~h1["ranging"]
    short = (h1["score4"] <= -2) & (h1["score1"] <= -3) & (h1["score15"] < 0) \
        & (h1["c4"] < h1["ema50"]) & ~h1["ranging"]
    out = []
    for i in np.flatnonzero((long_ | short).to_numpy()):
        r = h1.iloc[i]
        d = 1 if long_.iat[i] else -1
        e, a = r["close"], r["atr"]
        raw = r["sw_lo"] - 0.7 * a if d > 0 else r["sw_hi"] + 0.7 * a
        sl = clamp_sl(e, raw, a, 1.5, 3.5, d)
        tp = e + d * abs(e - sl) * 2.0
        out.append((i, d, sl, tp, 72, None))
    return out


def strat_pullback(h1, p):
    """Suivi de tendance : tendance 4h confirmee, entree sur repli vers EMA20 1h."""
    tp_r, adx_min = p.get("tp_r", 2.0), p.get("adx", 20)
    up = (h1["ema50"] > h1["ema200"]) & (h1["c4"] > h1["ema50"]) & (h1["adx"] > adx_min)
    dn = (h1["ema50"] < h1["ema200"]) & (h1["c4"] < h1["ema50"]) & (h1["adx"] > adx_min)
    touched_dn = (h1["low"].rolling(3).min() <= h1["ema20"])
    touched_up = (h1["high"].rolling(3).max() >= h1["ema20"])
    rsi_up = (h1["rsi"] > 45) & (h1["rsi"].shift(1) <= 45)
    rsi_dn = (h1["rsi"] < 55) & (h1["rsi"].shift(1) >= 55)
    long_ = up & touched_dn & (h1["close"] > h1["ema20"]) & rsi_up & (h1["close"] > h1["open"])
    short = dn & touched_up & (h1["close"] < h1["ema20"]) & rsi_dn & (h1["close"] < h1["open"])
    out = []
    for i in np.flatnonzero((long_ | short).to_numpy()):
        r = h1.iloc[i]
        d = 1 if long_.iat[i] else -1
        e, a = r["close"], r["atr"]
        raw = r["lo5"] - 0.3 * a if d > 0 else r["hi5"] + 0.3 * a
        sl = clamp_sl(e, raw, a, 1.0, 3.0, d)
        tp = e + d * abs(e - sl) * tp_r
        out.append((i, d, sl, tp, 48, None))
    return out


def strat_breakout(h1, p):
    """Cassure Donchian 20 bougies 4h, sortie par stop suiveur."""
    adx_min, trail = p.get("adx", 20), p.get("trail", 3.0)
    new = h1["new4h"]
    long_ = new & (h1["c4"] > h1["dc_hi"]) & (h1["c4"] > h1["ema200"]) & (h1["adx"] > adx_min)
    short = new & (h1["c4"] < h1["dc_lo"]) & (h1["c4"] < h1["ema200"]) & (h1["adx"] > adx_min)
    out = []
    for i in np.flatnonzero((long_ | short).to_numpy()):
        r = h1.iloc[i]
        d = 1 if long_.iat[i] else -1
        e, a4 = r["close"], r["atr4"]
        sl = e - d * 2.0 * a4
        out.append((i, d, sl, None, 240, trail * a4))
    return out


def strat_range(h1, p):
    """Retour a la moyenne en marche sans tendance (ADX 4h faible)."""
    adx_max = p.get("adx_max", 20)
    calm = h1["adx"] < adx_max
    long_ = calm & (h1["close"] < h1["bb_l"]) & (h1["rsi"] < 30)
    short = calm & (h1["close"] > h1["bb_u"]) & (h1["rsi"] > 70)
    out = []
    for i in np.flatnonzero((long_ | short).to_numpy()):
        r = h1.iloc[i]
        d = 1 if long_.iat[i] else -1
        e, a = r["close"], r["atr"]
        sl = e - d * 1.5 * a
        tp = r["bb_m"]
        if (tp - e) * d <= 0.3 * a:
            continue
        out.append((i, d, sl, tp, 24, None))
    return out


def strat_regime(h1, p):
    """Bascule : pullback en tendance, retour a la moyenne en range."""
    a = strat_pullback(h1, {"tp_r": p.get("tp_r", 2.0), "adx": p.get("adx", 25)})
    b = strat_range(h1, {"adx_max": p.get("adx_max", 18)})
    return sorted(a + b, key=lambda x: x[0])


def strat_random(h1, p):
    rng = np.random.default_rng(p.get("seed", 1))
    out = []
    for i in np.flatnonzero(rng.random(len(h1)) < 0.02):
        r = h1.iloc[i]
        if not np.isfinite(r["atr"]):
            continue
        d = 1 if rng.random() < 0.5 else -1
        e, a = r["close"], r["atr"]
        sl = e - d * 1.5 * a
        out.append((i, d, sl, e + d * 3.0 * a, 48, None))
    return out


STRATS = {
    "actuelle": strat_actuelle,
    "pullback": strat_pullback,
    "breakout": strat_breakout,
    "range": strat_range,
    "regime": strat_regime,
    "aleatoire": strat_random,
}




def signal_derniere_bougie(h1, nom, params):
    """Signal eventuel sur la DERNIERE bougie 1h cloturee (utilise par le bot reel).
    Renvoie dict ou None."""
    last = len(h1) - 1
    for (i, d, sl, tp, mh, trail) in STRATS[nom](h1, params):
        if i == last:
            r = h1.iloc[i]
            return {"dir": int(d), "sl": float(sl), "tp": None if tp is None else float(tp),
                    "max_heures": int(mh), "trail": None if trail is None else float(trail),
                    "close": float(r["close"]), "atr": float(r["atr"]),
                    "bougie_close_time": int(r["close_time"])}
    return None
