# ══════════════════════════════════════════════════════
# CONFIGURATION DU BOT — REMPLIS TES VALEURS ICI
# ══════════════════════════════════════════════════════

# ── TELEGRAM (obligatoire) ──
# 1. Sur Telegram, cherche "@BotFather", tape /newbot, suis les instructions
#    Il te donnera un TOKEN du genre : 123456789:ABCdef...
# 2. Pour le CHAT_ID : cherche "@userinfobot" sur Telegram, tape /start
#    Il te donnera ton ID (un nombre)
TELEGRAM_TOKEN = "8711541062:AAFQ11qY8Htz-SGIUi31cWiyjIWuPfFNOJQ"
TELEGRAM_CHAT_ID = "1766086477"

# ── TES PARAMÈTRES DE TRADING ──
CAPITAL = 38         # Ta bankroll totale en €
ALLOC_PCT = 50       # % de la BK par trade (50 = la moitié)
RISK_PCT = 5         # % de risque max sur la MISE (pas la BK totale)

# ── PAIRES À SURVEILLER ──
PAIRS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
]

# ── MODES ACTIFS ──
SCALP_ENABLED = True   # scan scalp toutes les 90 secondes
SWING_ENABLED = True   # scan swing toutes les 5 minutes
