"""
HomeOS — modules/chatbot_engine.py
═══════════════════════════════════════════════════════════════
Moteur de communication chatbot — Synology Chat via webhooks.

Flux de données :
  [Dashboard]         → send_message()         → POST → [Synology Chat webhook entrant]
  [Synology Chat bot] → POST /webhook/chat      → add_incoming_message() → store → display

Architecture prévue pour migration en daemon autonome :
  1. Remplacer le store in-memory par une queue persistante (Redis / SQLite)
  2. Ajouter start() / stop() pour une boucle asyncio ou thread de polling
  3. Exposer une légère API REST/socket pour découpler le daemon du serveur Dash
═══════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import TypedDict

import requests
import urllib3

import config as CFG

log = logging.getLogger(__name__)

# Désactive les warnings SSL pour les certificats auto-signés NAS Synology
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── Types ──────────────────────────────────────────────────────────────────────

class Message(TypedDict):
    role: str    # "user" | "bot" | "system"
    text: str
    ts:   float  # epoch seconds


# ── Store thread-safe ──────────────────────────────────────────────────────────

_lock:         threading.Lock = threading.Lock()
_messages:     list[Message]  = []
_last_ping_ok: bool           = False


# ── Interface publique ─────────────────────────────────────────────────────────

def get_messages() -> list[Message]:
    """Retourne une copie thread-safe de la liste des messages."""
    with _lock:
        return list(_messages)


def add_incoming_message(text: str, username: str = "Bot") -> None:
    """
    Ajoute un message reçu depuis Synology Chat.
    Appelé par le webhook Flask (/webhook/chat) dans app.py.
    """
    _append({"role": "bot", "text": text, "ts": time.time()})
    log.info("[chatbot] ← %s: %r", username, text)


def send_message(text: str) -> bool:
    """
    Envoie un message vers Synology Chat via le webhook entrant.
    Stocke également le message localement (rôle 'user').
    Retourne True si l'envoi HTTP a réussi (HTTP 200).
    """
    global _last_ping_ok
    _append({"role": "user", "text": text, "ts": time.time()})
    try:
        resp = requests.post(
            CFG.SYNOLOGY_CHAT_WEBHOOK_URL,
            data={"payload": f'{{"text": "{_escape(text)}"}}'},
            timeout=5,
            verify=False,   # Synology NAS avec certificat auto-signé
        )
        _last_ping_ok = resp.status_code == 200
        if not _last_ping_ok:
            log.warning("[chatbot] Synology HTTP %s — %s", resp.status_code, resp.text[:200])
    except requests.RequestException as exc:
        _last_ping_ok = False
        log.error("[chatbot] Erreur envoi vers Synology : %s", exc)
    return _last_ping_ok


def clear_messages() -> None:
    """Vide le store de messages (affiché et en mémoire)."""
    with _lock:
        _messages.clear()
    log.debug("[chatbot] Messages effacés.")


def get_connection_status() -> bool:
    """
    Retourne True si le dernier envoi vers Synology a réussi (HTTP 200).
    False au démarrage (aucun envoi effectué).
    """
    return _last_ping_ok


# ── Helpers privés ─────────────────────────────────────────────────────────────

def _append(msg: Message) -> None:
    with _lock:
        _messages.append(msg)


def _escape(text: str) -> str:
    """Échappement minimal pour inclusion dans JSON/payload Synology."""
    return (
        text
        .replace("\\", "\\\\")
        .replace('"',  '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def fmt_time(ts: float) -> str:
    """Formate un timestamp epoch en HH:MM:SS."""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
