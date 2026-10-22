# -*- coding: utf-8 -*-
"""
config.py — Reglages du bot. AUCUN SECRET ICI (les cles vivent dans les GitHub Secrets).
"""

# ══════════════════════════════════════════════════════
# MODE
# ══════════════════════════════════════════════════════
# "demo" = compte de demonstration BingX (VST, argent fictif) : vrais ordres, vrai TP/SL,
#          zero risque. A utiliser pendant plusieurs semaines AVANT tout passage en reel.
# "reel" = argent reel.
MODE = "demo"

# ══════════════════════════════════════════════════════
# CAPITAL ET RISQUE (en USDT)
# ══════════════════════════════════════════════════════
# Le bot ne raisonne JAMAIS sur plus que ce montant, meme si le compte contient davantage.
CAPITAL_MAX = 46.0               # ~40 €

RISQUE_PAR_TRADE_PCT = 2.0       # perte max si le SL est touche, frais inclus (≈ 0,92 USDT)
POSITION_MAX_X_CAPITAL = 3.0     # taille de position <= 3x le capital
MARGE_MAX_PCT = 50.0             # marge immobilisee <= 50 % du capital
LEVIER_MAX = 10                  # plafond absolu
LIQUIDATION_MIN_X_SL = 3.0       # la liquidation doit etre au moins 3x plus loin que le SL

# ══════════════════════════════════════════════════════
# GARDE-FOUS
# ══════════════════════════════════════════════════════
MAX_TRADES_PAR_JOUR = 3
PERTE_MAX_JOUR_PCT = 5.0         # au-dela : plus aucun trade jusqu'au lendemain
PAUSE_APRES_PERTES = 2           # pertes consecutives -> pause jusqu'au lendemain
COUPE_CIRCUIT_PCT = 25.0         # capital -25 % depuis le depart -> arret total (/reset_securite)
SIGNAL_AGE_MAX_MIN = 75          # signal trop vieux (retard GitHub) -> ignore
DERIVE_PRIX_MAX_ATR = 0.5        # prix parti trop loin depuis le signal -> ignore
DISTANCE_SL_MIN_PCT = 0.35       # SL trop serre -> ignore (bruit + frais)
DISTANCE_SL_MAX_PCT = 6.0        # SL trop large -> ignore

# ══════════════════════════════════════════════════════
# STRATEGIE (choisie d'apres le backtest — voir backtest/resultats_backtest.md)
# ══════════════════════════════════════════════════════
STRATEGIE = "pullback"
PARAMETRES = {"tp_r": 2.0, "adx": 25}

PAIRES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT"]

# Bilan quotidien envoye a partir de cette heure (Europe/Zurich) : preuve de vie du bot.
HEURE_BILAN = 21
