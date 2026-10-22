# -*- coding: utf-8 -*-
"""
bingx.py — Client API BingX Perpetual Futures (USDT-M).
Signature HMAC SHA256. Ne gere QUE le trading : aucune fonction de retrait.
"""
import hmac
import time
import json
import hashlib
import requests

BASE_URLS = ["https://open-api.bingx.com", "https://open-api.bingx.pro"]


class BingXError(Exception):
    pass


class BingX:
    def __init__(self, api_key, api_secret, timeout=15):
        self.key = api_key
        self.secret = api_secret
        self.timeout = timeout
        self._contracts = None

    # ── Signature ────────────────────────────────────────
    def _sign(self, params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        return qs + "&signature=" + sig

    def _request(self, method, path, params=None, signed=True):
        params = dict(params or {})
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            qs = self._sign(params)
        else:
            qs = "&".join(f"{k}={v}" for k, v in params.items())

        headers = {"X-BX-APIKEY": self.key} if signed else {}
        last_err = None

        for base in BASE_URLS:
            url = f"{base}{path}" + (f"?{qs}" if qs else "")
            try:
                r = requests.request(method, url, headers=headers, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                # BingX renvoie code=0 quand tout va bien
                if isinstance(data, dict) and data.get("code") not in (0, None, "0"):
                    raise BingXError(f"code {data.get('code')} : {data.get('msg')}")
                return data.get("data", data) if isinstance(data, dict) else data
            except BingXError:
                raise
            except Exception as e:
                last_err = e
                continue
        raise BingXError(f"Aucun endpoint BingX joignable ({last_err})")

    # ── Infos contrats (precision des quantites) ─────────
    def contracts(self):
        if self._contracts is None:
            data = self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)
            self._contracts = {c["symbol"]: c for c in (data or [])}
        return self._contracts

    def symbol_info(self, symbol):
        return self.contracts().get(symbol, {})

    def round_qty(self, symbol, qty):
        info = self.symbol_info(symbol)
        prec = int(info.get("quantityPrecision", 4) or 0)
        step = 10 ** (-prec)
        q = math_floor(qty, prec)
        min_q = float(info.get("tradeMinQuantity", 0) or 0)
        if min_q and q < min_q:
            q = min_q
        return round(q, prec) if prec > 0 else int(q)

    def round_price(self, symbol, price):
        info = self.symbol_info(symbol)
        prec = int(info.get("pricePrecision", 4) or 0)
        return round(float(price), prec)

    # ── Compte ───────────────────────────────────────────
    def balance(self):
        d = self._request("GET", "/openApi/swap/v2/user/balance")
        b = d.get("balance", d) if isinstance(d, dict) else d
        if isinstance(b, list):
            b = b[0] if b else {}
        return float(b.get("availableMargin") or b.get("balance") or 0)

    def positions(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        d = self._request("GET", "/openApi/swap/v2/user/positions", params)
        return d if isinstance(d, list) else []

    def is_hedge_mode(self):
        try:
            d = self._request("GET", "/openApi/swap/v1/positionSide/dual")
            v = d.get("dualSidePosition", d) if isinstance(d, dict) else d
            return str(v).lower() in ("true", "1")
        except Exception:
            return False

    def realized_pnl(self, symbol, start_ms):
        """Somme des PnL realises sur ce symbole depuis start_ms."""
        try:
            d = self._request("GET", "/openApi/swap/v2/user/income", {
                "symbol": symbol,
                "incomeType": "REALIZED_PNL",
                "startTime": int(start_ms),
                "limit": 200,
            })
            rows = d if isinstance(d, list) else []
            return sum(float(r.get("income", 0) or 0) for r in rows)
        except Exception:
            return None

    # ── Preparation de l'ordre ───────────────────────────
    def set_margin_isolated(self, symbol):
        try:
            self._request("POST", "/openApi/swap/v2/trade/marginType",
                          {"symbol": symbol, "marginType": "ISOLATED"})
        except Exception:
            pass  # deja isole : BingX renvoie une erreur benigne

    def set_leverage(self, symbol, leverage, side):
        for s in ([side] if side != "BOTH" else ["LONG", "SHORT"]):
            try:
                self._request("POST", "/openApi/swap/v2/trade/leverage",
                              {"symbol": symbol, "side": s, "leverage": int(leverage)})
            except Exception:
                pass

    # ── Passage d'ordre avec TP/SL attaches ──────────────
    def open_market_with_tpsl(self, symbol, direction, quantity, tp_price, sl_price,
                              position_side="BOTH"):
        """
        direction : 'LONG' ou 'SHORT'
        TP/SL sont attaches a l'ordre : BingX gere la sortie serveur-side,
        meme bot eteint.
        """
        side = "BUY" if direction == "LONG" else "SELL"
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": quantity,
            "takeProfit": json.dumps({
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": self.round_price(symbol, tp_price),
                "workingType": "MARK_PRICE",
            }),
            "stopLoss": json.dumps({
                "type": "STOP_MARKET",
                "stopPrice": self.round_price(symbol, sl_price),
                "workingType": "MARK_PRICE",
            }),
        }
        return self._request("POST", "/openApi/swap/v2/trade/order", params)

    def close_all(self, symbol):
        return self._request("DELETE", "/openApi/swap/v2/trade/allOpenOrders",
                             {"symbol": symbol})


def math_floor(value, precision):
    f = 10 ** precision
    return int(float(value) * f) / f


def to_bingx_symbol(pair):
    """BTCUSDT (format Binance) -> BTC-USDT (format BingX)."""
    if pair.endswith("USDT"):
        return pair[:-4] + "-USDT"
    return pair
