# -*- coding: utf-8 -*-
"""
Tests du bot V2 contre un FAUX serveur BingX local qui verifie chaque signature
selon la specification officielle, et simule les reponses (positions, ordres, SL...).
Aucune connexion reelle. Lancer : python tests/test_bot.py
"""
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(os.environ.get("TEST_DIR", "/tmp"))

KEY, SECRET = "cle-test-123456", "secret-test-abcdef"
os.environ.update({"BINGX_API_KEY": KEY, "BINGX_API_SECRET": SECRET,
                   "TELEGRAM_TOKEN": "", "TELEGRAM_CHAT_ID": "999"})

import bingx  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("  OK   " if cond else "  ECHEC ") + name + (f"  ({detail})" if detail and not cond else ""))


# ══════════════════════════════════════════════════════════════════
# FAUX SERVEUR BINGX
# ══════════════════════════════════════════════════════════════════
class Fake:
    def reset(self):
        self.equity = 10000.0
        self.positions = []
        self.orders = []
        self.income = []
        self.margin = "CROSSED"
        self.lev = 20
        self.attach_sl = True          # BingX accepte-t-il le SL attache ?
        self.allow_stop = True         # BingX accepte-t-il un STOP autonome ?
        self.liq_factor = 0.5          # distance de liquidation (fraction du prix / levier)
        self.mark = 100.0
        self.calls = []
        self.sig_errors = 0
        self.hedge = False


F = Fake()
F.reset()


def verify(params):
    sig = params.pop("signature", None)
    canon = "&".join(f"{k}={params[k]}" for k in sorted(params))
    good = hmac.new(SECRET.encode(), canon.encode(), hashlib.sha256).hexdigest()
    if sig != good:
        F.sig_errors += 1
        return False
    return True


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, data, code=0, msg=""):
        body = json.dumps({"code": code, "msg": msg, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _params(self):
        u = urllib.parse.urlparse(self.path)
        if self.command == "POST":
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            # le corps est la chaine signee brute (comme l'exemple officiel)
            pairs = [p.split("=", 1) for p in raw.split("&") if p]
        else:
            pairs = [(k, urllib.parse.unquote(v)) for k, v in
                     (p.split("=", 1) for p in u.query.split("&") if p)]
        return u.path, dict(pairs)

    def handle_any(self):
        path, p = self._params()
        F.calls.append((self.command, path, dict(p)))
        public = path.startswith("/openApi/swap/v2/quote")
        if not public:
            if self.headers.get("X-BX-APIKEY") != KEY or not verify(p):
                return self._reply(None, 100001, "signature")
            if int(p.get("recvWindow", 0)) > 5000:
                return self._reply(None, 109400, "recvWindow")
        sym = p.get("symbol")
        if path == "/openApi/swap/v2/quote/contracts":
            return self._reply([{"symbol": s, "status": 1, "apiStateOpen": "true",
                                 "quantityPrecision": 3, "pricePrecision": 2,
                                 "tradeMinQuantity": 0.001, "tradeMinUSDT": 2,
                                 "takerFeeRate": 0.0005, "maxLongLeverage": 50,
                                 "maxShortLeverage": 50}
                                for s in ("BTC-USDT", "ETH-USDT", "SOL-USDT")])
        if path == "/openApi/swap/v2/quote/premiumIndex":
            return self._reply({"symbol": sym, "markPrice": str(F.mark)})
        if path == "/openApi/swap/v3/user/balance":
            return self._reply([{"asset": "USDT", "equity": str(F.equity),
                                 "availableMargin": str(F.equity)}])
        if path == "/openApi/swap/v2/user/positions":
            return self._reply([x for x in F.positions if not sym or x["symbol"] == sym])
        if path == "/openApi/swap/v2/trade/openOrders":
            return self._reply({"orders": [o for o in F.orders if not sym or o["symbol"] == sym]})
        if path == "/openApi/swap/v1/positionSide/dual":
            return self._reply({"dualSidePosition": F.hedge})
        if path == "/openApi/swap/v2/trade/marginType":
            if self.command == "POST":
                F.margin = p["marginType"]
                return self._reply({})
            return self._reply({"marginType": F.margin})
        if path == "/openApi/swap/v2/trade/leverage":
            if self.command == "POST":
                F.lev = int(p["leverage"])
                return self._reply({"leverage": F.lev})
            return self._reply({"longLeverage": F.lev, "shortLeverage": F.lev})
        if path == "/openApi/swap/v2/user/income":
            return self._reply([i for i in F.income if int(i["time"]) >= int(p.get("startTime", 0))])
        if path == "/openApi/swap/v2/trade/order" and self.command == "POST":
            if p["type"] == "MARKET":
                sl = json.loads(p["stopLoss"]) if "stopLoss" in p else None
                if sl is not None and not isinstance(sl["stopPrice"], (int, float)):
                    return self._reply(None, 101400, "stopPrice doit etre un nombre")
                qty = float(p["quantity"])
                d = 1 if p["side"] == "BUY" else -1
                lev = F.lev
                liq = F.mark * (1 - d * F.liq_factor / lev)
                F.positions.append({"symbol": sym, "positionAmt": str(qty * d), "avgPrice": str(F.mark),
                                    "liquidationPrice": liq, "positionSide": p["positionSide"],
                                    "leverage": lev, "isolated": F.margin == "ISOLATED"})
                if sl and F.attach_sl:
                    F.orders.append({"symbol": sym, "type": "STOP_MARKET", "stopPrice": str(sl["stopPrice"]),
                                     "orderId": len(F.orders) + 1})
                if "takeProfit" in p:
                    tp = json.loads(p["takeProfit"])
                    F.orders.append({"symbol": sym, "type": "TAKE_PROFIT_MARKET",
                                     "stopPrice": str(tp["stopPrice"]), "orderId": len(F.orders) + 1})
                return self._reply({"order": {"orderId": 1, "status": "FILLED"}})
            if p["type"] == "STOP_MARKET":
                if not F.allow_stop:
                    return self._reply(None, 110400, "refuse")
                F.orders.append({"symbol": sym, "type": "STOP_MARKET", "stopPrice": p["stopPrice"],
                                 "orderId": len(F.orders) + 1})
                return self._reply({"order": {"orderId": len(F.orders)}})
        if path == "/openApi/swap/v2/trade/closeAllPositions":
            F.positions = [x for x in F.positions if x["symbol"] != sym]
            return self._reply({"success": [1]})
        if path == "/openApi/swap/v2/trade/allOpenOrders":
            F.orders = [o for o in F.orders if o["symbol"] != sym]
            return self._reply({})
        if path == "/openApi/swap/v2/trade/order" and self.command == "DELETE":
            F.orders = [o for o in F.orders if str(o["orderId"]) != p.get("orderId")]
            return self._reply({})
        return self._reply(None, 109400, f"inconnu {path}")

    do_GET = do_POST = do_DELETE = handle_any


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
bingx.ENVS["demo"] = [BASE]
bingx.ENVS["reel"] = [BASE]

import autotrade as A  # noqa: E402
import coffre  # noqa: E402

SENT = []
A.notif = lambda t: SENT.append(t)
A.time.sleep = lambda s: None


def sig(d=1, sl=97.0, tp=106.0, close=100.0, atr=1.0, age_min=5, trail=None):
    return {"pair": "BTCUSDT", "dir": d, "sl": sl, "tp": tp, "max_heures": 48, "trail": trail,
            "close": close, "atr": atr, "bougie_close_time": A.now_ms() - age_min * 60_000}


def fresh_state():
    s = A.etat_defaut()
    s["capital_bot"] = s["capital_depart"] = 46.0
    s["demarre"] = True
    return s


def api():
    return A.BingX(KEY, SECRET, env="demo")


# ══════════════════════════════════════════════════════════════════
print("\n[1] Signature et serialisation conformes a la doc BingX")
F.reset()
a = api()
eq, _ = a.equity()
check("requete signee acceptee par le serveur (signature verifiee)", eq == 10000.0 and F.sig_errors == 0)
check("recvWindow <= 5000", all(int(c[2].get("recvWindow", 0)) <= 5000 for c in F.calls if "recvWindow" in c[2]))
try:
    a._call("GET", "/openApi/swap/v2/user/positions", {"symbol": "BTC-USDT&side=SELL"})
    check("injection de parametre bloquee", False)
except ValueError:
    check("injection de parametre bloquee", True)
check("pas de notation scientifique", bingx._ser(0.00001) == "0.00001")

print("\n[2] Ouverture nominale : marge isolee, levier relu, SL+TP poses et verifies")
F.reset()
s = fresh_state()
ok = A.executer(s, api(), sig(), 10000.0, 10000.0)
p = s["position"]
check("position ouverte", ok and p is not None)
check("marge isolee appliquee", F.margin == "ISOLATED")
check("SL present chez BingX", any(o["type"] == "STOP_MARKET" for o in F.orders))
check("TP present chez BingX", any(o["type"] == "TAKE_PROFIT_MARKET" for o in F.orders))
risk = p["qty"] * abs(p["entree"] - p["sl"])
check("perte au SL <= 2 % du capital (46 USDT)", risk <= 0.92 + 1e-9, f"{risk:.3f}")
check("taille <= 3x le capital", p["qty"] * p["entree"] <= 46 * 3 + 1e-6)
check("marge <= 50 % du capital", p["qty"] * p["entree"] / p["lev"] <= 23 + 1e-6)
check("levier <= 10", p["lev"] <= 10, p["lev"])
check("aucune erreur de signature", F.sig_errors == 0)

print("\n[3] BingX n'attache pas le SL -> le bot le repose lui-meme")
F.reset(); F.attach_sl = False
s = fresh_state()
A.executer(s, api(), sig(), 10000.0, 10000.0)
check("SL repose par le bot", any(o["type"] == "STOP_MARKET" for o in F.orders))
check("position conservee et suivie", s["position"] is not None and len(F.positions) == 1)

print("\n[4] SL impossible a poser -> fermeture immediate + pause")
F.reset(); F.attach_sl = False; F.allow_stop = False
s = fresh_state(); SENT.clear()
A.executer(s, api(), sig(), 10000.0, 10000.0)
check("position fermee", F.positions == [])
check("bot mis en pause", s["actif"] is False)
check("alerte envoyee", any("SL non pose" in t for t in SENT))

print("\n[5] Liquidation plus proche que le SL -> fermeture")
F.reset(); F.liq_factor = 0.001  # liquidation colle au prix
s = fresh_state()
A.executer(s, api(), sig(), 10000.0, 10000.0)
check("position fermee par securite", F.positions == [] and s["actif"] is False)

print("\n[6] Refus des signaux dangereux ou perimes")
for name, kw, F_mark in [("signal trop ancien", {"age_min": 120}, 100.0),
                         ("prix parti trop loin", {}, 101.0),
                         ("prix deja sous le SL", {}, 96.5),
                         ("SL trop serre (<0.35 %)", {"sl": 99.8}, 100.0),
                         ("SL trop large (>6 %)", {"sl": 90.0, "atr": 20.0}, 100.0)]:
    F.reset(); F.mark = F_mark
    s = fresh_state()
    A.executer(s, api(), sig(**kw), 10000.0, 10000.0)
    check(name + " -> aucun ordre", s["position"] is None and F.positions == [])

print("\n[7] Capital trop faible pour la taille minimale -> refus (jamais d'arrondi vers le haut)")
F.reset(); F.mark = 100.0
s = fresh_state(); s["capital_bot"] = 0.1
A.executer(s, api(), sig(), 10000.0, 10000.0)
check("aucun ordre", F.positions == [])

print("\n[8] Suivi : SL supprime a la main -> repose au passage suivant")
F.reset()
s = fresh_state()
A.executer(s, api(), sig(), 10000.0, 10000.0)
F.orders = [o for o in F.orders if o["type"] != "STOP_MARKET"]
A.rapprocher(s, api())
check("SL repose", any(o["type"] == "STOP_MARKET" for o in F.orders))

print("\n[9] Cloture detectee -> comptabilite depuis BingX + pause apres 2 pertes")
F.reset()
s = fresh_state()
for k in range(2):
    A.executer(s, api(), sig(), 10000.0, 10000.0)
    F.positions = []
    F.income.append({"incomeType": "REALIZED_PNL", "income": "-0.85", "time": A.now_ms()})
    F.income.append({"incomeType": "TRADING_FEE", "income": "-0.05", "time": A.now_ms()})
    A.rapprocher(s, api())
    F.income = []
check("capital mis a jour (2 x -0.90)", abs(s["capital_bot"] - (46 - 1.8)) < 1e-6, s["capital_bot"])
check("pause jusqu'a demain", s["pause_jour"] == s["jour"])
check("garde-fou bloque", A.garde_fous(s, [], 10000.0) is not None)

print("\n[10] Perte max du jour et limite de trades")
s = fresh_state(); s["pnl_jour"] = -2.4
check("perte du jour > 5 % -> bloque", A.garde_fous(s, [], 10000.0) == "perte max du jour atteinte")
s = fresh_state(); s["trades_jour"] = 3
check("3 trades -> bloque", A.garde_fous(s, [], 10000.0) == "limite de trades du jour")
s = fresh_state()
check("position existante -> bloque", A.garde_fous(s, [{"symbol": "X"}], 10000.0) == "une position est ouverte")
s = fresh_state()
check("conditions normales -> autorise", A.garde_fous(s, [], 10000.0) is None)

print("\n[11] Coupe-circuit a -25 %")
s = fresh_state(); s["capital_bot"] = 34.0
r = A.garde_fous(s, [], 10000.0)
check("declenche", s["coupe_circuit"] and r is not None)
s["actif"] = True
check("/go ne leve PAS le coupe-circuit", A.garde_fous(s, [], 10000.0) == "coupe-circuit actif")

print("\n[12] Commandes Telegram : seul le proprietaire est obei")


class FakeTG:
    def __init__(self, msgs):
        self.msgs = msgs

    def commands(self, off):
        from telegram import Telegram
        t = Telegram("x", "999")
        t._api = lambda m, p: {"result": self.msgs}
        return t.commands(off)

    def send(self, t):
        SENT.append(t)


A.tg = FakeTG([{"update_id": 1, "message": {"chat": {"id": 666}, "from": {"id": 666}, "text": "/reset_securite"}},
               {"update_id": 2, "message": {"chat": {"id": 666}, "from": {"id": 666}, "text": "/go"}}])
s = fresh_state(); s["coupe_circuit"] = True; s["actif"] = False
A.traiter_commandes(s, api())
check("inconnu ignore (coupe-circuit maintenu)", s["coupe_circuit"] is True and s["actif"] is False)
A.tg = FakeTG([{"update_id": 3, "message": {"chat": {"id": 999}, "from": {"id": 999}, "text": "/stop"}}])
s = fresh_state()
A.traiter_commandes(s, api())
check("proprietaire obei (/stop)", s["actif"] is False)

print("\n[13] Etat chiffre")
blob = coffre.chiffrer({"capital": 46.0}, SECRET)
check("illisible sans la cle", "46" not in blob and "capital" not in blob)
check("relu avec la cle", coffre.dechiffrer(blob, SECRET) == {"capital": 46.0})
bad = blob[:-6] + ("A" if blob[-6] != "A" else "B") + blob[-5:]
try:
    coffre.dechiffrer(bad, SECRET); check("falsification detectee", False)
except Exception:
    check("falsification detectee", True)

print("\n[14] Masquage des secrets dans les logs")
check("cle masquee", KEY not in A.masque(f"erreur avec {KEY} et {SECRET}"))

print("\n[15] Cycle complet main() : demarrage, ouverture, puis suivi au passage suivant")
F.reset(); SENT.clear()
A.tg = FakeTG([])
for fn in ("etat.chiffre",):
    if os.path.exists(fn):
        os.remove(fn)
A.SECRETS.update({"BINGX_API_KEY": KEY, "BINGX_API_SECRET": SECRET, "TELEGRAM_TOKEN": "x", "TELEGRAM_CHAT_ID": "999"})
A.chercher_signal = lambda s: sig()
A.main()
check("message de demarrage envoye", any("demarre" in t for t in SENT))
check("position ouverte par main()", len(F.positions) == 1)
check("etat chiffre ecrit", os.path.exists("etat.chiffre"))
n = len(F.positions)
A.main()
check("2e passage : pas de 2e position", len(F.positions) == n)
check("aucune erreur remontee", not any("Erreur du bot" in t for t in SENT))

ok = sum(r for _, r in RESULTS)
print(f"\n{ok}/{len(RESULTS)} tests reussis")
srv.shutdown()
sys.exit(0 if ok == len(RESULTS) else 1)
