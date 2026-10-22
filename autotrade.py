#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autotrade.py — ScalpRadar FULL AUTO
Lance par GitHub Actions toutes les 15 min. Execution "one-shot" :
  1. Lit l'etat, applique les garde-fous
  2. Verifie si une position ouverte s'est cloturee (TP/SL) -> notifie + MAJ bankroll
  3. Scanne les paires -> si signal valide, passe l'ordre avec TP/SL attaches
  4. Sauvegarde l'etat, notifie Telegram

BingX gere la sortie (TP/SL) serveur-side : le bot n'a pas besoin de tourner 24h/24.
"""
import os
import sys
import json
import time
import traceback
from datetime import datetime, timezone

import requests
import signals
from bingx import BingX, BingXError, to_bingx_symbol
from config import (
    PAIRS, MODE, ALLOC_PCT, RISK_PCT, CONF_MIN,
    MAX_DAILY_LOSS_PCT, MAX_TRADES_PER_DAY,
    PAUSE_AFTER_CONSECUTIVE_LOSSES, MAX_OPEN_POSITIONS,
    INITIAL_BANKROLL, DRY_RUN,
)

STATE_FILE = "state.json"

# ── Secrets (variables d'environnement GitHub) ──────────
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
BINGX_KEY = os.environ.get("BINGX_API_KEY", "")
BINGX_SECRET = os.environ.get("BINGX_API_SECRET", "")


# ════════════════════════════════════════════════════════
# TELEGRAM
# ════════════════════════════════════════════════════════
def tg(text):
    if not TG_TOKEN or not TG_CHAT:
        print("[TG non configure]", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception as e:
        print("Erreur Telegram :", e)


def read_commands(state):
    """Lit les commandes /stop et /go envoyees dans Telegram."""
    if not TG_TOKEN:
        return
    try:
        offset = state.get("tg_offset", 0)
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset + 1, "timeout": 0}, timeout=15,
        )
        updates = r.json().get("result", [])
        for u in updates:
            state["tg_offset"] = u["update_id"]
            txt = (u.get("message", {}).get("text") or "").strip().lower()
            if txt.startswith("/stop"):
                state["enabled"] = False
                tg("⏸️ <b>Bot mis en pause</b>\nAucun nouveau trade ne sera ouvert.\nEnvoie /go pour reprendre.")
            elif txt.startswith("/go"):
                state["enabled"] = True
                state["consecutive_losses"] = 0
                tg("▶️ <b>Bot reactive</b>\nLa surveillance reprend.")
            elif txt.startswith("/status"):
                state["_status_requested"] = True
    except Exception as e:
        print("getUpdates :", e)


# ════════════════════════════════════════════════════════
# ETAT PERSISTANT
# ════════════════════════════════════════════════════════
def default_state():
    return {
        "bankroll": INITIAL_BANKROLL,
        "enabled": True,
        "day": today(),
        "daily_pnl": 0.0,
        "trades_today": 0,
        "consecutive_losses": 0,
        "open_position": None,
        "tg_offset": 0,
        "history": [],
        "total_trades": 0,
        "total_wins": 0,
    }


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            s = json.load(open(STATE_FILE))
            for k, v in default_state().items():
                s.setdefault(k, v)
            return s
        except Exception:
            pass
    return default_state()


def save_state(s):
    json.dump(s, open(STATE_FILE, "w"), indent=2, ensure_ascii=False)


def roll_day(state):
    """Remet les compteurs journaliers a zero au changement de jour."""
    if state["day"] != today():
        state["day"] = today()
        state["daily_pnl"] = 0.0
        state["trades_today"] = 0
        state["consecutive_losses"] = 0


# ════════════════════════════════════════════════════════
# FORMATAGE
# ════════════════════════════════════════════════════════
def fmt(p):
    p = float(p)
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.4f}"
    return f"${p:.6f}"


# ════════════════════════════════════════════════════════
# GARDE-FOUS
# ════════════════════════════════════════════════════════
def check_guardrails(state):
    """Retourne (autorise, raison_du_blocage)."""
    if not state.get("enabled", True):
        return False, "bot en pause (/go pour reprendre)"

    if state["open_position"]:
        return False, "une position est deja ouverte"

    if state["trades_today"] >= MAX_TRADES_PER_DAY:
        return False, f"limite de {MAX_TRADES_PER_DAY} trades/jour atteinte"

    max_loss = state["bankroll"] * MAX_DAILY_LOSS_PCT / 100
    if state["daily_pnl"] <= -max_loss:
        return False, f"perte journaliere max atteinte ({state['daily_pnl']:.2f}€)"

    if state["consecutive_losses"] >= PAUSE_AFTER_CONSECUTIVE_LOSSES:
        return False, f"{state['consecutive_losses']} pertes consecutives — pause jusqu'a demain"

    if state["bankroll"] < 5:
        return False, "bankroll trop faible pour trader"

    return True, ""


# ════════════════════════════════════════════════════════
# SUIVI DE POSITION
# ════════════════════════════════════════════════════════
def check_closed_position(api, state):
    """Si la position suivie n'existe plus sur BingX, elle s'est cloturee."""
    pos = state.get("open_position")
    if not pos:
        return

    symbol = pos["symbol"]
    try:
        live = api.positions(symbol)
    except Exception as e:
        print("Lecture positions impossible :", e)
        return

    still_open = any(abs(float(p.get("positionAmt", 0) or 0)) > 0 for p in live)
    if still_open:
        print(f"Position {symbol} toujours ouverte.")
        return

    # Position fermee -> recupere le PnL realise
    pnl = api.realized_pnl(symbol, pos["opened_at"])
    if pnl is None:
        pnl = 0.0

    is_win = pnl > 0
    state["bankroll"] = round(max(0.0, state["bankroll"] + pnl), 2)
    state["daily_pnl"] = round(state["daily_pnl"] + pnl, 2)
    state["total_trades"] += 1
    if is_win:
        state["total_wins"] += 1
        state["consecutive_losses"] = 0
    else:
        state["consecutive_losses"] += 1

    state["history"].insert(0, {
        "symbol": symbol, "direction": pos["direction"], "pnl": round(pnl, 2),
        "entry": pos["entry"], "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    state["history"] = state["history"][:50]
    state["open_position"] = None

    # Nettoie les ordres TP/SL orphelins
    try:
        api.close_all(symbol)
    except Exception:
        pass

    wr = (state["total_wins"] / state["total_trades"] * 100) if state["total_trades"] else 0
    icon = "✅" if is_win else "🛑"
    titre = "TAKE PROFIT" if is_win else "STOP LOSS"
    tg(
        f"{icon} <b>{titre} — {symbol} {pos['direction']}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Resultat : <b>{pnl:+.2f}€</b>\n"
        f"Bankroll : <b>{state['bankroll']:.2f}€</b>\n"
        f"Aujourd'hui : {state['daily_pnl']:+.2f}€ · {state['trades_today']} trade(s)\n"
        f"Bilan global : {state['total_wins']}/{state['total_trades']} gagnants ({wr:.0f}%)"
    )

    if state["consecutive_losses"] >= PAUSE_AFTER_CONSECUTIVE_LOSSES:
        tg(f"⏸️ <b>Pause automatique</b>\n{state['consecutive_losses']} pertes consecutives.\n"
           f"Reprise demain, ou envoie /go pour forcer.")


# ════════════════════════════════════════════════════════
# RECHERCHE ET EXECUTION
# ════════════════════════════════════════════════════════
def find_signal():
    """Scanne les paires, retourne le meilleur signal valide ou None."""
    cfg = signals.MODES[MODE]
    best = None
    for pair in PAIRS:
        try:
            dT = signals.fetch_klines(pair, cfg["trend"])
            dE = signals.fetch_klines(pair, cfg["entry"])
            dC = signals.fetch_klines(pair, cfg["confirm"])
            sig = signals.compute_signal(dT, dE, dC, MODE)
            etat = "range" if sig["ranging"] else f"{sig['conf']}%"
            print(f"  {pair}: {etat}")
            if (sig["isLong"] or sig["isShort"]) and sig["conf"] >= CONF_MIN:
                if best is None or sig["conf"] > best[1]["conf"]:
                    best = (pair, sig)
        except Exception as e:
            print(f"  {pair}: erreur {e}")
        time.sleep(0.25)
    return best


def execute_trade(api, state, pair, sig):
    cfg = signals.MODES[MODE]
    symbol = to_bingx_symbol(pair)
    direction = "LONG" if sig["isLong"] else "SHORT"

    margin = state["bankroll"] * ALLOC_PCT / 100
    entry = float(sig["entry"])
    sl_dist_pct = abs((sig["sl"] - entry) / entry * 100)
    if sl_dist_pct <= 0:
        print("Distance SL nulle — trade annule.")
        return

    # Levier : on ARRONDIT VERS LE BAS pour ne jamais depasser le risque vise.
    # perte_au_SL = margin x lev x sl_dist% <= margin x RISK_PCT%  (garanti)
    target_loss = margin * RISK_PCT / 100
    lev_theorique = int(target_loss / (margin * sl_dist_pct / 100))
    lev = max(1, min(cfg["lev_max"], lev_theorique))

    notional = margin * lev
    qty = api.round_qty(symbol, notional / entry)
    if qty <= 0:
        tg(f"⚠️ <b>Ordre impossible</b> — quantite trop faible sur {symbol}.\n"
           f"Bankroll insuffisante pour la taille minimale de cette paire.")
        return

    tp = api.round_price(symbol, sig["tp"])
    sl = api.round_price(symbol, sig["sl"])
    gain_est = notional * abs((sig["tp"] - entry) / entry)
    perte_est = notional * abs((sig["sl"] - entry) / entry)

    if DRY_RUN:
        tg(
            f"🧪 <b>SIMULATION (DRY_RUN)</b> — aucun ordre reel\n"
            f"{direction} {symbol} · conf {sig['conf']}%\n"
            f"Entree {fmt(entry)} · TP {fmt(tp)} · SL {fmt(sl)}\n"
            f"Mise {margin:.2f}€ · ×{lev} · qty {qty}\n"
            f"Gain estime +{gain_est:.2f}€ · Perte max -{perte_est:.2f}€"
        )
        print("DRY_RUN : ordre simule.")
        return

    # Preparation du compte
    hedge = api.is_hedge_mode()
    position_side = direction if hedge else "BOTH"
    api.set_margin_isolated(symbol)
    api.set_leverage(symbol, lev, position_side)

    # Ordre marche avec TP/SL attaches (BingX gere la sortie)
    res = api.open_market_with_tpsl(symbol, direction, qty, tp, sl, position_side)
    order = res.get("order", res) if isinstance(res, dict) else {}
    order_id = order.get("orderId") or order.get("orderID") or "?"

    state["open_position"] = {
        "symbol": symbol, "pair": pair, "direction": direction,
        "entry": entry, "tp": tp, "sl": sl, "qty": qty, "lev": lev,
        "margin": round(margin, 2), "order_id": str(order_id),
        "opened_at": int(time.time() * 1000),
        "conf": sig["conf"],
    }
    state["trades_today"] += 1

    tg(
        f"🟢 <b>ORDRE PASSE — {direction} {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Entree : <b>{fmt(entry)}</b> · confiance {sig['conf']}%\n"
        f"✅ TP : <b>{fmt(tp)}</b> → +{gain_est:.2f}€\n"
        f"🛑 SL : <b>{fmt(sl)}</b> → -{perte_est:.2f}€\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Mise <b>{margin:.2f}€</b> · Levier <b>×{lev}</b> · R/R 1:{sig['rr']:.1f}\n"
        f"Bankroll : {state['bankroll']:.2f}€ · trade {state['trades_today']}/{MAX_TRADES_PER_DAY} aujourd'hui\n\n"
        f"TP et SL sont poses sur BingX — la sortie est automatique."
    )
    print(f"Ordre passe : {direction} {symbol} qty={qty} lev={lev}")


# ════════════════════════════════════════════════════════
# POINT D'ENTREE
# ════════════════════════════════════════════════════════
def main():
    print("=" * 52)
    print(f"ScalpRadar AUTO — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC — mode {MODE}")
    print("=" * 52)

    state = load_state()
    roll_day(state)
    read_commands(state)

    if not BINGX_KEY or not BINGX_SECRET:
        tg("⚠️ <b>Cles API BingX manquantes</b> — ajoute-les dans les GitHub Secrets.")
        save_state(state)
        return

    api = BingX(BINGX_KEY, BINGX_SECRET)

    # 1. Une position suivie s'est-elle cloturee ?
    check_closed_position(api, state)

    # Reponse a /status
    if state.pop("_status_requested", False):
        wr = (state["total_wins"] / state["total_trades"] * 100) if state["total_trades"] else 0
        pos = state.get("open_position")
        tg(
            f"📊 <b>Statut du bot</b>\n"
            f"Etat : {'▶️ actif' if state.get('enabled') else '⏸️ en pause'}\n"
            f"Bankroll : <b>{state['bankroll']:.2f}€</b>\n"
            f"Aujourd'hui : {state['daily_pnl']:+.2f}€ · {state['trades_today']}/{MAX_TRADES_PER_DAY} trades\n"
            f"Bilan : {state['total_wins']}/{state['total_trades']} gagnants ({wr:.0f}%)\n"
            f"Position : {pos['direction'] + ' ' + pos['symbol'] if pos else 'aucune'}"
        )

    # 2. Garde-fous
    ok, raison = check_guardrails(state)
    if not ok:
        print(f"Pas de nouveau trade : {raison}")
        save_state(state)
        return

    # 3. Scan
    print(f"Scan de {len(PAIRS)} paires (mode {MODE}, confiance min {CONF_MIN}%)…")
    found = find_signal()

    if not found:
        print("Aucun signal valide.")
        save_state(state)
        return

    pair, sig = found
    print(f"SIGNAL : {pair} {'LONG' if sig['isLong'] else 'SHORT'} conf {sig['conf']}%")

    # 4. Execution
    try:
        execute_trade(api, state, pair, sig)
    except BingXError as e:
        tg(f"⚠️ <b>Ordre refuse par BingX</b>\n{pair} : {e}")
        print("BingXError :", e)
    except Exception as e:
        tg(f"⚠️ <b>Erreur a l'execution</b>\n{pair} : {e}")
        traceback.print_exc()

    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        traceback.print_exc()
        tg(f"⚠️ <b>Erreur du bot</b> : {e}")
        sys.exit(0)  # ne jamais faire echouer le workflow
