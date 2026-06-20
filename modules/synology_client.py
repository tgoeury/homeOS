"""
HomeOS — modules/synology_client.py
Client REST pour l'API DSM Synology — espace disque par dossier partagé.

Compte dédié non-admin (groupe "users" uniquement) : les endpoints
SYNO.Core.Storage.* (Storage Manager, santé SMART des disques) exigent un
compte membre du groupe "administrators" et renvoient 402 (Permission
denied) sinon. On reste volontairement sur File Station, accessible aux
comptes non-admin avec la seule permission applicative "File Station".
→ Conséquence : pas de santé SMART / température disque avec ce compte.
   Pour ça il faudrait soit passer le compte en administrators, soit créer
   un second compte admin dédié à cette unique lecture.

Endpoints utilisés (DSM 6 / 7) :
  POST /webapi/auth.cgi
       api=SYNO.API.Auth&version=3&method=login&account=…&passwd=…&session=FileStation
       → {"data": {"sid": "…"}, "success": true}

  GET  /webapi/entry.cgi
       api=SYNO.FileStation.List&version=2&method=list_share
       &additional=["volume_status"]&_sid=…
       → {"data": {"shares": [{path, name,
                                additional: {volume_status: {freespace, totalspace}}}]}}

  GET  /webapi/auth.cgi
       api=SYNO.API.Auth&version=1&method=logout&session=FileStation&_sid=…

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

# Nom de session DSM : doit correspondre à une application reconnue. File Station
# est accessible aux comptes non-admin via la permission applicative dédiée,
# contrairement à DSM Core / Storage Manager qui exige le groupe administrators.
SESSION_NAME = "FileStation"


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
    Client léger pour l'API REST DSM Synology (File Station, compte non-admin).

    Chaque appel à fetch() établit une session (login), interroge la liste des
    dossiers partagés avec leur espace volume associé, puis ferme la session
    (logout). Le résultat est mis en cache en mémoire pour éviter de surcharger
    le NAS.
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
        """Ouvre une session DSM (File Station). Retourne le sid ou None en cas d'erreur."""
        try:
            resp = requests.post(
                f"{self._base_url()}/auth.cgi",
                data={
                    "api":     "SYNO.API.Auth",
                    "version": "3",
                    "method":  "login",
                    "account": CFG.SYNOLOGY_NAS_USER,
                    "passwd":  CFG.SYNOLOGY_NAS_PASSWORD,
                    "session": SESSION_NAME,
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
                    "session": SESSION_NAME,
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
        Interroge SYNO.FileStation.List (method=list_share, additional=volume_status).
        Accessible aux comptes non-admin avec la permission File Station.

        Note : plusieurs dossiers partagés peuvent pointer vers le même volume
        physique (ex. "homes" et "photo" sur volume1) ; on déduplique sur le
        chemin de volume sous-jacent pour ne pas compter le même espace deux fois.

        Retourne [{path, total_bytes, used_bytes, free_bytes, used_pct}].
        """
        try:
            resp = requests.get(
                f"{self._base_url()}/entry.cgi",
                params={
                    "api":        "SYNO.FileStation.List",
                    "version":    "2",
                    "method":     "list_share",
                    "additional": '["volume_status"]',
                    "_sid":       sid,
                },
                timeout=10,
                verify=False,
            )
            body = resp.json()
            if not body.get("success"):
                logger.warning("SynologyClient: list_share — %s", body.get("error"))
                return []

            seen_volumes: set[str] = set()
            volumes = []
            for share in body.get("data", {}).get("shares", []):
                vol_status = share.get("additional", {}).get("volume_status", {})
                total = int(vol_status.get("totalspace", 0))
                free  = int(vol_status.get("freespace", 0))
                if total == 0:
                    continue

                # Identifiant de volume physique = couple (total, free) à l'instant T,
                # suffisant pour dédupliquer les partages d'un même volume dans un seul cycle fetch().
                vol_key = f"{total}:{free}"
                if vol_key in seen_volumes:
                    continue
                seen_volumes.add(vol_key)

                used = total - free
                pct  = round(used / total * 100) if total else 0
                volumes.append({
                    "path":        share.get("path", "—"),
                    "total_bytes": total,
                    "used_bytes":  used,
                    "free_bytes":  free,
                    "used_pct":    pct,
                    "total_str":   _fmt_bytes(total),
                    "used_str":    _fmt_bytes(used),
                    "free_str":    _fmt_bytes(free),
                })
            return volumes
        except Exception as exc:
            logger.error("SynologyClient: volumes exception — %s", exc)
            return []

    def _get_system_info(self, sid: str) -> dict:
        """
        Interroge SYNO.DSM.Info (accessible aux comptes non-admin).
        Retourne {model, ram_mb, temperature, temperature_warn, uptime_s, version}.
        """
        try:
            resp = requests.get(
                f"{self._base_url()}/entry.cgi",
                params={"api": "SYNO.DSM.Info", "version": "2", "method": "getinfo", "_sid": sid},
                timeout=10,
                verify=False,
            )
            body = resp.json()
            if not body.get("success"):
                logger.warning("SynologyClient: DSM.Info — %s", body.get("error"))
                return {}
            d = body["data"]
            return {
                "model":            d.get("model", "—"),
                "ram_mb":           d.get("ram", 0),
                "temperature":      d.get("temperature"),
                "temperature_warn": d.get("temperature_warn", False),
                "uptime_s":         d.get("uptime", 0),
                "version":          d.get("version_string", "—"),
            }
        except Exception as exc:
            logger.error("SynologyClient: DSM.Info exception — %s", exc)
            return {}

    # ── Interface publique ────────────────────────────────────────────────────

    def fetch(self, force: bool = False) -> dict | None:
        """
        Retourne {volumes, system, fetched_at} depuis le cache si frais (< TTL).
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
            system  = self._get_system_info(sid)
        finally:
            self._logout(sid)

        self._cache = {
            "volumes":    volumes,
            "system":     system,
            "fetched_at": time.time(),
        }
        self._cache_ts = time.time()
        logger.info(
            "SynologyClient: %d volume(s), temp=%s°C, uptime=%ds",
            len(volumes), system.get("temperature"), system.get("uptime_s"),
        )
        return self._cache


synology_client = SynologyClient()
