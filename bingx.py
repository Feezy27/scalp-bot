# -*- coding: utf-8 -*-
"""
bingx.py — Client BingX Futures perpetuels USDT-M.

Conforme a la specification officielle BingX (depot bingx-api/api-ai-skills) :
  * parametres tries par ordre ASCII, signes NON encodes (HMAC-SHA256, hex)
  * recvWindow <= 5000 ms
  * valeurs JSON (stopLoss/takeProfit) serialisees une seule fois, nombres non quotes
  * bascule vers le domaine .pro UNIQUEMENT sur erreur reseau, jamais sur erreur metier
  * refus de toute valeur contenant & = ? # ou retour ligne (injection de parametres)

Bibliotheque standard uniquement : aucune dependance tierce ne voit la cle.
Ce module ne contient AUCUNE fonction de retrait ou de transfert.
"""
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ENVS = {
    "reel": ["https://open-api.bingx.com", "https://open-api.bingx.pro"],
    "demo": ["https://open-api-vst.bingx.com", "https://open-api-vst.bingx.pro"],
}
FORBIDDEN = re.compile(r"[&=?#\r\n]")
SYMBOL_RE = re.compile(r"^[A-Z0-9]+-[A-Z]+$")


class BingXError(Exception):
    """Erreur metier renvoyee par BingX (code != 0)."""

    def __init__(self, code, msg):
        super().__init__(f"BingX {code} : {msg}")
        self.code = code


class NetworkError(Exception):
    """Aucun domaine BingX n'a repondu."""


def _ser(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    if isinstance(v, float):
        s = ("%.10f" % v).rstrip("0").rstrip(".")  # jamais de notation scientifique
        return s if s else "0"
    return str(v)


class BingX:
    def __init__(self, key, secret, env="demo", timeout=10):
        if env not in ENVS:
            raise ValueError("env doit etre 'demo' ou 'reel'")
        self.key, self.secret, self.env, self.timeout = key, secret, env, timeout
        self._contracts = None

    # ── Requete signee ──────────────────────────────────────────
    def _call(self, method, path, params=None, signed=True):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if "symbol" in params and not SYMBOL_RE.match(str(params["symbol"])):
            raise ValueError(f"symbole invalide : {params['symbol']}")
        if signed:
            params["recvWindow"] = 5000
            params["timestamp"] = int(time.time() * 1000)
        flat = {k: _ser(v) for k, v in params.items()}
        for k, v in flat.items():
            if FORBIDDEN.search(v):
                raise ValueError(f"caractere interdit dans {k}")
        canonical = "&".join(f"{k}={flat[k]}" for k in sorted(flat))
        if signed:
            sig = hmac.new(self.secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
            signed_str = (canonical + "&" if canonical else "") + f"signature={sig}"
        else:
            signed_str = canonical
        needs_enc = "{" in canonical or "[" in canonical
        headers = {"X-SOURCE-KEY": "BX-AI-SKILL", "User-Agent": "scalpradar/2"}
        if signed:
            headers["X-BX-APIKEY"] = self.key

        last = None
        for base in ENVS[self.env]:
            if method == "POST":
                url, body = base + path, signed_str.encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                if needs_enc:
                    q = "&".join(f"{k}={urllib.parse.quote(flat[k], safe='') if ('{' in flat[k] or '[' in flat[k]) else flat[k]}"
                                 for k in sorted(flat))
                    q += f"&signature={sig}" if signed else ""
                else:
                    q = signed_str
                url, body = base + path + (("?" + q) if q else ""), None
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
            except urllib.error.HTTPError as e:
                raw = e.read()  # reponse HTTP valide : erreur metier, pas de bascule
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                continue  # erreur reseau : on tente le domaine de secours
            data = json.loads(raw.decode())
            code = data.get("code", 0)
            if str(code) != "0":
                raise BingXError(code, data.get("msg", ""))
            return data.get("data")
        raise NetworkError(f"BingX injoignable ({type(last).__name__})")

    # ── Marche (public) ─────────────────────────────────────────
    def contract(self, symbol):
        if self._contracts is None:
            rows = self._call("GET", "/openApi/swap/v2/quote/contracts", signed=False) or []
            self._contracts = {c["symbol"]: c for c in rows}
        c = self._contracts.get(symbol)
        if not c:
            raise BingXError("local", f"contrat {symbol} introuvable")
        return c

    def mark_price(self, symbol):
        d = self._call("GET", "/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol}, signed=False)
        if isinstance(d, list):
            d = d[0]
        return float(d["markPrice"])

    # ── Compte ──────────────────────────────────────────────────
    def equity(self):
        rows = self._call("GET", "/openApi/swap/v3/user/balance") or []
        for b in rows if isinstance(rows, list) else [rows]:
            if b.get("asset") in ("USDT", "VST"):
                return float(b["equity"]), float(b["availableMargin"])
        raise BingXError("local", "solde USDT introuvable")

    def positions(self, symbol=None):
        rows = self._call("GET", "/openApi/swap/v2/user/positions",
                          {"symbol": symbol} if symbol else {}) or []
        return [p for p in rows if abs(float(p.get("positionAmt", 0) or 0)) > 0]

    def open_orders(self, symbol=None):
        d = self._call("GET", "/openApi/swap/v2/trade/openOrders",
                       {"symbol": symbol} if symbol else {}) or {}
        return d.get("orders", d) if isinstance(d, dict) else d

    def income_since(self, start_ms, symbol=None):
        rows = self._call("GET", "/openApi/swap/v2/user/income",
                          {"startTime": int(start_ms), "limit": 1000, "symbol": symbol}) or []
        return sum(float(r.get("income", 0) or 0) for r in rows
                   if r.get("incomeType") in ("REALIZED_PNL", "TRADING_FEE", "FUNDING_FEE",
                                              "INSURANCE_CLEAR", "ADL"))

    def hedge_mode(self):
        d = self._call("GET", "/openApi/swap/v1/positionSide/dual") or {}
        return str(d.get("dualSidePosition", "false")).lower() == "true"

    # ── Reglages de position ────────────────────────────────────
    def ensure_isolated(self, symbol):
        cur = (self._call("GET", "/openApi/swap/v2/trade/marginType", {"symbol": symbol}) or {})
        if cur.get("marginType") != "ISOLATED":
            self._call("POST", "/openApi/swap/v2/trade/marginType",
                       {"symbol": symbol, "marginType": "ISOLATED"})
            cur = self._call("GET", "/openApi/swap/v2/trade/marginType", {"symbol": symbol}) or {}
        if cur.get("marginType") != "ISOLATED":
            raise BingXError("local", "impossible de passer en marge isolee")

    def ensure_leverage(self, symbol, lev, side):
        self._call("POST", "/openApi/swap/v2/trade/leverage",
                   {"symbol": symbol, "side": side, "leverage": int(lev)})
        d = self._call("GET", "/openApi/swap/v2/trade/leverage", {"symbol": symbol}) or {}
        key = "longLeverage" if side in ("LONG", "BOTH") else "shortLeverage"
        if int(d.get(key, -1)) != int(lev):
            raise BingXError("local", f"levier non applique ({d.get(key)} au lieu de {lev})")

    # ── Ordres ──────────────────────────────────────────────────
    def market_with_sl_tp(self, symbol, direction, qty, sl, tp, pos_side, client_id):
        p = {
            "symbol": symbol,
            "side": "BUY" if direction > 0 else "SELL",
            "positionSide": pos_side,
            "type": "MARKET",
            "quantity": qty,
            "clientOrderId": client_id,
            "stopLoss": {"type": "STOP_MARKET", "stopPrice": float(sl), "workingType": "MARK_PRICE"},
        }
        if tp is not None:
            p["takeProfit"] = {"type": "TAKE_PROFIT_MARKET", "stopPrice": float(tp),
                               "workingType": "MARK_PRICE"}
        return self._call("POST", "/openApi/swap/v2/trade/order", p)

    def place_stop(self, symbol, direction, qty, stop, pos_side):
        """Stop de protection autonome (ferme toute la position au declenchement)."""
        return self._call("POST", "/openApi/swap/v2/trade/order", {
            "symbol": symbol,
            "side": "SELL" if direction > 0 else "BUY",
            "positionSide": pos_side,
            "type": "STOP_MARKET",
            "quantity": qty,
            "stopPrice": float(stop),
            "workingType": "MARK_PRICE",
            "closePosition": "true",
        })

    def cancel(self, symbol, order_id):
        return self._call("DELETE", "/openApi/swap/v2/trade/order",
                          {"symbol": symbol, "orderId": order_id})

    def cancel_all(self, symbol):
        return self._call("DELETE", "/openApi/swap/v2/trade/allOpenOrders", {"symbol": symbol})

    def close_all(self, symbol):
        return self._call("POST", "/openApi/swap/v2/trade/closeAllPositions", {"symbol": symbol})


def to_symbol(pair):
    """BTCUSDT -> BTC-USDT"""
    return pair[:-4] + "-USDT" if pair.endswith("USDT") else pair


def floor_to(x, decimals):
    f = 10 ** int(decimals)
    return int(float(x) * f + 1e-9) / f


def round_price(x, decimals):
    return round(float(x), int(decimals))
