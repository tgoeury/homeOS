"""
HomeOS — modules/synology_client.py
Client REST pour l'API DSM Synology — stockage et santé des disques.

Endpoints utilisés (DSM 6 / 7) :
  POST /webapi/auth.cgi
       api=SYNO.API.Auth&version=3&method=login&account=…&passwd=…
       → {"data": {"sid": "…"}, "success": true}

  GET  /webapi/entry.cgi
       api=SYNO.Core.Storage.Volume&version=1&method=list&_sid=…
       → {"data": {"volumes": [{volume_path, size_total_byte, size_used_byte, status}]}}

  GET  /webapi/entry.cgi
       api=SYNO.Core.Storage.Disk&version=1&method=list&_sid=…
       → {"data": {"disks": [{name, model, temp, status, diskno}]}}

  GET  /webapi/auth.cgi
       api=SYNO.API.Auth&version=1&method=logout&_sid=…

Stratégie :
  - Cache en mémoire TTL = 1 h (CFG.SYNOLOGY_NAS_TTL si défini, sinon 3600 s).
  - fetch() retourne le cache si frais ; appelle l'API sinon.
  - is_configured() = True si NAS_USER et NAS_PASSWORD sont renseignés.
  - SSL non vérifié (certificat auto-signé Synology).
"""
from __future__ import annotations

import logging
import time

import requests
import urllib3

import config as CFG

logger = logging.getLogger(__name__)

# Désactive les warnings SSL pour les certificats auto-signés NAS Synology
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NAS_STALE_SECS = 6 * 3600   # délai au-delà duquel la data est considérée obsolète


def _fmt_bytes(n: int) -> str:
    """Convertit un nombre d'octets en chaîne lisible (TB, GB, MB)."""
    if n >= 1e12:
        return f"{n / 1e12:.1f} TB"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    return f"{n} B"


class SynologyClient:
    """
    Client léger pour l'API REST DSM Synology.

    Chaque appel à fetch() établit une session (login), interroge les endpoints
    volumes et disques, puis ferme la session (logout). Le résultat est mis en
    cache en mémoire pour éviter de surcharger le NAS.
    """

    def __init__(self) -> None:
        self._cache: dict | None = None
        self._cache_ts: float = 0.0
        ttl = getattr(CFG, "SYNOLOGY_NAS_TTL", 3600)
        self._ttl: int = int(ttl)

    # ── Configuration ──────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Retourne True si les credentials NAS sont renseignés dans config.py."""
        return bool(
            getattr(CFG, "SYNOLOGY_NAS_USER", "").strip()
            and getattr(CFG, "SYNOLOGY_NAS_PASSWORD", "").strip()
        )

    def _base_url(self) -> str:
        host = getattr(CFG, "SYNOLOGY_NAS_HOST", "192.168.1.20")
        port = getattr(CFG, "SYNOLOGY_NAS_PORT", 5001)
        return f"https://{host}:{port}/webapi"

    # ── Cache ─────────────────────────────────────────────────────────────────

    def is_stale(self, max_age: float = NAS_STALE_SECS) -> bool:
        """Retourne True si le cache est absent ou plus vieux que max_age secondes."""
        return self._cache is None or (time.time() - self._cache_ts) > max_age

    def cache_age(self) -> float | None:
        """Retourne l'âge en secondes du cache, ou None si aucun cache."""
        if self._cache is None:
            return None
        return time.time() - self._cache_ts

    # ── Session DSM ───────────────────────────────────────────────────────────

    def _login(self) -> str | None:
        """Ouvre une session DSM. Retourne le sid ou None en cas d'erreur."""
        try:
            resp = requests.post(
                f"{self._base_url()}/auth.cgi",
                data={
                    "api":     "SYNO.API.Auth",
                    "version": "3",
                    "method":  "login",
                    "account": CFG.SYNOLOGY_NAS_USER,
                    "passwd":  CFG.SYNOLOGY_NAS_PASSWORD,
                    "session": "HomeOS",
                    "format":  "sid",
                },
                timeout=10,
                verify=False,
            )
            body = resp.json()
            if not body.get("success"):
                logger.error("SynologyClient: login échoué — %s", body.get("error"))
                return None
            return body["data"]["sid"]
        except Exception as exc:
            logger.error("SynologyClient: login exception — %s", exc)
            return None

    def _logout(self, sid: str) -> None:
        """Ferme la session DSM (best-effort, erreurs ignorées)."""
        try:
            requests.get(
                f"{self._base_url()}/auth.cgi",
                params={
                    "api":     "SYNO.API.Auth",
                    "version": "1",
                    "method":  "logout",
                    "session": "HomeOS",
                    "_sid":    sid,
                },
                timeout=5,
                verify=False,
            )
        except Exception:
            pass

    # ── Endpoints données ─────────────────────────────────────────────────────

    def _get_volumes(self, sid: str) -> list[dict]:
        """
        Interroge SYNO.Core.Storage.Volume.
        Retourne [{path, total_bytes, used_bytes, free_bytes, used_pct, status}].
        """
        try:
            resp = requests.get(
                f"{self._base_url()}/entry.cgi",
                params={
                    "api":     "SYNO.Core.Storage.Volume",
                    "version": "1",
                    "method":  "list",
                    "_sid":    sid,
                },
                timeout=10,
                verify=False,
            )
            body = resp.json()
            if not body.get("success"):
                logger.warning("SynologyClient: volumes — %s", body.get("error"))
                return []
            volumes = []
            for v in body.get("data", {}).get("volumes", []):
                total = int(v.get("size_total_byte", 0))
                used  = int(v.get("size_used_byte", 0))
                free  = total - used
                pct   = round(used / total * 100) if total else 0
                volumes.append({
                    "path":        v.get("volume_path", "—"),
                    "total_bytes": total,
                    "used_bytes":  used,
                    "free_bytes":  free,
                    "used_pct":    pct,
                    "total_str":   _fmt_bytes(total),
                    "used_str":    _fmt_bytes(used),
                    "free_str":    _fmt_bytes(free),
                    "status":      v.get("status", "unknown"),
                    "fs_type":     v.get("fs_type", ""),
                })
            return volumes
        except Exception as exc:
            logger.error("SynologyClient: volumes exception — %s", exc)
            return []

    def _get_disks(self, sid: str) -> list[dict]:
        """
        Interroge SYNO.Core.Storage.Disk.
        Retourne [{name, model, temp, status}].
        """
        try:
            resp = requests.get(
                f"{self._base_url()}/entry.cgi",
                params={
                    "api":     "SYNO.Core.Storage.Disk",
                    "version": "1",
                    "method":  "list",
                    "_sid":    sid,
                },
                timeout=10,
                verify=False,
            )
            body = resp.json()
            if not body.get("success"):
                logger.warning("SynologyClient: disks — %s", body.get("error"))
                return []
            disks = []
            for d in body.get("data", {}).get("disks", []):
                disks.append({
                    "name":   d.get("diskno") or d.get("name", "—"),
                    "model":  d.get("model", "—"),
                    "temp":   d.get("temp"),        # °C ou None
                    "status": d.get("status", "unknown"),
                })
            return disks
        except Exception as exc:
            logger.error("SynologyClient: disks exception — %s", exc)
            return []

    # ── Interface publique ────────────────────────────────────────────────────

    def fetch(self, force: bool = False) -> dict | None:
        """
        Retourne {volumes, disks, fetched_at} depuis le cache si frais (< TTL).
        Force un appel API si force=True ou si le cache est périmé.
        Retourne None si non configuré ou si l'API échoue.
        """
        if not force and not self.is_stale(self._ttl):
            return self._cache

        if not self.is_configured():
            logger.debug("SynologyClient: non configuré — fetch ignoré")
            return self._cache

        sid = self._login()
        if sid is None:
            return self._cache   # retourne le vieux cache si disponible

        try:
            volumes = self._get_volumes(sid)
            disks   = self._get_disks(sid)
        finally:
            self._logout(sid)

        self._cache = {
            "volumes":    volumes,
            "disks":      disks,
            "fetched_at": time.time(),
        }
        self._cache_ts = time.time()
        logger.info(
            "SynologyClient: %d volume(s), %d disque(s) mis à jour",
            len(volumes), len(disks),
        )
        return self._cache


synology_client = SynologyClient()
