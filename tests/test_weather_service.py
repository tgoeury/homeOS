"""Tests unitaires pour modules/weather_service.py — WeatherService (cache TTL + parsing)."""
import time
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from modules.weather_service import WeatherService, WeatherData, CurrentWeather


# ── Fixture API response ──────────────────────────────────────────────────────

TODAY = date.today().isoformat()

RAW_API_RESPONSE = {
    "current": {
        "temperature_2m":        21.4,
        "apparent_temperature":  19.8,
        "relative_humidity_2m":  58,
        "weather_code":          0,
        "wind_speed_10m":        12.3,
        "wind_gusts_10m":        18.5,
        "precipitation":         0.0,
        "surface_pressure":      1015.2,
    },
    "daily": {
        "time":                    [TODAY, "2024-06-02", "2024-06-03"],
        "weather_code":            [0, 2, 61],
        "temperature_2m_max":      [25.1, 22.0, 18.5],
        "temperature_2m_min":      [16.3, 14.0, 12.0],
        "precipitation_sum":       [0.0,  1.2,  8.5],
    },
    "hourly": {
        "time":        [f"{TODAY}T{h:02d}:00" for h in range(24)] + ["2024-06-02T00:00"],
        "temperature_2m": list(range(24)) + [99],
    },
}


@pytest.fixture
def svc():
    """WeatherService avec config locale (no real network)."""
    return WeatherService(latitude=43.3, longitude=5.4, timezone="Europe/Paris", cache_ttl=600)


# ── _cache_valid() / is_stale() ───────────────────────────────────────────────

class TestCacheValidity:
    def test_cache_invalid_when_none(self, svc):
        assert svc._cache_valid() is False

    def test_is_stale_when_no_cache(self, svc):
        assert svc.is_stale() is True

    def test_cache_valid_when_fresh(self, svc):
        mock_data = MagicMock(spec=WeatherData)
        mock_data.fetched_at = time.time()
        svc._cache = mock_data
        assert svc._cache_valid() is True

    def test_cache_invalid_after_ttl(self, svc):
        mock_data = MagicMock(spec=WeatherData)
        mock_data.fetched_at = time.time() - 700   # TTL = 600s
        svc._cache = mock_data
        assert svc._cache_valid() is False

    def test_is_stale_false_when_fresh_cache(self, svc):
        mock_data = MagicMock(spec=WeatherData)
        mock_data.fetched_at = time.time()
        svc._cache = mock_data
        assert svc.is_stale() is False


# ── get() ─────────────────────────────────────────────────────────────────────

class TestGet:
    def test_get_returns_cached_when_valid(self, svc):
        mock_data = MagicMock(spec=WeatherData)
        mock_data.fetched_at = time.time()
        svc._cache = mock_data
        with patch.object(svc, "_fetch") as mock_fetch:
            result = svc.get()
        mock_fetch.assert_not_called()
        assert result is mock_data

    def test_get_calls_fetch_when_cache_stale(self, svc):
        with patch.object(svc, "_fetch", return_value=None) as mock_fetch:
            svc.get()
        mock_fetch.assert_called_once()

    def test_get_force_bypasses_valid_cache(self, svc):
        mock_data = MagicMock(spec=WeatherData)
        mock_data.fetched_at = time.time()
        svc._cache = mock_data
        with patch.object(svc, "_fetch", return_value=mock_data) as mock_fetch:
            svc.get(force=True)
        mock_fetch.assert_called_once()

    def test_get_returns_none_when_fetch_fails_and_no_cache(self, svc):
        with patch.object(svc, "_fetch", return_value=None):
            result = svc.get()
        assert result is None

    def test_get_returns_stale_cache_when_fetch_fails(self, svc):
        old_data = MagicMock(spec=WeatherData)
        old_data.fetched_at = time.time() - 700
        svc._cache = old_data

        def fetch_fail():
            return svc._cache   # retourne le vieux cache, comme _fetch le ferait

        with patch.object(svc, "_fetch", side_effect=fetch_fail):
            result = svc.get()
        assert result is old_data


# ── _parse() ──────────────────────────────────────────────────────────────────

class TestParse:
    def test_parse_returns_weather_data(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert isinstance(data, WeatherData)

    def test_parse_current_temperature(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.current.temperature == pytest.approx(21.4)

    def test_parse_current_feels_like(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.current.feels_like == pytest.approx(19.8)

    def test_parse_current_humidity(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.current.humidity == 58

    def test_parse_current_weather_code(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.current.weather_code == 0

    def test_parse_daily_count(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert len(data.daily) == 3

    def test_parse_first_daily_label_is_today(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.daily[0].day_name == "Auj"

    def test_parse_hourly_only_today(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert len(data.hourly_today.times) == 24
        assert all(t.startswith(TODAY) for t in data.hourly_today.times)

    def test_parse_description_from_wmo(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert "dégagé" in data.current.description.lower()

    def test_parse_icon_from_wmo(self, svc):
        data = svc._parse(RAW_API_RESPONSE)
        assert data.current.icon == "☀"


# ── _fetch() ──────────────────────────────────────────────────────────────────

class TestFetch:
    def test_fetch_success_updates_cache(self, svc):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = RAW_API_RESPONSE
        with patch("modules.weather_service.requests.get", return_value=mock_resp):
            result = svc._fetch()
        assert result is not None
        assert svc._cache is result

    def test_fetch_network_error_returns_none_if_no_cache(self, svc):
        import requests as req_lib
        with patch("modules.weather_service.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            result = svc._fetch()
        assert result is None

    def test_fetch_network_error_returns_old_cache(self, svc):
        old = MagicMock(spec=WeatherData)
        svc._cache = old
        import requests as req_lib
        with patch("modules.weather_service.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            result = svc._fetch()
        assert result is old
