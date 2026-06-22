#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScalpRadar Bot — Notifications Telegram
Scanne le marché crypto 24h/24 et envoie une notif Telegram à chaque signal.
Reprend exactement la logique de l'app ScalpRadar (multi-timeframe + filtres).
"""

import time
import requests
import math
from datetime import datetime

# ══════════════════════════════════════════════════════
# CONFIGURATION — À REMPLIR (voir config.py)
# ══════════════════════════════════════════════════════
from config import (
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    CAPITAL,
    ALLOC_PCT,
    RISK_PCT,
    PAIRS,
    SCALP_ENABLED,
    SWING_ENABLED,
)

# ── PARAMÈTRES DES MODES (identiques à l'app) ──
MODES = {
    'scalp': {'trend': '1h', 'entry': '5m', 'confirm': '15m', 'atrTP': 2.5, 'atrSL': 1.5, 'cycle': 90},
    'swing': {'trend': '4h', 'entry': '1h', 'confirm': '15m', 'atrTP': 3.5, 'atrSL': 1.8, 'cycle': 300},
}

CONF_THRESHOLD = 80
BINANCE_BASE = 'https://api.binance.com/api/v3/klines'

# Mémoire anti-doublon : ne pas renvoyer la même alerte dans les 15 min
last_alerts = {}  # clé: "PAIR_MODE_DIR" -> timestamp


# ══════════════════════════════════════════════════════
# INDICATEURS TECHNIQUES
# ══════════════════════════════════════════════════════
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

    # SL structurel + TP en R/R 1.8
    entry_ref = entry_limit
    if is_long:
        swing = last_swing_low(dE['lows'], 20)
        sl = max(swing - atr_val * 0.3, entry_ref - atr_val * 2.2)
        risk = entry_ref - sl
        tp = entry_ref + risk * 1.8
    elif is_short:
        swing = last_swing_high(dE['highs'], 20)
        sl = min(swing + atr_val * 0.3, entry_ref + atr_val * 2.2)
        risk = sl - entry_ref
        tp = entry_ref - risk * 1.8
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
# DONNÉES BINANCE
# ══════════════════════════════════════════════════════
def fetch_klines(pair, interval):
    url = f"{BINANCE_BASE}?symbol={pair}&interval={interval}&limit=150"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    d = r.json()
    return {
        'closes': [float(c[4]) for c in d],
        'highs': [float(c[2]) for c in d],
        'lows': [float(c[3]) for c in d],
        'vols': [float(c[5]) for c in d],
    }


# ══════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Erreur Telegram] {e}")


def fmt_price(p):
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.4f}"
    return f"${p:.6f}"


def format_alert(pair, mode_key, sig):
    direction = 'LONG' if sig['isLong'] else 'SHORT'
    arrow = '🟢 ↑' if sig['isLong'] else '🔴 ↓'
    mode_label = '⚡ Scalp' if mode_key == 'scalp' else '📈 Swing'
    short = pair.replace('USDT', '')

    trade_amt = CAPITAL * ALLOC_PCT / 100
    sl_dist = abs((sig['sl'] - sig['entry']) / sig['entry'] * 100)
    target_loss = trade_amt * RISK_PCT / 100
    lev = max(1, min(20, round(target_loss / (trade_amt * sl_dist / 100)))) if sl_dist > 0 else 1

    tp_pct = (sig['tp'] - sig['entry']) / sig['entry'] * 100
    sl_pct = (sig['sl'] - sig['entry']) / sig['entry'] * 100
    gain = trade_amt * abs(tp_pct) / 100
    loss = trade_amt * abs(sl_pct) / 100

    entry_type = "au marché" if sig['entryAtMarket'] else "LIMITE (attends le pullback)"
    fresh = "✅ frais" if sig['isFresh'] else "signal mûr"
    now = datetime.now().strftime("%H:%M")

    msg = (
        f"{arrow} <b>{direction} {short}/USDT</b>\n"
        f"{mode_label} · Confiance <b>{sig['conf']}%</b> · {now} · {fresh}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎯 Entrée ({entry_type}) : <b>{fmt_price(sig['entry'])}</b>\n"
        f"✅ TP : <b>{fmt_price(sig['tp'])}</b> ({tp_pct:+.2f}%) → +{gain:.2f}€\n"
        f"🛑 SL : <b>{fmt_price(sig['sl'])}</b> ({sl_pct:+.2f}%) → -{loss:.2f}€\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Mise : <b>{trade_amt:.0f}€</b> · Levier : <b>×{lev}</b> · R/R : <b>1:{sig['rr']:.1f}</b>\n"
        f"📊 BK : {CAPITAL}€ · Order Value BingX : <b>{trade_amt * lev:.0f} USDT</b>\n\n"
        f"⚠️ Vérifie le signal avant d'entrer — il peut avoir changé."
    )
    return msg


# ══════════════════════════════════════════════════════
# BOUCLE DE SCAN
# ══════════════════════════════════════════════════════
def scan_mode(mode_key):
    for pair in PAIRS:
        try:
            cfg = MODES[mode_key]
            dT = fetch_klines(pair, cfg['trend'])
            dE = fetch_klines(pair, cfg['entry'])
            dC = fetch_klines(pair, cfg['confirm'])
            sig = compute_signal(dT, dE, dC, mode_key)

            if (sig['isLong'] or sig['isShort']) and sig['conf'] >= CONF_THRESHOLD:
                direction = 'LONG' if sig['isLong'] else 'SHORT'
                key = f"{pair}_{mode_key}_{direction}"
                now = time.time()
                # Anti-doublon : 15 min
                if key in last_alerts and (now - last_alerts[key]) < 900:
                    print(f"  ↳ {pair} {direction} ({mode_key}) — doublon ignoré")
                    continue
                last_alerts[key] = now
                msg = format_alert(pair, mode_key, sig)
                send_telegram(msg)
                print(f"  ✅ ALERTE {pair} {direction} ({mode_key}) conf {sig['conf']}%")
            else:
                status = "range" if sig['ranging'] else f"{sig['conf']}%"
                print(f"  · {pair} ({mode_key}) — {status}")
        except Exception as e:
            print(f"  ⚠ {pair} ({mode_key}) erreur : {e}")
        time.sleep(0.3)  # respecte les limites Binance


def main():
    print("=" * 50)
    print("ScalpRadar Bot — Démarrage")
    print(f"Paires : {', '.join(PAIRS)}")
    print(f"Scalp : {'ON' if SCALP_ENABLED else 'OFF'} · Swing : {'ON' if SWING_ENABLED else 'OFF'}")
    print("=" * 50)

    send_telegram(
        "🚀 <b>ScalpRadar Bot démarré</b>\n"
        f"Surveillance de {len(PAIRS)} paires\n"
        f"Scalp : {'✅' if SCALP_ENABLED else '❌'} · Swing : {'✅' if SWING_ENABLED else '❌'}\n"
        "Tu recevras une notif à chaque signal validé."
    )

    last_scalp = 0
    last_swing = 0

    while True:
        now = time.time()
        if SCALP_ENABLED and (now - last_scalp) >= MODES['scalp']['cycle']:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan SCALP")
            scan_mode('scalp')
            last_scalp = now
        if SWING_ENABLED and (now - last_swing) >= MODES['swing']['cycle']:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan SWING")
            scan_mode('swing')
            last_swing = now
        time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot arrêté.")
    except Exception as e:
        print(f"Erreur fatale : {e}")
        send_telegram(f"⚠️ <b>Bot arrêté</b> : {e}")
