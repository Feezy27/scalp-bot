#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autotrade.py — ScalpRadar V2, execution automatique sur BingX.

Lance par GitHub Actions toutes les 15 minutes, en un seul passage :
  1. Commandes Telegram du proprietaire (et de lui seul)
  2. Rapprochement avec BingX (BingX fait foi) : position cloturee ? SL bien en place ?
  3. Garde-fous (coupe-circuit, perte du jour, pertes consecutives, limite de trades)
  4. Recherche d'un signal sur la derniere bougie 1h cloturee
  5. Execution verifiee : marge isolee + levier relus, ordre avec SL/TP attaches,
     controle que le SL existe vraiment et que la liquidation est loin derriere.
     Sinon : fermeture immediate de la position et arret du bot.
  6. Etat chiffre sauvegarde, bilan quotidien = preuve de vie.

Le SL est pose CHEZ BingX : meme si GitHub ou ce script s'arretent, la perte reste bornee.
"""
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config as C
import coffre
import donnees
import strategie
from bingx import BingX, BingXError, NetworkError, floor_to, round_price, to_symbol
from telegram import Telegram

ETAT = "etat.chiffre"
TZ = ZoneInfo("Europe/Zurich")

SECRETS = {k: os.environ.get(k, "") for k in
           ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "BINGX_API_KEY", "BINGX_API_SECRET")}


def masque(s):
    """Retire toute trace de secret d'un texte (logs publics, messages d'erreur)."""
    s = str(s)
    for v in SECRETS.values():
        if v and len(v) >= 6:
            s = s.replace(v, "***")
    return s


def log(msg):
    print(masque(msg), flush=True)


tg = Telegram(SECRETS["TELEGRAM_TOKEN"], SECRETS["TELEGRAM_CHAT_ID"])


def notif(text):
    tg.send(masque(text))


def now_ms():
    return int(time.time() * 1000)


def jour_local():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def fmt(p):
    p = float(p)
    return f"{p:,.2f}" if p >= 1000 else (f"{p:.4f}" if p >= 1 else f"{p:.6f}")


# ══════════════════════════════════════════════════════════════════
# ETAT
# ══════════════════════════════════════════════════════════════════
def etat_defaut():
    return {"version": 2, "actif": True, "coupe_circuit": False,
            "capital_depart": None, "capital_bot": None,
            "jour": jour_local(), "pnl_jour": 0.0, "trades_jour": 0,
            "pertes_consec": 0, "pause_jour": None,
            "position": None, "tg_offset": 0, "bougies_vues": {},
            "historique": [], "total": 0, "gagnants": 0,
            "bilan_envoye": None, "derniere_erreur": 0,
            "alerte_inconnue": None, "demarre": False}


def charger_etat():
    if os.path.exists(ETAT):
        try:
            s = coffre.dechiffrer(open(ETAT).read().strip(), SECRETS["BINGX_API_SECRET"])
            base = etat_defaut()
            base.update(s)
            return base
        except Exception as e:
            log(f"Etat illisible ({type(e).__name__}) : redemarrage prudent")
            notif("⚠️ <b>Etat du bot illisible</b> — redemarrage avec un etat neuf.\n"
                  "Le bot reste prudent : il verifie BingX avant toute action.")
    return etat_defaut()


def sauver_etat(s):
    open(ETAT, "w").write(coffre.chiffrer(s, SECRETS["BINGX_API_SECRET"]) + "\n")


def nouveau_jour(s):
    j = jour_local()
    if s["jour"] != j:
        s.update(jour=j, pnl_jour=0.0, trades_jour=0, pertes_consec=0, pause_jour=None)


# ══════════════════════════════════════════════════════════════════
# COMMANDES TELEGRAM
# ══════════════════════════════════════════════════════════════════
AIDE = ("🤖 <b>Commandes</b>\n"
        "/status — etat, capital, position\n"
        "/stop — plus aucun nouveau trade\n"
        "/go — reprendre apres /stop\n"
        "/fermer — fermer la position en cours tout de suite\n"
        "/reset_securite — lever le coupe-circuit (apres reflexion)\n"
        "Les commandes sont lues au passage suivant (15 min max).")


def statut(s):
    wr = s["gagnants"] / s["total"] * 100 if s["total"] else 0
    p = s["position"]
    etat = ("⛔ coupe-circuit" if s["coupe_circuit"] else
            "⏸️ en pause" if not s["actif"] else
            "⏸️ pause jusqu'a demain" if s["pause_jour"] == s["jour"] else "▶️ actif")
    pos = (f"{'LONG' if p['dir'] > 0 else 'SHORT'} {p['symbol']} · SL {fmt(p['sl'])}" if p else "aucune")
    cap = s["capital_bot"] or 0
    dep = s["capital_depart"] or cap or 1
    return (f"📊 <b>ScalpRadar — {C.MODE.upper()}</b>\n"
            f"Etat : {etat}\n"
            f"Capital bot : <b>{cap:.2f} USDT</b> ({(cap / dep - 1) * 100:+.1f} % depuis le depart)\n"
            f"Aujourd'hui : {s['pnl_jour']:+.2f} USDT · {s['trades_jour']}/{C.MAX_TRADES_PAR_JOUR} trades\n"
            f"Bilan : {s['gagnants']}/{s['total']} gagnants ({wr:.0f} %)\n"
            f"Position : {pos}")


def traiter_commandes(s, api):
    off, cmds = tg.commands(s["tg_offset"])
    s["tg_offset"] = off
    for c in cmds:
        if c == "/stop":
            s["actif"] = False
            notif("⏸️ <b>Bot en pause.</b> Aucun nouveau trade. La position en cours garde son SL.\n/go pour reprendre.")
        elif c == "/go":
            s["actif"] = True
            notif("▶️ <b>Bot reactive.</b> Les autres garde-fous restent en vigueur.")
        elif c == "/status":
            notif(statut(s))
        elif c == "/fermer":
            p = s["position"]
            if p:
                try:
                    api.close_all(p["symbol"])
                    p["raison_sortie"] = "fermeture manuelle"
                    notif(f"🛑 Fermeture demandee pour {p['symbol']}. Bot mis en pause (/go pour reprendre).")
                except Exception as e:
                    notif(f"⚠️ Fermeture impossible : {masque(e)}\nFerme-la depuis l'app BingX.")
            else:
                notif("Aucune position ouverte par le bot.")
            s["actif"] = False
        elif c == "/reset_securite":
            s["coupe_circuit"] = False
            s["pause_jour"] = None
            s["pertes_consec"] = 0
            s["capital_depart"] = s["capital_bot"]
            notif("🔓 <b>Coupe-circuit leve.</b> Nouveau point de depart : "
                  f"{(s['capital_bot'] or 0):.2f} USDT.")
        elif c in ("/aide", "/help", "/start"):
            notif(AIDE)


# ══════════════════════════════════════════════════════════════════
# RAPPROCHEMENT AVEC BINGX
# ══════════════════════════════════════════════════════════════════
def stops_ouverts(api, symbol):
    return [o for o in (api.open_orders(symbol) or [])
            if o.get("type") in ("STOP_MARKET", "STOP")]


def cloturer_comptabilite(s, api, p, raison=None):
    time.sleep(2)
    pnl = api.income_since(p["ouvert_le"] - 60_000, p["symbol"])
    try:
        api.cancel_all(p["symbol"])  # retire un TP/SL orphelin
    except Exception:
        pass
    gagne = pnl > 0
    s["capital_bot"] = round(s["capital_bot"] + pnl, 4)
    s["pnl_jour"] = round(s["pnl_jour"] + pnl, 4)
    s["total"] += 1
    s["gagnants"] += int(gagne)
    s["pertes_consec"] = 0 if gagne else s["pertes_consec"] + 1
    s["historique"] = ([{"symbol": p["symbol"], "dir": p["dir"], "pnl": round(pnl, 4),
                         "ouvert": p["ouvert_le"], "ferme": now_ms()}] + s["historique"])[:100]
    s["position"] = None
    raison = raison or p.get("raison_sortie") or ("objectif atteint" if gagne else "stop-loss")
    wr = s["gagnants"] / s["total"] * 100
    notif(f"{'✅' if gagne else '🛑'} <b>Trade clos — {p['symbol']} {'LONG' if p['dir'] > 0 else 'SHORT'}</b>\n"
          f"Raison : {raison}\n"
          f"Resultat : <b>{pnl:+.2f} USDT</b> (frais compris)\n"
          f"Capital bot : <b>{s['capital_bot']:.2f} USDT</b>\n"
          f"Aujourd'hui : {s['pnl_jour']:+.2f} · Bilan {s['gagnants']}/{s['total']} ({wr:.0f} %)")
    if s["pertes_consec"] >= C.PAUSE_APRES_PERTES:
        s["pause_jour"] = s["jour"]
        notif(f"⏸️ <b>Pause jusqu'a demain</b> : {s['pertes_consec']} pertes d'affilee.")


def gerer_position(s, api, live):
    """Position du bot encore ouverte : SL present ? liquidation loin ? duree depassee ?"""
    p = s["position"]
    sym, d = p["symbol"], p["dir"]

    # 1. Duree maximale
    if now_ms() - p["ouvert_le"] > p["max_heures"] * 3_600_000:
        api.close_all(sym)
        cloturer_comptabilite(s, api, p, f"duree max ({p['max_heures']} h) atteinte")
        return

    # 2. Le SL existe-t-il toujours chez BingX ?
    stops = stops_ouverts(api, sym)
    if not stops:
        try:
            api.place_stop(sym, d, p["qty"], p["sl"], p["pos_side"])
            time.sleep(1)
            stops = stops_ouverts(api, sym)
        except Exception as e:
            log(f"Repose du SL impossible : {e}")
        if not stops:
            api.close_all(sym)
            s["actif"] = False
            notif("🚨 <b>SL introuvable et impossible a reposer</b> — position fermee par securite, "
                  "bot mis en pause. Verifie l'app BingX.")
            cloturer_comptabilite(s, api, p, "fermeture de securite (SL absent)")
            return
        notif(f"🛡️ SL absent sur {sym} : <b>repose automatiquement</b> a {fmt(p['sl'])}.")

    # 3. Liquidation derriere le SL ?
    liq = float(live.get("liquidationPrice") or 0)
    if liq > 0 and ((d > 0 and liq >= p["sl"]) or (d < 0 and liq <= p["sl"])):
        api.close_all(sym)
        s["actif"] = False
        notif("🚨 <b>Liquidation plus proche que le SL</b> — position fermee par securite, bot en pause.")
        cloturer_comptabilite(s, api, p, "fermeture de securite (liquidation)")
        return

    # 4. Stop suiveur (strategies a sortie suiveuse)
    if p.get("trail"):
        h1 = donnees.klines(p["pair"], "1h", 300)
        h1 = h1[h1["close_time"] > p["ouvert_le"]]
        if len(h1):
            best = h1["high"].max() if d > 0 else h1["low"].min()
            nouveau = best - d * p["trail"]
            gain = (nouveau - p["sl"]) * d
            if gain > 0.1 * p["atr"] and (stops and (nouveau - float(stops[0].get("stopPrice", 0))) * d > 0):
                prec = int(api.contract(sym)["pricePrecision"])
                nouveau = round_price(nouveau, prec)
                api.place_stop(sym, d, p["qty"], nouveau, p["pos_side"])   # nouveau d'abord…
                time.sleep(1)
                if len(stops_ouverts(api, sym)) > len(stops):
                    for o in stops:                                          # …puis l'ancien
                        api.cancel(sym, o.get("orderId"))
                    p["sl"] = nouveau
                    log("Stop suiveur remonte")


def rapprocher(s, api):
    """BingX fait foi. Renvoie la liste des positions ouvertes sur le compte."""
    positions = api.positions()
    p = s["position"]
    if p:
        live = [x for x in positions if x.get("symbol") == p["symbol"]
                and (p.get("pos_side", "BOTH") == "BOTH" or x.get("positionSide") == p["pos_side"])]
        if live:
            gerer_position(s, api, live[0])
        else:
            cloturer_comptabilite(s, api, p)
        positions = api.positions()

    mine = s["position"]["symbol"] if s["position"] else None
    autres = sorted({x.get("symbol") for x in positions if x.get("symbol") != mine})
    if autres and s["alerte_inconnue"] != autres:
        notif("ℹ️ Position(s) non ouverte(s) par le bot detectee(s) : " + ", ".join(autres) +
              "\nLe bot n'y touche pas et n'ouvrira rien tant qu'elles existent.")
    s["alerte_inconnue"] = autres or None
    return positions


# ══════════════════════════════════════════════════════════════════
# GARDE-FOUS
# ══════════════════════════════════════════════════════════════════
def garde_fous(s, positions, equity):
    if s["coupe_circuit"]:
        return "coupe-circuit actif"
    plancher = s["capital_depart"] * (1 - C.COUPE_CIRCUIT_PCT / 100)
    if s["capital_bot"] <= plancher:
        s["coupe_circuit"] = True
        notif(f"⛔ <b>COUPE-CIRCUIT</b> : capital {s['capital_bot']:.2f} USDT, "
              f"soit -{C.COUPE_CIRCUIT_PCT:.0f} % depuis le depart.\n"
              "Le bot ne trade plus. Prends le temps d'analyser, puis /reset_securite si tu veux reprendre.")
        return "coupe-circuit declenche"
    if C.MODE == "reel" and equity < s["capital_bot"] * 0.8:
        s["coupe_circuit"] = True
        notif("⛔ <b>COUPE-CIRCUIT</b> : le solde reel du compte est tres inferieur au capital du bot. "
              "Retrait, perte externe ? Le bot s'arrete. /reset_securite apres verification.")
        return "ecart compte/bot"
    if not s["actif"]:
        return "bot en pause (/go)"
    if s["pause_jour"] == s["jour"]:
        return "pause jusqu'a demain (pertes consecutives)"
    if positions:
        return "une position est ouverte"
    if s["trades_jour"] >= C.MAX_TRADES_PAR_JOUR:
        return "limite de trades du jour"
    if s["pnl_jour"] <= -s["capital_bot"] * C.PERTE_MAX_JOUR_PCT / 100:
        return "perte max du jour atteinte"
    return None


# ══════════════════════════════════════════════════════════════════
# SIGNAL ET EXECUTION
# ══════════════════════════════════════════════════════════════════
def chercher_signal(s):
    for pair in C.PAIRES:
        try:
            h1 = strategie.prepare(donnees.jeu_complet(pair))
        except Exception as e:
            log(f"{pair} : donnees indisponibles ({type(e).__name__})")
            continue
        sig = strategie.signal_derniere_bougie(h1, C.STRATEGIE, C.PARAMETRES)
        if not sig:
            continue
        cle = str(sig["bougie_close_time"])
        if s["bougies_vues"].get(pair) == cle:
            continue  # deja traite (meme bougie)
        s["bougies_vues"][pair] = cle
        sig["pair"] = pair
        return sig
    return None


def executer(s, api, sig, equity, dispo):
    pair, d = sig["pair"], sig["dir"]
    sym = to_symbol(pair)
    sens = "LONG" if d > 0 else "SHORT"

    def refus(raison):
        log(f"Signal {sym} ignore : {raison}")
        notif(f"🔎 Signal {sens} {sym} <b>ignore</b> : {raison}.")
        return None

    c = api.contract(sym)
    if str(c.get("status")) != "1" or str(c.get("apiStateOpen", "true")).lower() != "true":
        return refus("paire suspendue")

    age_min = (now_ms() - sig["bougie_close_time"]) / 60_000
    if age_min > C.SIGNAL_AGE_MAX_MIN:
        return refus(f"signal trop ancien ({age_min:.0f} min)")

    px = api.mark_price(sym)
    if abs(px - sig["close"]) > C.DERIVE_PRIX_MAX_ATR * sig["atr"]:
        return refus("le prix est deja parti trop loin")

    sl, tp = sig["sl"], sig["tp"]
    if (px - sl) * d <= 0:
        return refus("le prix a deja franchi le stop")
    dist_pct = abs(px - sl) / px * 100
    if not C.DISTANCE_SL_MIN_PCT <= dist_pct <= C.DISTANCE_SL_MAX_PCT:
        return refus(f"distance SL hors bornes ({dist_pct:.2f} %)")
    if tp is not None:
        if (tp - px) * d <= 0:
            return refus("objectif deja atteint")
        if abs(tp - px) / abs(px - sl) < 1.2:
            return refus("rapport gain/risque devenu insuffisant")

    # ── Taille : perte au SL (frais + glissement compris) = risque fixe ──
    capital = min(s["capital_bot"], C.CAPITAL_MAX, equity)
    risque = capital * C.RISQUE_PAR_TRADE_PCT / 100
    fee = max(float(c.get("takerFeeRate") or 0.0005), 0.0005)
    perte_unitaire = abs(px - sl) + px * (2 * fee + 0.001)
    qty = risque / perte_unitaire
    qty = min(qty, capital * C.POSITION_MAX_X_CAPITAL / px)

    # ── Levier : marge limitee ET liquidation tres loin derriere le SL ──
    mmr = 0.005
    lev_liq = int(1 / (C.LIQUIDATION_MIN_X_SL * dist_pct / 100 + mmr))
    lev_max = max(1, min(C.LEVIER_MAX, lev_liq, int(c.get("maxLongLeverage" if d > 0 else "maxShortLeverage") or 10)))
    marge_max = min(capital * C.MARGE_MAX_PCT / 100, dispo * 0.95)
    qty = min(qty, marge_max * lev_max / px)
    qty = floor_to(qty, int(c["quantityPrecision"]))
    notionnel = qty * px
    if qty < float(c.get("tradeMinQuantity") or 0) or notionnel < float(c.get("tradeMinUSDT") or 0):
        return refus("capital trop faible pour la taille minimale de cette paire")
    lev = max(1, math.ceil(notionnel / marge_max))
    if lev > lev_max:
        return refus("levier necessaire trop eleve")

    prec = int(c["pricePrecision"])
    sl_r = round_price(sl, prec)
    tp_r = round_price(tp, prec) if tp is not None else None

    hedge = api.hedge_mode()
    pos_side = sens if hedge else "BOTH"
    api.ensure_isolated(sym)
    api.ensure_leverage(sym, lev, pos_side)

    cid = "sr" + format(now_ms(), "x")
    try:
        api.market_with_sl_tp(sym, d, qty, sl_r, tp_r, pos_side, cid)
    except NetworkError:
        log("Reponse perdue apres l'envoi : verification de l'etat reel")
    time.sleep(2)

    live = [x for x in api.positions(sym)
            if pos_side == "BOTH" or x.get("positionSide") == pos_side]
    if not live:
        return refus("ordre non execute par BingX")
    pos = live[0]
    entree = float(pos.get("avgPrice") or px)
    qty_reelle = abs(float(pos.get("positionAmt")))

    # ── Verification : SL reellement pose ? ──
    if not stops_ouverts(api, sym):
        try:
            api.place_stop(sym, d, qty_reelle, sl_r, pos_side)
            time.sleep(1)
        except Exception as e:
            log(f"SL de secours refuse : {e}")
        if not stops_ouverts(api, sym):
            api.close_all(sym)
            s["actif"] = False
            notif("🚨 <b>SL non pose par BingX</b> — position fermee immediatement, bot en pause.")
            return None

    liq = float(pos.get("liquidationPrice") or 0)
    if liq > 0 and ((d > 0 and liq >= sl_r) or (d < 0 and liq <= sl_r)):
        api.close_all(sym)
        s["actif"] = False
        notif("🚨 Liquidation plus proche que le SL a l'ouverture — position fermee, bot en pause.")
        return None

    s["position"] = {"symbol": sym, "pair": pair, "dir": d, "entree": entree, "qty": qty_reelle,
                     "sl": sl_r, "tp": tp_r, "lev": lev, "pos_side": pos_side,
                     "ouvert_le": now_ms(), "max_heures": sig["max_heures"],
                     "trail": sig["trail"], "atr": sig["atr"]}
    s["trades_jour"] += 1
    perte_max = qty_reelle * abs(entree - sl_r) + notionnel * 2 * fee
    gain_vise = qty_reelle * abs(tp_r - entree) - notionnel * 2 * fee if tp_r else None
    notif(f"🟢 <b>{'[DEMO] ' if C.MODE == 'demo' else ''}POSITION OUVERTE — {sens} {sym}</b>\n"
          f"Entree : <b>{fmt(entree)}</b>\n"
          f"🛑 SL : <b>{fmt(sl_r)}</b> → perte max ≈ <b>-{perte_max:.2f} USDT</b>\n"
          + (f"✅ TP : <b>{fmt(tp_r)}</b> → gain ≈ <b>+{gain_vise:.2f} USDT</b>\n" if tp_r else
             "✅ Sortie : stop suiveur (le gain n'est pas plafonne)\n")
          + f"Taille {notionnel:.2f} USDT · levier ×{lev} · marge isolee\n"
          f"SL verifie chez BingX ✔️ · trade {s['trades_jour']}/{C.MAX_TRADES_PAR_JOUR} du jour")
    log("Position ouverte et verifiee")
    return True


# ══════════════════════════════════════════════════════════════════
# BILAN QUOTIDIEN (preuve de vie)
# ══════════════════════════════════════════════════════════════════
def bilan(s):
    if datetime.now(TZ).hour >= C.HEURE_BILAN and s["bilan_envoye"] != s["jour"]:
        s["bilan_envoye"] = s["jour"]
        notif("📋 <b>Bilan du jour</b> (le bot est en vie)\n" + statut(s).split("\n", 1)[1])


# ══════════════════════════════════════════════════════════════════
def main():
    manquants = [k for k, v in SECRETS.items() if not v]
    if manquants:
        log("Secrets manquants : " + ", ".join(manquants))
        notif("⚠️ Secrets GitHub manquants : " + ", ".join(manquants))
        return
    if C.MODE not in ("demo", "reel"):
        notif("⚠️ MODE invalide dans config.py (demo ou reel).")
        return

    s = charger_etat()
    nouveau_jour(s)
    api = BingX(SECRETS["BINGX_API_KEY"], SECRETS["BINGX_API_SECRET"], env=C.MODE)
    traiter_commandes(s, api)

    try:
        equity, dispo = api.equity()
        if s["capital_bot"] is None:
            s["capital_bot"] = s["capital_depart"] = round(min(equity, C.CAPITAL_MAX), 4)
        if not s["demarre"]:
            s["demarre"] = True
            notif(f"🚀 <b>ScalpRadar V2 demarre — mode {C.MODE.upper()}</b>\n"
                  f"Capital gere : {s['capital_bot']:.2f} USDT · strategie « {C.STRATEGIE} »\n"
                  f"Risque par trade : {C.RISQUE_PAR_TRADE_PCT:.0f} % · 1 position max · SL toujours pose chez BingX\n\n"
                  + AIDE)

        positions = rapprocher(s, api)
        raison = garde_fous(s, positions, equity)
        if raison:
            log(f"Pas de nouveau trade : {raison}")
        else:
            sig = chercher_signal(s)
            if sig:
                log("Signal detecte")
                executer(s, api, sig, equity, dispo)
            else:
                log("Aucun signal")
        bilan(s)
    except (BingXError, NetworkError, Exception) as e:
        log("Erreur : " + masque(f"{type(e).__name__}: {e}"))
        traceback.print_exc(file=sys.stdout) if os.environ.get("DEBUG") else None
        if now_ms() - s.get("derniere_erreur", 0) > 3_600_000:  # 1 alerte/heure max
            s["derniere_erreur"] = now_ms()
            notif("⚠️ <b>Erreur du bot</b> : " + masque(f"{type(e).__name__}: {e}") +
                  "\nTa position eventuelle reste protegee par son SL chez BingX.")
    finally:
        sauver_etat(s)


if __name__ == "__main__":
    main()
