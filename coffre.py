# -*- coding: utf-8 -*-
"""
coffre.py — Etat du bot chiffre et authentifie.

Le depot est public : l'etat (capital, positions, historique) ne doit pas etre lisible.
Cle derivee du secret API BingX (deja stocke dans les GitHub Secrets) : aucun nouveau
secret a creer. Chiffrement : flux HMAC-SHA256 en mode compteur + tag HMAC-SHA256
(chiffrer-puis-authentifier). Toute modification du fichier est detectee.

Bibliotheque standard uniquement.
"""
import base64
import hashlib
import hmac
import json
import os

VERSION = b"scalpradar-etat-v2"


def _keys(secret):
    master = hmac.new(secret.encode(), VERSION, hashlib.sha256).digest()
    enc = hmac.new(master, b"chiffrement", hashlib.sha256).digest()
    mac = hmac.new(master, b"authentification", hashlib.sha256).digest()
    return enc, mac


def _stream(key, nonce, n):
    out, ctr = bytearray(), 0
    while len(out) < n:
        out += hmac.new(key, nonce + ctr.to_bytes(8, "big"), hashlib.sha256).digest()
        ctr += 1
    return bytes(out[:n])


def chiffrer(obj, secret):
    enc, mac = _keys(secret)
    pt = json.dumps(obj, separators=(",", ":")).encode()
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(pt, _stream(enc, nonce, len(pt))))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + ct).decode()


def dechiffrer(blob, secret):
    enc, mac = _keys(secret)
    raw = base64.b64decode(blob)
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(mac, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("etat altere ou cle differente")
    pt = bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct))))
    return json.loads(pt)
