# -*- coding: utf-8 -*-
"""
telegram.py — Notifications et commandes, reservees au proprietaire.

Seuls les messages venant du chat prive du proprietaire (TELEGRAM_CHAT_ID) sont
pris en compte. Tout autre expediteur est ignore : impossible pour un inconnu
de reactiver le bot ou de lever un garde-fou.
"""
import json
import urllib.parse
import urllib.request


class Telegram:
    def __init__(self, token, chat_id):
        self.token, self.chat = token, str(chat_id)

    def _api(self, method, payload):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def send(self, text):
        if not self.token or not self.chat:
            return False
        try:
            self._api("sendMessage", {"chat_id": self.chat, "text": text,
                                      "parse_mode": "HTML", "disable_web_page_preview": True})
            return True
        except Exception:
            return False

    def commands(self, offset):
        """Renvoie (nouvel_offset, [commandes du proprietaire])."""
        if not self.token:
            return offset, []
        try:
            res = self._api("getUpdates", {"offset": offset + 1, "timeout": 0,
                                           "allowed_updates": ["message"]})
        except Exception:
            return offset, []
        cmds = []
        for u in res.get("result", []):
            offset = max(offset, u["update_id"])
            m = u.get("message") or {}
            chat = str((m.get("chat") or {}).get("id", ""))
            sender = str((m.get("from") or {}).get("id", ""))
            if chat != self.chat or sender != self.chat:
                continue  # pas le proprietaire : ignore
            txt = (m.get("text") or "").strip().split()
            if txt and txt[0].startswith("/"):
                cmds.append(txt[0].split("@")[0].lower())
        return offset, cmds
