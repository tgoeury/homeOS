"""Tests unitaires pour modules/data_cache.py — DataCache (SQLite)."""
import csv
import time
from pathlib import Path

import pytest

from modules.data_cache import DataCache


@pytest.fixture
def dc(tmp_path):
    """DataCache isolé dans un répertoire temporaire."""
    return DataCache(path=tmp_path / "test.db")


# ── write / read ──────────────────────────────────────────────────────────────

class TestWriteRead:
    def test_read_missing_key_returns_none(self, dc):
        assert dc.read("nonexistent.key") is None

    def test_write_then_read_string(self, dc):
        dc.write("test.key", "hello", unit="", source="unit-test")
        result = dc.read("test.key")
        assert result is not None
        assert result["value"] == "hello"
        assert result["source"] == "unit-test"

    def test_write_then_read_dict(self, dc):
        payload = {"temperature": 21.4, "humidity": 58}
        dc.write("sensor.salon.data", payload)
        result = dc.read("sensor.salon.data")
        assert result["value"] == payload

    def test_write_then_read_list(self, dc):
        dc.write("network.devices", [{"ip": "192.168.1.1", "name": "router"}])
        result = dc.read("network.devices")
        assert isinstance(result["value"], list)
        assert result["value"][0]["ip"] == "192.168.1.1"

    def test_write_overwrites_previous_value(self, dc):
        dc.write("k", "v1")
        dc.write("k", "v2")
        assert dc.read("k")["value"] == "v2"

    def test_write_stores_unit(self, dc):
        dc.write("sensor.temp", 21.4, unit="°C")
        assert dc.read("sensor.temp")["unit"] == "°C"

    def test_write_stores_updated_at(self, dc):
        before = time.time()
        dc.write("k", 1)
        after = time.time()
        ts = dc.read("k")["updated_at"]
        assert before <= ts <= after


# ── log / read_history ────────────────────────────────────────────────────────

class TestLog:
    def test_log_creates_history_entry(self, dc):
        dc.log("sensor_salon_temperature", 21.5, "°C", "zigbee2mqtt")
        rows = dc.read_history("sensor_salon_temperature")
        assert len(rows) == 1
        assert rows[0]["value"] == "21.5"
        assert rows[0]["unit"] == "°C"

    def test_log_same_value_same_series_skips_write_when_history_exists(self, dc):
        dc.log("s", 21.0, "°C", "src")
        dc.log("s", 21.0, "°C", "src")
        assert len(dc.read_history("s")) == 1

    def test_log_same_sig_empty_history_writes_anyway(self, dc):
        """Bypass write-on-change : si l'historique est vide, toujours écrire."""
        import json
        from modules.data_cache import DataCache as DC, LOG_HEARTBEAT_INTERVAL
        dc2 = DC(path=Path(dc._path))
        # Injecte la signature exacte qu'utiliserait log() (4 éléments avec bucket courant)
        bucket = int(time.time() // LOG_HEARTBEAT_INTERVAL)
        sig = ["src", "21.0", "°C", bucket]
        with dc2._lock, dc2._connect() as conn:
            conn.execute(
                "INSERT INTO cache (key,value,unit,source,updated_at) VALUES (?,?,?,?,?)",
                ("log.last.orphan", json.dumps(sig), "", "test", time.time()),
            )
        # log() doit quand même écrire puisqu'il n'y a pas de data dans history
        dc2.log("orphan", 21.0, "°C", "src")
        assert len(dc2.read_history("orphan")) == 1

    def test_log_forces_write_after_heartbeat_interval(self, dc, monkeypatch):
        """Un point est écrit toutes les LOG_HEARTBEAT_INTERVAL secondes même si la valeur est stable."""
        import modules.data_cache as dc_mod
        # Premier write dans bucket 0
        monkeypatch.setattr(dc_mod.time, "time", lambda: 0.0)
        dc.log("s", 21.0, "°C", "src")
        assert len(dc.read_history("s")) == 1

        # Même valeur, même bucket → skip
        monkeypatch.setattr(dc_mod.time, "time", lambda: dc_mod.LOG_HEARTBEAT_INTERVAL / 2)
        dc.log("s", 21.0, "°C", "src")
        assert len(dc.read_history("s")) == 1

        # Même valeur, bucket suivant → heartbeat forcé
        monkeypatch.setattr(dc_mod.time, "time", lambda: dc_mod.LOG_HEARTBEAT_INTERVAL + 1.0)
        dc.log("s", 21.0, "°C", "src")
        assert len(dc.read_history("s")) == 2

    def test_log_different_value_writes_new_entry(self, dc):
        dc.log("s", 21.0, "°C", "src")
        time.sleep(0.01)
        dc.log("s", 22.0, "°C", "src")
        rows = dc.read_history("s")
        assert len(rows) == 2
        assert {r["value"] for r in rows} == {"21.0", "22.0"}

    def test_log_different_source_writes_new_entry(self, dc):
        dc.log("s", 21.0, "°C", "src_a")
        time.sleep(0.01)
        dc.log("s", 21.0, "°C", "src_b")
        assert len(dc.read_history("s")) == 2

    def test_log_stores_source(self, dc):
        dc.log("s", 5, "%", "SGS01Z")
        rows = dc.read_history("s")
        assert rows[0]["source"] == "SGS01Z"

    def test_log_multiple_series_independent(self, dc):
        dc.log("series_a", 1, "u", "s")
        dc.log("series_b", 2, "u", "s")
        assert len(dc.read_history("series_a")) == 1
        assert len(dc.read_history("series_b")) == 1


class TestReadHistory:
    def _populate(self, dc, name, values):
        for v in values:
            dc.log(name, v, "°C", "test")
            time.sleep(0.01)

    def test_returns_empty_for_unknown_series(self, dc):
        assert dc.read_history("unknown") == []

    def test_sorted_asc(self, dc):
        self._populate(dc, "s", [10, 20, 30])
        vals = [r["value"] for r in dc.read_history("s")]
        assert vals == ["10", "20", "30"]

    def test_since_ts_filter(self, dc):
        dc.log("s", 1, "u", "src")
        cutoff = time.time()
        time.sleep(0.02)
        dc.log("s", 2, "u", "src")
        rows = dc.read_history("s", since_ts=cutoff)
        assert len(rows) == 1
        assert rows[0]["value"] == "2"

    def test_limit(self, dc):
        self._populate(dc, "s", [1, 2, 3, 4, 5])
        rows = dc.read_history("s", limit=3)
        assert len(rows) == 3


# ── get_stale_categories ──────────────────────────────────────────────────────

class TestStaleness:
    def test_fresh_entry_not_stale(self, dc):
        dc.write("sensor.salon.temperature", 21.0)
        stale = dc.get_stale_categories()
        assert "sensor" not in stale

    def test_stale_entry_detected(self, dc):
        import sqlite3
        # Insère une entrée sensor vieille de 2h dans cache
        old_ts = time.time() - 7_201
        with dc._lock, dc._connect() as conn:
            conn.execute(
                "INSERT INTO cache (key,value,unit,source,updated_at) VALUES (?,?,?,?,?)",
                ("sensor.bureau.temperature", "18.0", "°C", "test", old_ts),
            )
        stale = dc.get_stale_categories()
        assert "sensor" in stale

    def test_unknown_category_ignored(self, dc):
        dc.write("log.last.foo", "bar")
        stale = dc.get_stale_categories()
        assert "log" not in stale

    def test_only_latest_key_per_category_counts(self, dc):
        # sensor a une clé vieille et une récente → ne doit pas être stale
        old_ts = time.time() - 7_201
        with dc._lock, dc._connect() as conn:
            conn.execute(
                "INSERT INTO cache (key,value,unit,source,updated_at) VALUES (?,?,?,?,?)",
                ("sensor.old.temp", "10", "°C", "test", old_ts),
            )
        dc.write("sensor.new.temp", 22.0)
        stale = dc.get_stale_categories()
        assert "sensor" not in stale


# ── _migrate_legacy_csvs ──────────────────────────────────────────────────────

class TestMigrateCsvs:
    def _write_csv(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "source", "value", "unit"])
            writer.writeheader()
            writer.writerows(rows)

    def test_imports_sensor_csv_rows(self, tmp_path):
        # DB à tmp_path/data/cache.db → migration cherche tmp_path/data/history/
        history_dir = tmp_path / "data" / "history"
        self._write_csv(history_dir / "sensor_salon_temperature.csv", [
            {"timestamp": "2024-01-01T12:00:00", "source": "zigbee", "value": "21.0", "unit": "°C"},
            {"timestamp": "2024-01-01T13:00:00", "source": "zigbee", "value": "21.5", "unit": "°C"},
        ])
        db_path = tmp_path / "data" / "cache.db"
        dc = DataCache(path=db_path)
        rows = dc.read_history("sensor_salon_temperature")
        assert len(rows) == 2
        assert rows[0]["value"] == "21.0"

    def test_enedis_csv_mapped_to_correct_series(self, tmp_path):
        history_dir = tmp_path / "data" / "history"
        self._write_csv(history_dir / "enedis_daily_consumption.csv", [
            {"timestamp": "2024-01-15", "source": "enedis", "value": "12.345", "unit": "kWh"},
        ])
        dc = DataCache(path=tmp_path / "data" / "cache.db")
        rows = dc.read_history("enedis_daily")
        assert len(rows) == 1
        assert rows[0]["value"] == "12.345"

    def test_migrated_csv_renamed(self, tmp_path):
        history_dir = tmp_path / "data" / "history"
        csv_path = history_dir / "sensor_test.csv"
        self._write_csv(csv_path, [
            {"timestamp": "2024-01-01T00:00:00", "source": "test", "value": "5", "unit": "u"},
        ])
        DataCache(path=tmp_path / "data" / "cache.db")
        assert not csv_path.exists()
        assert (history_dir / "sensor_test.csv.migrated").exists()

    def test_no_history_dir_doesnt_crash(self, tmp_path):
        dc = DataCache(path=tmp_path / "data" / "cache.db")
        assert dc.read_history("anything") == []
