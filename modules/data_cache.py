"""
HomeOS — modules/data_cache.py
Cache persistant SQLite3 — source unique de vérité pour HomeOS.

Deux tables :
  cache   — dernière valeur connue par clé (key/value/unit/source/updated_at)
  history — séries temporelles (name/ts/source/value/unit) avec write-on-change

Clés cache :
  weather.snapshot           — données météo complètes (sérialisées)
  sensor.<pièce>.<mesure>   — capteurs (temperature, humidity, luminosity)
  network.devices            — liste des appareils LAN [{ip, name}, …]
  network.dns.blocked/rate/total/countries
  log.last.<name>            — dernière signature (source,value,unit,bucket) pour write-on-change
                               le bucket change toutes les LOG_HEARTBEAT_INTERVAL secondes,
                               forçant un enregistrement périodique même si la valeur est stable

Séries history (name) :
  sensor_<room>_temperature / humidity / luminosity
  plant_<id>_soil_moisture
  window_<id>_contact
  weather_temperature / humidity / wind_speed / pressure / sky
  network_devices_count / dns_blocked / dns_rate
  enedis_daily
"""

import csv
import json
import logging
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "cache.db"

# Ancienneté maximale autorisée par catégorie de clé (secondes)
LOG_HEARTBEAT_INTERVAL: float = 1800  # 30 min — force un point en DB même si la valeur est stable

STALE_THRESHOLDS: dict[str, float] = {
    "weather": 12 * 3600,   # 12 h
    "sensor":   1 * 3600,   #  1 h
    "network":  1 * 3600,   #  1 h
}


class DataCache:
    """
    Cache persistant basé sur SQLite3 — mémoire à court terme du système.

    Stocke des valeurs JSON horodatées sous des clés hiérarchiques
    (ex. "sensor.salon.temperature"). Fournit une méthode d'inspection
    de fraîcheur par catégorie pour alimenter les badges de statut.
    Thread-safe via un verrou interne.
    """

    def __init__(self, path: Path = _DB_PATH):
        self._path = path
        self._lock = threading.Lock()
        self._cx: sqlite3.Connection | None = None
        self._init_db()
        self._migrate_legacy_csvs()

    def _connect(self) -> sqlite3.Connection:
        # Connexion SQLite persistante, réutilisée pour tous les appels (round 4) :
        # ouvrir une connexion par requête est coûteux sur la microSD d'un RPi 3
        # (~24 ouvertures/5 s rien que pour update_sensors). Tous les accès passent
        # par self._lock, donc une connexion unique partagée est sûre ;
        # check_same_thread=False autorise son usage depuis le thread MQTT et Dash.
        # _connect() est toujours appelé sous self._lock → création paresseuse sûre.
        if self._cx is None:
            self._cx = sqlite3.connect(str(self._path), check_same_thread=False)
        return self._cx

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    unit       TEXT DEFAULT '',
                    source     TEXT DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    name   TEXT NOT NULL,
                    ts     REAL NOT NULL,
                    source TEXT DEFAULT '',
                    value  TEXT NOT NULL,
                    unit   TEXT DEFAULT '',
                    PRIMARY KEY (name, ts)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_name_ts ON history(name, ts)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ytdlp_jobs (
                    id         TEXT PRIMARY KEY,
                    ts         TEXT NOT NULL,
                    url        TEXT NOT NULL,
                    params     TEXT NOT NULL DEFAULT '{}',
                    folder     TEXT NOT NULL DEFAULT '',
                    files      TEXT NOT NULL DEFAULT '[]',
                    status     TEXT NOT NULL DEFAULT 'running',
                    created_at REAL NOT NULL
                )
            """)

    def write(self, key: str, value, unit: str = "", source: str = "") -> None:
        """Insère ou met à jour une entrée dans le cache."""
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO cache (key, value, unit, source, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET
                            value      = excluded.value,
                            unit       = excluded.unit,
                            source     = excluded.source,
                            updated_at = excluded.updated_at
                        """,
                        (key, payload, unit, source, time.time()),
                    )
            except Exception as e:
                logger.error("DataCache.write(%s): %s", key, e)

    def read(self, key: str) -> dict | None:
        """
        Retourne {"value": …, "unit": …, "source": …, "updated_at": float}
        ou None si la clé est absente du cache.
        La valeur est désérialisée depuis JSON.
        """
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT value, unit, source, updated_at FROM cache WHERE key = ?",
                    (key,),
                ).fetchone()
            if row is None:
                return None
            return {
                "value":      json.loads(row[0]),
                "unit":       row[1],
                "source":     row[2],
                "updated_at": row[3],
            }
        except Exception as e:
            logger.error("DataCache.read(%s): %s", key, e)
            return None

    # ── Séries temporelles ────────────────────────────────────────────────────

    def log_raw(self, name: str, ts: float, value, unit: str, source: str) -> None:
        """Insère une ligne dans history à un timestamp précis, sans déduplication."""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO history (name,ts,source,value,unit) VALUES (?,?,?,?,?)",
                        (name, ts, source, str(value), unit),
                    )
            except Exception as e:
                logger.error("DataCache.log_raw(%s): %s", name, e)

    def update_history_ts(self, name: str, old_ts: float, new_ts: float) -> None:
        """Met à jour le timestamp d'une ligne history (fenêtre glissante capteurs)."""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE OR IGNORE history SET ts=? WHERE name=? AND ts=?",
                        (new_ts, name, old_ts),
                    )
            except Exception as e:
                logger.error("DataCache.update_history_ts(%s): %s", name, e)

    def log(self, name: str, value, unit: str, source: str,
            force: bool = False) -> None:
        """
        Enregistre une entrée dans la table history avec write-on-change + heartbeat.

        N'écrit que si (source, value, unit, bucket) diffère de la dernière entrée connue,
        où bucket = int(now // LOG_HEARTBEAT_INTERVAL). Cela garantit un point en base
        toutes les 30 min même si la valeur ne change pas, rendant les graphes continus.
        La signature est mémorisée dans la table cache (clé log.last.<name>).
        force=True : bypass write-on-change, toujours écrire (à utiliser à la source MQTT).
        """
        now     = time.time()
        bucket  = int(now // LOG_HEARTBEAT_INTERVAL)
        sig_new = (str(source), str(value), str(unit), bucket)
        sig_key = f"log.last.{name}"
        with self._lock:
            try:
                with self._connect() as conn:
                    if not force:
                        row = conn.execute(
                            "SELECT value FROM cache WHERE key=?", (sig_key,)
                        ).fetchone()
                        if row and tuple(json.loads(row[0])) == sig_new:
                            has_data = conn.execute(
                                "SELECT 1 FROM history WHERE name=? LIMIT 1", (name,)
                            ).fetchone()
                            if has_data:
                                return
                    conn.execute(
                        "INSERT OR REPLACE INTO history (name,ts,source,value,unit) "
                        "VALUES (?,?,?,?,?)",
                        (name, now, source, str(value), unit),
                    )
                    conn.execute(
                        "INSERT INTO cache (key,value,unit,source,updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                        "unit=excluded.unit, source=excluded.source, updated_at=excluded.updated_at",
                        (sig_key, json.dumps(list(sig_new)), "", "datalogger", now),
                    )
            except Exception as e:
                logger.error("DataCache.log(%s): %s", name, e)

    def read_history(
        self,
        name: str,
        since_ts: float | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Retourne [{ts, source, value, unit}, …] trié ASC pour la série <name>.
        since_ts : filtre les entrées antérieures à ce timestamp Unix.
        limit    : nombre maximum de lignes retournées (les plus récentes si limite).
        """
        try:
            query  = "SELECT ts, source, value, unit FROM history WHERE name=?"
            params: list = [name]
            if since_ts is not None:
                query  += " AND ts >= ?"
                params.append(since_ts)
            query += " ORDER BY ts ASC"
            if limit is not None:
                query += f" LIMIT {int(limit)}"
            with self._lock, self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
            return [{"ts": r[0], "source": r[1], "value": r[2], "unit": r[3]}
                    for r in rows]
        except Exception as e:
            logger.error("DataCache.read_history(%s): %s", name, e)
            return []

    # ── Jobs yt-dlp ───────────────────────────────────────────────────────────

    def log_ytdlp_job(self, job_id: str, ts: str, url: str,
                      params: dict, folder: str) -> None:
        """Enregistre un nouveau job yt-dlp en base."""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO ytdlp_jobs "
                        "(id, ts, url, params, folder, status, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 'running', ?)",
                        (job_id, ts, url, json.dumps(params), folder, time.time()),
                    )
            except Exception as e:
                logger.error("DataCache.log_ytdlp_job: %s", e)

    def update_ytdlp_job(self, job_id: str, status: str,
                         files: list | None = None) -> None:
        """Met à jour le statut et la liste de fichiers d'un job yt-dlp."""
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE ytdlp_jobs SET status=?, files=? WHERE id=?",
                        (status, json.dumps(files or []), job_id),
                    )
            except Exception as e:
                logger.error("DataCache.update_ytdlp_job: %s", e)

    def get_ytdlp_jobs(self, limit: int = 20) -> list[dict]:
        """Retourne les jobs yt-dlp récents, du plus récent au plus ancien."""
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, ts, url, params, folder, files, status, created_at "
                    "FROM ytdlp_jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [
                {
                    "id":         r[0],
                    "ts":         r[1],
                    "url":        r[2],
                    "params":     json.loads(r[3]),
                    "folder":     r[4],
                    "files":      json.loads(r[5]),
                    "status":     r[6],
                    "created_at": r[7],
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("DataCache.get_ytdlp_jobs: %s", e)
            return []

    # ── Migration CSV → SQLite (one-shot au premier démarrage) ────────────────

    def _migrate_legacy_csvs(self) -> None:
        """
        Importe tous les CSV de data/history/ dans la table history,
        puis renomme les fichiers en .csv.migrated pour ne pas les re-traiter.

        Format CSV attendu : timestamp,source,value,unit  (ISO 8601 → ts Unix)
        Exception : enedis_daily_consumption.csv  → timestamp converti en midi local.
        """
        history_dir = self._path.parent.parent / "data" / "history"
        if not history_dir.exists():
            return

        csv_files = sorted(p for p in history_dir.glob("*.csv")
                           if not p.name.endswith(".migrated"))
        if not csv_files:
            return

        total = 0
        with self._lock, self._connect() as conn:
            for path in csv_files:
                try:
                    with open(path, newline="", encoding="utf-8") as f:
                        rows = list(csv.DictReader(f))
                except Exception as e:
                    logger.error("DataCache._migrate_legacy_csvs: lecture %s — %s", path.name, e)
                    continue

                # Nom de série : stem du fichier, sauf pour Enedis
                if path.stem == "enedis_daily_consumption":
                    series_name = "enedis_daily"
                else:
                    series_name = path.stem

                imported = 0
                for row in rows:
                    try:
                        raw_ts = row.get("timestamp", "")
                        if series_name == "enedis_daily":
                            d  = date.fromisoformat(raw_ts[:10])
                            ts = datetime(d.year, d.month, d.day, 12, 0).timestamp()
                        else:
                            ts = datetime.fromisoformat(raw_ts).timestamp()
                        conn.execute(
                            "INSERT OR IGNORE INTO history (name,ts,source,value,unit) "
                            "VALUES (?,?,?,?,?)",
                            (series_name, ts,
                             row.get("source", ""), row.get("value", ""), row.get("unit", "")),
                        )
                        imported += 1
                    except Exception:
                        continue

                path.rename(path.with_suffix(".csv.migrated"))
                total += imported
                logger.info("DataCache: %s — %d lignes importées", series_name, imported)

        logger.info("DataCache: migration terminée — %d lignes au total", total)

    # ── Fraîcheur du cache ────────────────────────────────────────────────────

    def get_stale_categories(self) -> dict[str, float]:
        """
        Retourne {catégorie: âge_en_secondes} pour les catégories dont
        la clé LA PLUS RÉCENTE dépasse son seuil de fraîcheur configuré.
        Les clés orphelines (anciennes rooms renommées, etc.) sont ignorées.
        Exemple : {"weather": 50400.0, "sensor": 4200.0}
        """
        now = time.time()
        cat_latest: dict[str, float] = {}   # catégorie → timestamp le plus récent
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute("SELECT key, updated_at FROM cache").fetchall()
            for key, updated_at in rows:
                cat = key.split(".")[0]
                if cat in STALE_THRESHOLDS:
                    cat_latest[cat] = max(cat_latest.get(cat, 0.0), updated_at)
        except Exception as e:
            logger.error("DataCache.get_stale_categories: %s", e)
            return {}

        return {
            cat: now - latest
            for cat, latest in cat_latest.items()
            if (now - latest) > STALE_THRESHOLDS[cat]
        }


data_cache = DataCache()
