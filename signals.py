# -*- coding: utf-8 -*-
"""
signals.py — Moteur d'analyse ScalpRadar (identique a la version validee).
Indicateurs, filtres (anti-range, EMA50, fraicheur), entree pullback,
SL structurel encadre et TP en R/R.
"""
import math
import requests

# Plusieurs endpoints Binance — on essaie dans l'ordre jusqu'a ce qu'un reponde.
BINANCE_ENDPOINTS = [
    'https://data-api.binance.vision/api/v3/klines',
    'https://api.binance.com/api/v3/klines',
    'https://api1.binance.com/api/v3/klines',
    'https://api2.binance.com/api/v3/klines',
    'https://api3.binance.com/api/v3/klines',
]
working_endpoint = [None]

MODES = {
    'scalp': {
        'trend': '1h', 'entry': '5m', 'confirm': '15m',
        'atrTP': 2.5, 'atrSL': 1.5, 'cycle': 90,
        'sl_buffer': 0.6, 'sl_min': 1.3, 'sl_max': 3.0,
        'lev_max': 5, 'rr': 1.8,
    },
    'swing': {
        'trend': '4h', 'entry': '1h', 'confirm': '15m',
        'atrTP': 3.5, 'atrSL': 1.8, 'cycle': 300,
        'sl_buffer': 0.7, 'sl_min': 1.5, 'sl_max': 3.5,
        'lev_max': 8, 'rr': 2.0,
    },
}

def ema(data, period):
    k = 2 / (period + 1)
    e = data[0]
    result = [e]
    for i in range(1, len(data)):
        e = data[i] * k + e * (1 - k)
        result.append(e)
    return result


def rsi(closes, period=14):
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    ag = sum(c for c in changes[:period] if c > 0) / period
    al = sum(-c for c in changes[:period] if c < 0) / period
    for i in range(period, len(changes)):
        ag = (ag * (period - 1) + (changes[i] if changes[i] > 0 else 0)) / period
        al = (al * (period - 1) + (-changes[i] if changes[i] < 0 else 0)) / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))


def bollinger(closes, period=20, mult=2):
    s = closes[-period:]
    sma = sum(s) / period
    std = math.sqrt(sum((v - sma) ** 2 for v in s) / period)
    upper = sma + mult * std
    lower = sma - mult * std
    pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {'u': upper, 'l': lower, 'pctB': pct_b}


def macd(closes):
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    macd_line = [e12[i] - e26[i] for i in range(len(closes))][26:]
    signal_line = ema(macd_line, 9)
    hist = macd_line[-1] - signal_line[-1]
    prev_hist = macd_line[-2] - signal_line[-2]
    return {'hist': hist, 'prevHist': prev_hist}


def atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    return sum(trs[-period:]) / period


def vol_ratio(vols):
    last = vols[-1]
    avg = sum(vols[-21:-1]) / 20
    return last / avg if avg else 1


def score_from(closes, highs, lows, vols):
    """Score de -9 à +9 — identique à l'app."""
    r = rsi(closes, 14)
    bb = bollinger(closes, 20, 2)
    m = macd(closes)
    vr = vol_ratio(vols)
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    ce9, pe9 = e9[-1], e9[-2]
    ce21, pe21 = e21[-1], e21[-2]
    price, prev = closes[-1], closes[-2]
    sc = 0
    # RSI
    if r < 30: sc += 2
    elif r < 42: sc += 1
    elif r > 70: sc -= 2
    elif r > 58: sc -= 1
    # EMA cross
    if pe9 < pe21 and ce9 > ce21: sc += 2
    elif pe9 > pe21 and ce9 < ce21: sc -= 2
    elif ce9 > ce21: sc += 1
    else: sc -= 1
    # MACD
    if m['hist'] > 0 and m['hist'] > m['prevHist']: sc += 2
    elif m['hist'] > 0: sc += 1
    elif m['hist'] < 0 and m['hist'] < m['prevHist']: sc -= 2
    else: sc -= 1
    # Bollinger
    if bb['pctB'] < 0.1: sc += 2
    elif bb['pctB'] < 0.3: sc += 1
    elif bb['pctB'] > 0.9: sc -= 2
    elif bb['pctB'] > 0.7: sc -= 1
    # Volume
    if vr > 1.5:
        sc += 1 if price > prev else -1
    return sc


# ── FILTRES AVANCÉS (comme la dernière version de l'app) ──
def last_swing_low(lows, lookback=20):
    s = lows[-lookback:]
    mins = [s[i] for i in range(1, len(s) - 1) if s[i] < s[i - 1] and s[i] < s[i + 1]]
    return min(mins) if mins else min(s)


def last_swing_high(highs, lookback=20):
    s = highs[-lookback:]
    maxs = [s[i] for i in range(1, len(s) - 1) if s[i] > s[i - 1] and s[i] > s[i + 1]]
    return max(maxs) if maxs else max(s)


def signal_freshness(closes):
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    n = len(e9)
    side = 1 if e9[-1] > e21[-1] else -1
    bars = 0
    for i in range(n - 1, max(0, n - 10) - 1, -1):
        s = 1 if e9[i] > e21[i] else -1
        if s == side:
            bars += 1
        else:
            break
    return bars


def is_ranging(closes, atr_val):
    recent = closes[-20:]
    return (max(recent) - min(recent)) < atr_val * 3.5


def trend_filter(closes):
    e50 = ema(closes, 50)
    price = closes[-1]
    return {'above': price > e50[-1], 'dist': (price - e50[-1]) / e50[-1] * 100}


# ══════════════════════════════════════════════════════
# MOTEUR DE SIGNAL (identique à computeSignal de l'app)
# ══════════════════════════════════════════════════════
def compute_signal(dT, dE, dC, mode_key):
    cfg = MODES[mode_key]
    price = dE['closes'][-1]
    atr_val = atr(dE['highs'], dE['lows'], dE['closes'], 14)

    s_trend = score_from(dT['closes'], dT['highs'], dT['lows'], dT['vols'])
    s_entry = score_from(dE['closes'], dE['highs'], dE['lows'], dE['vols'])
    s_conf = score_from(dC['closes'], dC['highs'], dC['lows'], dC['vols'])

    t_bull, t_bear = s_trend >= 2, s_trend <= -2
    e_bull, e_bear = s_entry >= 3, s_entry <= -3
    c_bull, c_bear = s_conf > 0, s_conf < 0

    is_long = t_bull and e_bull and c_bull
    is_short = t_bear and e_bear and c_bear

    # Filtre tendance LT
    tf = trend_filter(dT['closes'])
    if is_long and not tf['above']: is_long = False
    if is_short and tf['above']: is_short = False

    # Filtre anti-range
    ranging = is_ranging(dE['closes'], atr_val)
    if ranging:
        is_long = is_short = False

    # Fraîcheur
    fresh_bars = signal_freshness(dE['closes'])
    is_fresh = fresh_bars <= 4

    # Entrée limite (pullback)
    e9e = ema(dE['closes'], 9)[-1]
    bb = bollinger(dE['closes'], 20, 2)
    mid = (bb['u'] + bb['l']) / 2
    entry_limit = price
    if is_long:
        target = max(e9e, mid)
        entry_limit = min(price, max(target, price - atr_val * 0.4))
        if price - entry_limit > atr_val * 0.6:
            entry_limit = price
    elif is_short:
        target = min(e9e, mid)
        entry_limit = max(price, min(target, price + atr_val * 0.4))
        if entry_limit - price > atr_val * 0.6:
            entry_limit = price

    # ── SL STRUCTUREL AVEC DISTANCE MINIMALE (correction majeure) ──
    # Avant : le SL pouvait coller au prix si le creux était proche → sortie sur le bruit.
    # Maintenant : le SL est encadré entre sl_min et sl_max ATR de l'entrée.
    entry_ref = entry_limit
    buf     = cfg['sl_buffer']
    sl_min  = cfg['sl_min']
    sl_max  = cfg['sl_max']
    rr_target = cfg['rr']

    if is_long:
        swing = last_swing_low(dE['lows'], 20)
        sl = swing - atr_val * buf              # SL sous le dernier creux + marge
        sl = max(sl, entry_ref - atr_val * sl_max)  # pas trop loin (limite la perte)
        sl = min(sl, entry_ref - atr_val * sl_min)  # pas trop près (absorbe le bruit)
        risk = entry_ref - sl
        tp = entry_ref + risk * rr_target
    elif is_short:
        swing = last_swing_high(dE['highs'], 20)
        sl = swing + atr_val * buf
        sl = min(sl, entry_ref + atr_val * sl_max)
        sl = max(sl, entry_ref + atr_val * sl_min)
        risk = sl - entry_ref
        tp = entry_ref - risk * rr_target
    else:
        tp = price + atr_val * cfg['atrTP']
        sl = price - atr_val * cfg['atrSL']

    # Confiance
    align = 0
    if is_long:
        align = sum([t_bull, e_bull, c_bull])
    elif is_short:
        align = sum([t_bear, e_bear, c_bear])
    conf = round(50 + align * 12) if (is_long or is_short) else 0
    if is_long or is_short:
        if is_fresh: conf += 8
        if abs(tf['dist']) > 1: conf += 4
        rr = (tp - entry_ref) / (entry_ref - sl) if is_long else (entry_ref - tp) / (sl - entry_ref)
        if rr >= 2: conf += 6
        conf = min(99, conf)

    rr = 0
    if is_long or is_short:
        rr = (tp - entry_ref) / (entry_ref - sl) if is_long else (entry_ref - tp) / (sl - entry_ref)

    return {
        'isLong': is_long, 'isShort': is_short,
        'conf': conf, 'price': price, 'entry': entry_limit,
        'tp': tp, 'sl': sl, 'rr': rr, 'atr': atr_val,
        'entryAtMarket': abs(entry_limit - price) < price * 0.0001,
        'isFresh': is_fresh, 'ranging': ranging,
    }


# ══════════════════════════════════════════════════════
# DONNÉES BINANCE — multi-endpoints avec bascule automatique
# ══════════════════════════════════════════════════════
def _parse_klines(d):
    return {
        'closes': [float(c[4]) for c in d],
        'highs': [float(c[2]) for c in d],
        'lows': [float(c[3]) for c in d],
        'vols': [float(c[5]) for c in d],
    }

def fetch_klines(pair, interval):
    params = f"?symbol={pair}&interval={interval}&limit=150"
    # On commence par l'endpoint qui a marché la dernière fois
    endpoints = BINANCE_ENDPOINTS[:]
    if working_endpoint[0] in endpoints:
        endpoints.remove(working_endpoint[0])
        endpoints.insert(0, working_endpoint[0])

    last_err = None
    for base in endpoints:
        try:
            r = requests.get(base + params, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 451 or r.status_code == 403:
                last_err = f"{r.status_code} bloqué"
                continue  # endpoint géo-bloqué, on essaie le suivant
            r.raise_for_status()
            working_endpoint[0] = base  # mémorise l'endpoint qui marche
            return _parse_klines(r.json())
        except Exception as e:
            last_err = str(e)
            continue
    # Aucun endpoint n'a répondu
    raise RuntimeError(f"Tous les endpoints Binance ont échoué ({last_err})")


# ══════════════════════════════════════════════════════
# TELEGRAM
