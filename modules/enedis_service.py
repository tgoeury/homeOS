"""
HomeOS — modules/enedis_service.py
Consommation électrique Enedis via l'API conso.boris.sh.

Endpoint : GET https://conso.boris.sh/api/daily_consumption
           ?prm=<PDL>&start=YYYY-MM-DD&end=YYYY-MM-DD  (end exclusif)
Auth     : Authorization: Bearer <token>
Réponse  : {"reading_type": {"unit": "Wh", ...},
            "interval_reading": [{"date": "YYYY-MM-DD", "value": "12873"}, ...]}

Stockage : data/cache.db — table history, name="enedis_daily"
           ts = datetime(année, mois, jour, 12, 0).timestamp()  (midi local)
           value = kWh (float→str), unit = "kWh", source = "enedis"

Stratégie de fetch :
  - Planifié chaque jour à FETCH_HOUR:FETCH_MINUTE (8h42 par défaut).
  - Uniquement les jours manquants : last_date+1 → veille incluse.
  - En cas d'échec API, retry automatique toutes les RETRY_INTERVAL secondes (60 min).
  - Requêtes paginées par tranche d'1 an (limite Enedis).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta

import requests

import config as CFG
from modules.data_cache import data_cache

logger = logging.getLogger(__name__)

_API_BASE       = "https://conso.boris.sh/api"
_FETCH_HOUR     = 8
_FETCH_MINUTE   = 42
_RETRY_INTERVAL = 60 * 60      # secondes entre deux retries en cas d'échec
_HISTORY_NAME   = "enedis_daily"


def _default_history_start() -> date:
    today = date.today()
    return today.replace(year=today.year - 3)


def _date_to_ts(d: date) -> float:
    """Convertit une date en timestamp Unix (midi local, arbitraire mais stable)."""
    return datetime(d.year, d.month, d.day, 12, 0).timestamp()


def _ts_to_date(ts: float) -> date:
    return datetime.fromtimestamp(ts).date()


class EnedisService:
    """
    Service singleton pour la récupération et la lecture de la consommation Enedis.

    Un thread daemon planifie le fetch quotidien à _FETCH_HOUR:_FETCH_MINUTE.
    En cas d'échec, il réessaie toutes les _RETRY_INTERVAL secondes jusqu'au succès.
    Toutes les données sont stockées dans data/cache.db.
    """

    def __init__(self) -> None:
        self._last_error:     str | None = None
        self._morning_failed: bool       = False
        self._hist_cache:     list[dict] | None = None
        self._hist_lock       = threading.Lock()

        self._thread = threading.Thread(
            target=self._schedule_loop,
            daemon=True,
            name="enedis-scheduler",
        )
        self._thread.start()

    # ── État ──────────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(
            getattr(CFG, "ENEDIS_TOKEN", "").strip()
            and getattr(CFG, "ENEDIS_PRM", "").strip()
        )

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def morning_failed(self) -> bool:
        return self._morning_failed

    # ── Lecture depuis SQLite ─────────────────────────────────────────────────

    def read_history(self) -> list[dict]:
        """
        Retourne [{date, kwh}, …] triés ASC. Mémoïsé en mémoire (invalidé par
        _store_rows, seul point d'écriture de la série) — les données ne
        changent qu'une fois par jour, inutile de rescanner/re-parser SQLite
        à chaque appel. Ne pas muter la liste retournée.
        """
        with self._hist_lock:
            if self._hist_cache is None:
                self._hist_cache = self._read_history_from_db()
            return self._hist_cache

    def _read_history_from_db(self) -> list[dict]:
        rows = data_cache.read_history(_HISTORY_NAME)
        result = []
        seen: set[date] = set()
        for r in rows:
            try:
                d   = _ts_to_date(r["ts"])
                kwh = float(r["value"])
                if d not in seen:          # déduplique si la même date existe deux fois
                    seen.add(d)
                    result.append({"date": d, "kwh": kwh})
            except (ValueError, TypeError):
                continue
        result.sort(key=lambda x: x["date"])
        return result

    # ── Planificateur ─────────────────────────────────────────────────────────

    def _schedule_loop(self) -> None:
        if not self.is_configured():
            logger.info("EnedisService: non configuré — scheduler inactif")
            return

        # Fetch immédiat au démarrage si données manquantes
        self._fetch_missing()

        while True:
            self._sleep_until_fetch_time()
            success = self._fetch_missing()
            if not success:
                self._morning_failed = True
                while not success:
                    logger.warning(
                        "EnedisService: fetch échoué (%s), retry dans %d min",
                        self._last_error, _RETRY_INTERVAL // 60,
                    )
                    time.sleep(_RETRY_INTERVAL)
                    success = self._fetch_missing()
                self._morning_failed = False

    def _sleep_until_fetch_time(self) -> None:
        now    = datetime.now()
        target = now.replace(hour=_FETCH_HOUR, minute=_FETCH_MINUTE,
                             second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info(
            "EnedisService: prochain fetch %02d:%02d le %s (dans %.0fh%02.0fm)",
            _FETCH_HOUR, _FETCH_MINUTE,
            target.strftime("%d/%m"),
            wait // 3600, (wait % 3600) // 60,
        )
        time.sleep(wait)

    # ── Fetch ─────────────────────────────────────────────────────────────────

    def _fetch_missing(self) -> bool:
        """
        Récupère les jours manquants de last_date+1 jusqu'à la veille (incluse).
        Retourne True si succès (y compris si déjà à jour), False si erreur API.
        """
        yesterday = date.today() - timedelta(days=1)
        rows      = self.read_history()
        start     = (rows[-1]["date"] + timedelta(days=1)) if rows else _default_history_start()

        if start > yesterday:
            logger.info("EnedisService: données déjà à jour (dernière entrée : %s)", yesterday)
            self._last_error = None
            return True

        end = yesterday + timedelta(days=1)   # end exclusif dans l'API

        new_rows: list[tuple] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(
                date(chunk_start.year + 1, chunk_start.month, chunk_start.day),
                end,
            )
            fetched = self._fetch_chunk(chunk_start, chunk_end)
            if fetched is None:
                return False
            new_rows.extend(fetched)
            chunk_start = chunk_end

        if new_rows:
            self._store_rows(new_rows)
            logger.info(
                "EnedisService: %d jour(s) enregistré(s) (%s → %s)",
                len(new_rows), new_rows[0][0], new_rows[-1][0],
            )

        self._last_error = None
        return True

    def _store_rows(self, rows: list[tuple]) -> None:
        """Enregistre (date_str, kwh) dans data/cache.db en une seule transaction."""
        db_rows = []
        for date_str, kwh in rows:
            try:
                d  = date.fromisoformat(date_str)
                ts = _date_to_ts(d)
                db_rows.append((_HISTORY_NAME, ts, "enedis", str(kwh), "kWh"))
            except Exception as e:
                logger.error("EnedisService._store_rows: %s", e)
        if db_rows:
            data_cache.log_raw_many(db_rows)
        with self._hist_lock:
            self._hist_cache = None

    def _fetch_chunk(self, start: date, end: date) -> list[tuple] | None:
        try:
            resp = requests.get(
                f"{_API_BASE}/daily_consumption",
                params={
                    "prm":   CFG.ENEDIS_PRM,
                    "start": start.isoformat(),
                    "end":   end.isoformat(),
                },
                headers={"Authorization": f"Bearer {CFG.ENEDIS_TOKEN}"},
                timeout=30,
            )
        except requests.RequestException as exc:
            self._last_error = f"Réseau : {exc}"
            logger.error("EnedisService._fetch_chunk: %s", exc)
            return None

        if resp.status_code == 404:
            return []
        if resp.status_code == 400:
            body = resp.json() if resp.content else {}
            desc = body.get("error", {}).get("error_description", "")
            if "start date" in desc.lower() or "history deadline" in desc.lower():
                retried_start = date(start.year + 1, start.month, start.day)
                if retried_start < end:
                    logger.warning("EnedisService: start %s trop ancien, retry à %s",
                                   start, retried_start)
                    return self._fetch_chunk(retried_start, end)
            self._last_error = f"HTTP 400 : {desc or resp.text[:120]}"
            logger.error("EnedisService: %s", self._last_error)
            return None
        if resp.status_code != 200:
            self._last_error = f"HTTP {resp.status_code}"
            logger.error("EnedisService: HTTP %s — %s", resp.status_code, resp.text[:200])
            return None

        readings = resp.json().get("interval_reading", [])
        return [
            (r["date"], round(float(r["value"]) / 1000, 3))
            for r in readings
            if "date" in r and "value" in r
        ]

enedis_service = EnedisService()
