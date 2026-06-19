"""
HomeOS — modules/nextdns_client.py
Client NextDNS API : stats DNS 24 h, pays de destination du trafic.
Singleton `nextdns_client` partagé par les callbacks.

Endpoints utilisés (NextDNS REST API v1) :
  GET /profiles/{id}/analytics/status?from=-24h
  GET /profiles/{id}/analytics/destinations?type=countries&from=-24h
"""

import logging
import time

import requests
import config

logger = logging.getLogger(__name__)


class NextDNSClient:
    """
    Client léger pour l'API REST NextDNS v1.

    Implémente un cache en mémoire avec TTL de 60 s pour éviter de surcharger
    l'API lors des rafraîchissements fréquents du dashboard.
    Si NEXTDNS_API_KEY ou NEXTDNS_PROFILE_ID sont vides dans config.py,
    toutes les méthodes retournent None / [] sans effectuer de requête.
    """
    BASE = "https://api.nextdns.io"
    TTL  = 60  # secondes de cache en mémoire

    def __init__(self):
        self._cache: dict = {}

    def configured(self) -> bool:
        """Retourne True si les clés NextDNS sont renseignées dans config.py."""
        return bool(config.NEXTDNS_API_KEY and config.NEXTDNS_PROFILE_ID)

    def _get(self, path: str, params: dict | None = None):
        if not self.configured():
            return None
        cache_key = (path, str(sorted((params or {}).items())))
        entry = self._cache.get(cache_key)
        if entry and time.time() - entry["ts"] < self.TTL:
            return entry["data"]
        try:
            r = requests.get(
                f"{self.BASE}/profiles/{config.NEXTDNS_PROFILE_ID}/{path}",
                params=params,
                headers={"X-Api-Key": config.NEXTDNS_API_KEY},
                timeout=8,
            )
            r.raise_for_status()
            data = r.json()
            self._cache[cache_key] = {"data": data, "ts": time.time()}
            return data
        except requests.HTTPError as e:
            logger.error("NextDNS HTTP %s %s : %s", e.response.status_code, path, e)
            return None
        except Exception as e:
            logger.error("NextDNS %s : %s", path, e)
            return None

    # ── Statistiques globales ─────────────────────────────────────────────────

    def get_status(self) -> dict | None:
        """Retourne {total, blocked, rate} pour les dernières 24 h.

        L'API retourne une liste d'objets {"status": str, "queries": int}.
        On somme toutes les entrées pour le total et on isole "blocked".
        """
        data = self._get("analytics/status", {"from": "-24h"})
        if not data:
            return None
        rows    = data.get("data", [])
        total   = sum(r.get("queries", 0) for r in rows)
        blocked = sum(r.get("queries", 0) for r in rows if r.get("status") == "blocked")
        rate    = round(blocked / total * 100, 1) if total > 0 else 0.0
        return {"total": total, "blocked": blocked, "rate": rate}

    # ── Pays de destination du trafic ─────────────────────────────────────────

    def get_traffic_countries(self) -> list:
        """
        Retourne [{country: 'US', queries: N}, …] triés par volume décroissant.
        Champ 'code' dans la réponse NextDNS : code ISO-2 du pays de destination.
        """
        data = self._get(
            "analytics/destinations",
            {"from": "-24h", "type": "countries", "limit": 200},
        )
        if not data:
            return []
        return [
            {"country": d.get("code", ""), "queries": d.get("queries", 0)}
            for d in data.get("data", [])
            if d.get("code")
        ]


nextdns_client = NextDNSClient()
