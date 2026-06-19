"""Tests unitaires pour modules/enedis_service.py — utilitaires date + stockage."""
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch, call
import threading
import time

import pytest

import modules.enedis_service as es
from modules.enedis_service import (
    _date_to_ts,
    _ts_to_date,
    _default_history_start,
    EnedisService,
)


# ── Utilitaires de date ───────────────────────────────────────────────────────

class TestDateUtils:
    def test_date_to_ts_is_noon(self):
        d = date(2024, 6, 15)
        ts = _date_to_ts(d)
        dt = datetime.fromtimestamp(ts)
        assert dt.hour == 12
        assert dt.minute == 0

    def test_date_to_ts_ts_to_date_round_trip(self):
        d = date(2024, 1, 20)
        assert _ts_to_date(_date_to_ts(d)) == d

    def test_ts_to_date_uses_local_time(self):
        d = date(2024, 3, 10)
        ts = datetime(2024, 3, 10, 12, 0).timestamp()
        assert _ts_to_date(ts) == d

    def test_default_history_start_is_three_years_ago(self):
        start = _default_history_start()
        today = date.today()
        expected_year = today.year - 3
        assert start.year == expected_year
        assert start.month == today.month
        assert start.day == today.day


# ── is_configured() ──────────────────────────────────────────────────────────

class TestIsConfigured:
    def _make_service(self, token="", prm=""):
        with patch("modules.enedis_service.threading.Thread"):
            with patch("modules.enedis_service.CFG") as mock_cfg:
                mock_cfg.ENEDIS_TOKEN = token
                mock_cfg.ENEDIS_PRM   = prm
                svc = EnedisService()
        return svc

    def test_false_when_both_empty(self):
        svc = self._make_service("", "")
        with patch("modules.enedis_service.CFG") as mock_cfg:
            mock_cfg.ENEDIS_TOKEN = ""
            mock_cfg.ENEDIS_PRM   = ""
            assert svc.is_configured() is False

    def test_false_when_token_only(self):
        svc = self._make_service()
        with patch("modules.enedis_service.CFG") as mock_cfg:
            mock_cfg.ENEDIS_TOKEN = "token123"
            mock_cfg.ENEDIS_PRM   = ""
            assert svc.is_configured() is False

    def test_true_when_both_present(self):
        svc = self._make_service()
        with patch("modules.enedis_service.CFG") as mock_cfg:
            mock_cfg.ENEDIS_TOKEN = "token123"
            mock_cfg.ENEDIS_PRM   = "prm456"
            assert svc.is_configured() is True


# ── _store_rows() ─────────────────────────────────────────────────────────────

class TestStoreRows:
    @pytest.fixture
    def service(self, tmp_path):
        """EnedisService avec thread désactivé et data_cache pointant vers tmp."""
        from modules.data_cache import DataCache
        dc = DataCache(path=tmp_path / "cache.db")
        with (
            patch("modules.enedis_service.threading.Thread"),
            patch("modules.enedis_service.data_cache", dc),
        ):
            svc = EnedisService()
            svc._dc = dc
        return svc, dc

    def test_store_rows_writes_to_history(self, service):
        svc, dc = service
        with patch("modules.enedis_service.data_cache", dc):
            svc._store_rows([("2024-06-01", 12.345)])
        rows = dc.read_history("enedis_daily")
        assert len(rows) == 1
        assert rows[0]["value"] == "12.345"
        assert rows[0]["unit"] == "kWh"

    def test_store_rows_timestamp_is_noon(self, service):
        svc, dc = service
        with patch("modules.enedis_service.data_cache", dc):
            svc._store_rows([("2024-06-15", 8.0)])
        rows = dc.read_history("enedis_daily")
        dt = datetime.fromtimestamp(rows[0]["ts"])
        assert dt.hour == 12

    def test_store_rows_multiple_days(self, service):
        svc, dc = service
        with patch("modules.enedis_service.data_cache", dc):
            svc._store_rows([
                ("2024-06-01", 10.0),
                ("2024-06-02", 11.0),
                ("2024-06-03", 12.0),
            ])
        rows = dc.read_history("enedis_daily")
        assert len(rows) == 3

    def test_store_rows_invalid_date_silently_skipped(self, service):
        svc, dc = service
        with patch("modules.enedis_service.data_cache", dc):
            svc._store_rows([("invalid-date", 5.0), ("2024-06-01", 9.0)])
        rows = dc.read_history("enedis_daily")
        assert len(rows) == 1


# ── read_history() ────────────────────────────────────────────────────────────

class TestReadHistory:
    @pytest.fixture
    def service_with_data(self, tmp_path):
        from modules.data_cache import DataCache
        dc = DataCache(path=tmp_path / "cache.db")
        dates = [date(2024, 6, i) for i in range(1, 6)]
        for d, kwh in zip(dates, [10.0, 11.0, 12.0, 11.5, 9.0]):
            ts = _date_to_ts(d)
            with dc._lock, dc._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO history (name,ts,source,value,unit) VALUES (?,?,?,?,?)",
                    ("enedis_daily", ts, "enedis", str(kwh), "kWh"),
                )
        with (
            patch("modules.enedis_service.threading.Thread"),
            patch("modules.enedis_service.data_cache", dc),
        ):
            svc = EnedisService()
        return svc, dc

    def test_returns_sorted_list(self, service_with_data):
        svc, dc = service_with_data
        with patch("modules.enedis_service.data_cache", dc):
            rows = svc.read_history()
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)

    def test_returns_correct_kwh(self, service_with_data):
        svc, dc = service_with_data
        with patch("modules.enedis_service.data_cache", dc):
            rows = svc.read_history()
        assert rows[0]["kwh"] == pytest.approx(10.0)

    def test_deduplicates_same_date(self, service_with_data):
        svc, dc = service_with_data
        # Insère une entrée dupliquée pour le 01/06
        d = date(2024, 6, 1)
        ts2 = _date_to_ts(d) + 0.001
        with dc._lock, dc._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO history (name,ts,source,value,unit) VALUES (?,?,?,?,?)",
                ("enedis_daily", ts2, "enedis", "99.0", "kWh"),
            )
        with patch("modules.enedis_service.data_cache", dc):
            rows = svc.read_history()
        june_1_rows = [r for r in rows if r["date"] == date(2024, 6, 1)]
        assert len(june_1_rows) == 1


# ── _schedule_loop / thread démarrage ────────────────────────────────────────

class TestThreadStartup:
    def test_thread_started_on_init(self):
        mock_thread = MagicMock()
        with patch("modules.enedis_service.threading.Thread", return_value=mock_thread):
            EnedisService()
        mock_thread.start.assert_called_once()

    def test_thread_is_daemon(self):
        created_thread = None

        def capture_thread(*args, **kwargs):
            nonlocal created_thread
            created_thread = threading.Thread(*args, **kwargs)
            return created_thread

        with patch("modules.enedis_service.threading.Thread", side_effect=capture_thread):
            try:
                EnedisService()
            except Exception:
                pass
        if created_thread:
            assert created_thread.daemon is True
