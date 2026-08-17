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
    'scalp': {
        'trend': '1h', 'entry': '5m', 'confirm': '15m',
        'atrTP': 2.5, 'atrSL': 1.5, 'cycle': 90,
        # ── Garde-fous de gestion du risque ──
        'sl_buffer': 0.6,   # marge sous le creux (en ATR) — respiration du marché
        'sl_min': 1.3,      # SL JAMAIS plus proche que 1.3 ATR de l'entrée
        'sl_max': 3.0,      # SL jamais plus loin que 3.0 ATR
        'lev_max': 5,       # levier plafonné à ×5 en scalp
        'rr': 1.8,          # ratio risque/récompense visé
    },
    'swing': {
        'trend': '4h', 'entry': '1h', 'confirm': '15m',
        'atrTP': 3.5, 'atrSL': 1.8, 'cycle': 300,
        'sl_buffer': 0.7,
        'sl_min': 1.5,
        'sl_max': 3.5,
        'lev_max': 8,       # levier plafonné à ×8 en swing
        'rr': 2.0,
    },
}

CONF_THRESHOLD = 80
# Plusieurs endpoints Binance — on essaie dans l'ordre jusqu'à ce qu'un réponde.
# data-api.binance.vision est l'endpoint public de données, rarement géo-bloqué.
BINANCE_ENDPOINTS = [
    'https://data-api.binance.vision/api/v3/klines',
    'https://api.binance.com/api/v3/klines',
    'https://api1.binance.com/api/v3/klines',
    'https://api2.binance.com/api/v3/klines',
    'https://api3.binance.com/api/v3/klines',
]
# Mémorise l'endpoint qui marche pour ne pas retester à chaque fois
working_endpoint = [None]

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
# ══════════════════════════════════════════════════════
def send_telegram(text, buttons=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }
    # buttons = liste de lignes, chaque ligne = liste de (label, url)
    if buttons:
        payload['reply_markup'] = {
            'inline_keyboard': [
                [{'text': label, 'url': link} for (label, link) in row]
                for row in buttons
            ]
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

    cfg = MODES[mode_key]
    trade_amt = CAPITAL * ALLOC_PCT / 100
    sl_dist = abs((sig['sl'] - sig['entry']) / sig['entry'] * 100)
    target_loss = trade_amt * RISK_PCT / 100

    # ── LEVIER PLAFONNÉ PAR MODE (correction majeure) ──
    # Avant : jusqu'à ×20 → le bruit du marché suffisait à déclencher le SL.
    lev_theorique = round(target_loss / (trade_amt * sl_dist / 100)) if sl_dist > 0 else 1
    lev = max(1, min(cfg['lev_max'], lev_theorique))

    tp_pct = (sig['tp'] - sig['entry']) / sig['entry'] * 100
    sl_pct = (sig['sl'] - sig['entry']) / sig['entry'] * 100
    # ── GAIN/PERTE RÉELS : le levier multiplie le résultat (correction) ──
    gain = trade_amt * lev * abs(tp_pct) / 100
    loss = trade_amt * lev * abs(sl_pct) / 100
    loss_pct_bk = loss / CAPITAL * 100  # perte en % de la bankroll totale

    entry_type = "au marché" if sig['entryAtMarket'] else "LIMITE (attends le pullback)"
    fresh = "✅ frais" if sig['isFresh'] else "signal mûr"
    now = datetime.now().strftime("%H:%M")

    # Lien raccourci iOS (ouvre l'app BingX, session active) — cliquable dans le corps du message
    shortcut_link = "shortcuts://run-shortcut?name=BingX"
    # Lien web de secours (ouvre le navigateur sur la bonne paire)
    web_link = f"https://bingx.com/en/perpetual/{short}-USDT/"

    msg = (
        f"{arrow} <b>{direction} {short}/USDT</b>\n"
        f"{mode_label} · Confiance <b>{sig['conf']}%</b> · {now} · {fresh}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎯 Entrée ({entry_type}) : <b>{fmt_price(sig['entry'])}</b>\n"
        f"✅ TP : <b>{fmt_price(sig['tp'])}</b> ({tp_pct:+.2f}%) → +{gain:.2f}€\n"
        f"🛑 SL : <b>{fmt_price(sig['sl'])}</b> ({sl_pct:+.2f}%) → -{loss:.2f}€ ({loss_pct_bk:.1f}% BK)\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 Mise : <b>{trade_amt:.0f}€</b> · Levier : <b>×{lev}</b> (max ×{cfg['lev_max']}) · R/R : <b>1:{sig['rr']:.1f}</b>\n"
        f"📊 BK : {CAPITAL}€ · Order Value BingX : <b>{trade_amt * lev:.0f} USDT</b>\n\n"
        f'👉 <a href="{shortcut_link}">📲 Ouvrir l\'app BingX</a>\n'
        f"⚠️ Vérifie le signal avant d'entrer — il peut avoir changé."
    )
    # Bouton de secours web (https obligatoire pour les boutons Telegram)
    buttons = [[("🌐 BingX " + short + " (web)", web_link)]]
    return msg, buttons


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
                msg, buttons = format_alert(pair, mode_key, sig)
                send_telegram(msg, buttons)
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
        "Tu recevras une notif à chaque signal validé.\n\n"
        '👉 <a href="shortcuts://run-shortcut?name=BingX">📲 Tester l\'ouverture de BingX</a>'
    )

    # Test de connectivité au démarrage : vérifie qu'au moins un endpoint répond
    try:
        test = fetch_klines('BTCUSDT', '1h')
        print(f"✓ Connexion Binance OK via {working_endpoint[0]}")
        send_telegram(f"✅ Connexion aux données marché OK\nEndpoint actif : {working_endpoint[0].replace('https://','').split('/')[0]}")
    except Exception as e:
        print(f"⚠ Aucun endpoint Binance accessible : {e}")
        send_telegram(
            "⚠️ <b>Attention</b> : impossible d'accéder aux données Binance depuis ce serveur.\n"
            "Le bot continue d'essayer, mais il faudra peut-être changer la région du serveur Railway."
        )

    last_scalp = 0
    last_swing = 0

    while True:
        try:
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
        except Exception as e:
            # Le bot ne s'arrête JAMAIS sur une erreur — il log et continue
            print(f"⚠ Erreur dans la boucle (ignorée) : {e}")
            time.sleep(10)


if __name__ == '__main__':
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("\nBot arrêté manuellement.")
            break
        except Exception as e:
            # En cas d'erreur fatale, on redémarre automatiquement après 15s
            print(f"Erreur fatale : {e} — redémarrage dans 15s")
            try:
                send_telegram(f"⚠️ <b>Bot a rencontré une erreur</b> : {e}\nRedémarrage automatique...")
            except Exception:
                pass
            time.sleep(15)
