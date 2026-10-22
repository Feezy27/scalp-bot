# -*- coding: utf-8 -*-
"""
config.py — Reglages du bot full auto.
AUCUN SECRET ICI : les cles API et Telegram vivent dans les GitHub Secrets.
Ce fichier peut donc rester public sans risque.
"""

# ══════════════════════════════════════════════════════
# MODE DE SECURITE
# ══════════════════════════════════════════════════════
# True  = le bot analyse et notifie, mais NE PASSE AUCUN ORDRE REEL.
# False = trading reel avec de l'argent.
# Laisse True quelques jours pour verifier les signaux avant de passer en reel.
DRY_RUN = True

# ══════════════════════════════════════════════════════
# CAPITAL
# ══════════════════════════════════════════════════════
INITIAL_BANKROLL = 40.0   # bankroll de depart en € (sert au 1er lancement)
ALLOC_PCT = 50            # % de la bankroll engage par trade -> 20€
RISK_PCT = 5              # % de perte max sur la MISE si le SL est touche
                          # -> plafond garanti : 5% de 20€ = 1€ max par trade (2.5% de la BK)
                          # Le levier est calcule puis ARRONDI VERS LE BAS : jamais au-dessus.

# ══════════════════════════════════════════════════════
# STRATEGIE
# ══════════════════════════════════════════════════════
MODE = "swing"            # 'swing' (4h/1h/15m) — recommande en auto
CONF_MIN = 80             # confiance minimale pour declencher un ordre

PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "AVAXUSDT",
]

# ══════════════════════════════════════════════════════
# GARDE-FOUS (le bot s'arrete tout seul)
# ══════════════════════════════════════════════════════
MAX_OPEN_POSITIONS = 1              # une seule position a la fois
MAX_TRADES_PER_DAY = 3              # maximum 3 ordres par jour
MAX_DAILY_LOSS_PCT = 10             # stop si perte du jour > 10% de la bankroll (4€ sur 40€)
PAUSE_AFTER_CONSECUTIVE_LOSSES = 2  # pause apres 2 pertes d'affilee
