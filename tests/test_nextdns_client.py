"""Tests unitaires pour modules/nextdns_client.py — NextDNSClient."""
import time
from unittest.mock import MagicMock, patch

import pytest

from modules.nextdns_client import NextDNSClient


@pytest.fixture
def client():
    return NextDNSClient()


@pytest.fixture
def configured_client():
    """Client avec des credentials NextDNS factices."""
    c = NextDNSClient()
    with patch("modules.nextdns_client.config") as mock_cfg:
        mock_cfg.NEXTDNS_API_KEY    = "fake_key_abc123"
        mock_cfg.NEXTDNS_PROFILE_ID = "abc123"
        c._cfg = mock_cfg
    return c


# ── configured() ──────────────────────────────────────────────────────────────

class TestConfigured:
    def test_false_when_api_key_empty(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = ""
            mock_cfg.NEXTDNS_PROFILE_ID = "abc123"
            c = NextDNSClient()
            assert c.configured() is False

    def test_false_when_profile_id_empty(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "key"
            mock_cfg.NEXTDNS_PROFILE_ID = ""
            c = NextDNSClient()
            assert c.configured() is False

    def test_true_when_both_present(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "key"
            mock_cfg.NEXTDNS_PROFILE_ID = "prof"
            c = NextDNSClient()
            assert c.configured() is True


# ── _get() — cache TTL ────────────────────────────────────────────────────────

class TestInternalGet:
    def _make_resp(self, data):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = data
        return resp

    def test_returns_none_when_not_configured(self, client):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = ""
            mock_cfg.NEXTDNS_PROFILE_ID = ""
            c = NextDNSClient()
            assert c._get("analytics/status") is None

    def test_caches_result_within_ttl(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "key"
            mock_cfg.NEXTDNS_PROFILE_ID = "prof"
            c = NextDNSClient()
            resp = self._make_resp({"data": []})
            with patch("modules.nextdns_client.requests.get", return_value=resp) as mock_get:
                c._get("analytics/status")
                c._get("analytics/status")
            mock_get.assert_called_once()   # second call uses cache

    def test_refetches_after_ttl_expired(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "key"
            mock_cfg.NEXTDNS_PROFILE_ID = "prof"
            c = NextDNSClient()
            resp = self._make_resp({"data": []})
            with patch("modules.nextdns_client.requests.get", return_value=resp) as mock_get:
                c._get("analytics/status")
                # Expire le cache
                for key in c._cache:
                    c._cache[key]["ts"] = time.time() - c.TTL - 1
                c._get("analytics/status")
            assert mock_get.call_count == 2


# ── get_status() ──────────────────────────────────────────────────────────────

class TestGetStatus:
    STATUS_RESPONSE = {
        "data": [
            {"status": "allowed",  "queries": 8000},
            {"status": "blocked",  "queries": 2000},
        ]
    }

    def test_returns_none_when_not_configured(self, client):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = ""
            mock_cfg.NEXTDNS_PROFILE_ID = ""
            c = NextDNSClient()
            assert c.get_status() is None

    def test_calculates_total(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=self.STATUS_RESPONSE):
                result = c.get_status()
        assert result["total"] == 10000

    def test_calculates_blocked(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=self.STATUS_RESPONSE):
                result = c.get_status()
        assert result["blocked"] == 2000

    def test_calculates_rate(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=self.STATUS_RESPONSE):
                result = c.get_status()
        assert result["rate"] == pytest.approx(20.0)

    def test_rate_zero_when_no_queries(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value={"data": []}):
                result = c.get_status()
        assert result["rate"] == 0.0

    def test_returns_none_on_api_failure(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=None):
                assert c.get_status() is None


# ── get_traffic_countries() ───────────────────────────────────────────────────

class TestGetTrafficCountries:
    COUNTRIES_RESPONSE = {
        "data": [
            {"code": "US", "queries": 3000},
            {"code": "FR", "queries": 1500},
            {"code": "",   "queries": 500},   # code vide → filtré
        ]
    }

    def test_returns_countries_list(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=self.COUNTRIES_RESPONSE):
                result = c.get_traffic_countries()
        assert len(result) == 2
        assert result[0]["country"] == "US"
        assert result[1]["country"] == "FR"

    def test_filters_empty_country_code(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=self.COUNTRIES_RESPONSE):
                result = c.get_traffic_countries()
        assert all(r["country"] for r in result)

    def test_returns_empty_list_on_failure(self):
        with patch("modules.nextdns_client.config") as mock_cfg:
            mock_cfg.NEXTDNS_API_KEY    = "k"
            mock_cfg.NEXTDNS_PROFILE_ID = "p"
            c = NextDNSClient()
            with patch.object(c, "_get", return_value=None):
                assert c.get_traffic_countries() == []
