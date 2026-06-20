"""Tests unitaires pour modules/synology_client.py — SynologyClient DSM API."""
import time
from unittest.mock import MagicMock, patch, call

import pytest

from modules.synology_client import SynologyClient, _fmt_bytes, NAS_STALE_SECS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_resp(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


def _auth_ok(sid="test_sid_123"):
    return {"success": True, "data": {"sid": sid}}


def _auth_fail():
    return {"success": False, "error": {"code": 400}}


# SYNO.FileStation.List format — shares avec additional.volume_status
VOLUMES_RESP = {
    "success": True,
    "data": {
        "shares": [
            {
                "path": "/volume1",
                "additional": {
                    "volume_status": {
                        "totalspace": 8_000_000_000_000,
                        "freespace":  6_000_000_000_000,
                    }
                },
            },
            {
                "path": "/volume2",
                "additional": {
                    "volume_status": {
                        "totalspace": 4_000_000_000_000,
                        "freespace":    400_000_000_000,
                    }
                },
            },
        ]
    },
}

# SYNO.DSM.Info format
SYSTEM_RESP = {
    "success": True,
    "data": {
        "model":            "DS420+",
        "ram":              2048,
        "temperature":      42,
        "temperature_warn": False,
        "uptime":           86400,
        "version_string":   "DSM 7.2.1-69057",
    },
}


@pytest.fixture
def client():
    """SynologyClient avec config NAS mockée — patch CFG actif pendant tout le test."""
    with patch("modules.synology_client.CFG") as mock_cfg:
        mock_cfg.SYNOLOGY_NAS_HOST     = "192.168.1.20"
        mock_cfg.SYNOLOGY_NAS_PORT     = 5001
        mock_cfg.SYNOLOGY_NAS_USER     = "admin"
        mock_cfg.SYNOLOGY_NAS_PASSWORD = "secret"
        mock_cfg.SYNOLOGY_NAS_TTL      = 3600
        c = SynologyClient()
        yield c


@pytest.fixture
def unconfigured_client():
    """SynologyClient sans credentials — patch CFG actif pendant tout le test."""
    with patch("modules.synology_client.CFG") as mock_cfg:
        mock_cfg.SYNOLOGY_NAS_HOST     = "192.168.1.20"
        mock_cfg.SYNOLOGY_NAS_PORT     = 5001
        mock_cfg.SYNOLOGY_NAS_USER     = ""
        mock_cfg.SYNOLOGY_NAS_PASSWORD = ""
        mock_cfg.SYNOLOGY_NAS_TTL      = 3600
        c = SynologyClient()
        yield c


# ── _fmt_bytes() ──────────────────────────────────────────────────────────────

class TestFmtBytes:
    def test_terabytes(self):
        assert _fmt_bytes(8_000_000_000_000) == "8.0 TB"

    def test_gigabytes(self):
        assert _fmt_bytes(500_000_000) == "500.0 MB"

    def test_megabytes(self):
        assert _fmt_bytes(2_000_000) == "2.0 MB"

    def test_bytes_fallback(self):
        assert _fmt_bytes(1024) == "1024 B"

    def test_gigabytes_threshold(self):
        assert "GB" in _fmt_bytes(1_500_000_000)


# ── is_configured() ───────────────────────────────────────────────────────────

class TestIsConfigured:
    def test_true_when_credentials_present(self):
        with patch("modules.synology_client.CFG") as cfg:
            cfg.SYNOLOGY_NAS_USER     = "admin"
            cfg.SYNOLOGY_NAS_PASSWORD = "pass"
            c = SynologyClient()
            assert c.is_configured() is True

    def test_false_when_user_empty(self):
        with patch("modules.synology_client.CFG") as cfg:
            cfg.SYNOLOGY_NAS_USER     = ""
            cfg.SYNOLOGY_NAS_PASSWORD = "pass"
            c = SynologyClient()
            assert c.is_configured() is False

    def test_false_when_password_empty(self):
        with patch("modules.synology_client.CFG") as cfg:
            cfg.SYNOLOGY_NAS_USER     = "admin"
            cfg.SYNOLOGY_NAS_PASSWORD = ""
            c = SynologyClient()
            assert c.is_configured() is False

    def test_false_when_both_empty(self):
        with patch("modules.synology_client.CFG") as cfg:
            cfg.SYNOLOGY_NAS_USER     = ""
            cfg.SYNOLOGY_NAS_PASSWORD = ""
            c = SynologyClient()
            assert c.is_configured() is False

    def test_false_when_whitespace_only(self):
        with patch("modules.synology_client.CFG") as cfg:
            cfg.SYNOLOGY_NAS_USER     = "   "
            cfg.SYNOLOGY_NAS_PASSWORD = "   "
            c = SynologyClient()
            assert c.is_configured() is False


# ── is_stale() / cache_age() ──────────────────────────────────────────────────

class TestStaleness:
    def test_stale_when_no_cache(self, client):
        assert client.is_stale() is True

    def test_cache_age_none_when_no_cache(self, client):
        assert client.cache_age() is None

    def test_not_stale_when_fresh_cache(self, client):
        client._cache    = {"volumes": [], "system": {}, "fetched_at": time.time()}
        client._cache_ts = time.time()
        assert client.is_stale(3600) is False

    def test_stale_after_ttl(self, client):
        client._cache    = {"volumes": [], "system": {}, "fetched_at": time.time()}
        client._cache_ts = time.time() - 3700
        assert client.is_stale(3600) is True

    def test_stale_at_custom_threshold(self, client):
        client._cache_ts = time.time() - 7_200
        client._cache    = {"volumes": [], "system": {}}
        assert client.is_stale(6 * 3600) is False
        assert client.is_stale(3600) is True

    def test_cache_age_returns_elapsed(self, client):
        client._cache_ts = time.time() - 1800
        client._cache    = {}
        assert client.cache_age() == pytest.approx(1800, abs=2)


# ── _login() ──────────────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success_returns_sid(self, client):
        with patch("modules.synology_client.requests.post",
                   return_value=_make_resp(_auth_ok("sid_abc"))):
            sid = client._login()
        assert sid == "sid_abc"

    def test_login_failure_returns_none(self, client):
        with patch("modules.synology_client.requests.post",
                   return_value=_make_resp(_auth_fail())):
            sid = client._login()
        assert sid is None

    def test_login_network_error_returns_none(self, client):
        import requests as req_lib
        with patch("modules.synology_client.requests.post",
                   side_effect=req_lib.RequestException("timeout")):
            sid = client._login()
        assert sid is None

    def test_login_posts_credentials(self, client):
        mock_post = MagicMock(return_value=_make_resp(_auth_ok()))
        with patch("modules.synology_client.requests.post", mock_post):
            with patch("modules.synology_client.CFG") as cfg:
                cfg.SYNOLOGY_NAS_HOST     = "192.168.1.20"
                cfg.SYNOLOGY_NAS_PORT     = 5001
                cfg.SYNOLOGY_NAS_USER     = "myuser"
                cfg.SYNOLOGY_NAS_PASSWORD = "mypass"
                cfg.SYNOLOGY_NAS_TTL      = 3600
                c = SynologyClient()
                c._login()
        call_kwargs = mock_post.call_args[1]["data"]
        assert call_kwargs["account"] == "myuser"
        assert call_kwargs["passwd"]  == "mypass"
        assert call_kwargs["method"]  == "login"


# ── _logout() ────────────────────────────────────────────────────────────────

class TestLogout:
    def test_logout_does_not_raise_on_error(self, client):
        import requests as req_lib
        with patch("modules.synology_client.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            client._logout("any_sid")   # doit passer silencieusement

    def test_logout_sends_get_with_sid(self, client):
        mock_get = MagicMock(return_value=_make_resp({"success": True}))
        with patch("modules.synology_client.requests.get", mock_get):
            client._logout("test_sid")
        params = mock_get.call_args[1]["params"]
        assert params["_sid"] == "test_sid"
        assert params["method"] == "logout"


# ── _get_volumes() ────────────────────────────────────────────────────────────

class TestGetVolumes:
    def test_parses_volumes_correctly(self, client):
        with patch("modules.synology_client.requests.get",
                   return_value=_make_resp(VOLUMES_RESP)):
            vols = client._get_volumes("sid")
        assert len(vols) == 2
        assert vols[0]["path"]        == "/volume1"
        assert vols[0]["total_bytes"] == 8_000_000_000_000
        assert vols[0]["used_bytes"]  == 2_000_000_000_000   # total - free
        assert vols[0]["free_bytes"]  == 6_000_000_000_000
        assert vols[0]["used_pct"]    == 25

    def test_calculates_used_pct(self, client):
        resp = {"success": True, "data": {"shares": [{
            "path": "/v1",
            "additional": {"volume_status": {
                "totalspace": 1000,
                "freespace":  250,
            }},
        }]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            vols = client._get_volumes("sid")
        assert vols[0]["used_pct"] == 75

    def test_includes_human_readable_sizes(self, client):
        with patch("modules.synology_client.requests.get",
                   return_value=_make_resp(VOLUMES_RESP)):
            vols = client._get_volumes("sid")
        assert "TB" in vols[0]["total_str"]
        assert "TB" in vols[0]["used_str"]

    def test_returns_empty_on_api_failure(self, client):
        fail = {"success": False, "error": {"code": 105}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(fail)):
            assert client._get_volumes("sid") == []

    def test_returns_empty_on_network_error(self, client):
        import requests as req_lib
        with patch("modules.synology_client.requests.get",
                   side_effect=req_lib.RequestException("conn refused")):
            assert client._get_volumes("sid") == []

    def test_zero_total_share_is_skipped(self, client):
        # _get_volumes filtre les entrées avec totalspace == 0
        resp = {"success": True, "data": {"shares": [{
            "path": "/v1",
            "additional": {"volume_status": {"totalspace": 0, "freespace": 0}},
        }]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            assert client._get_volumes("sid") == []

    def test_deduplicates_shares_on_same_volume(self, client):
        # Deux partages sur le même volume physique (même totalspace/freespace)
        resp = {"success": True, "data": {"shares": [
            {"path": "/homes", "additional": {"volume_status": {"totalspace": 4_000_000_000_000, "freespace": 2_000_000_000_000}}},
            {"path": "/photo", "additional": {"volume_status": {"totalspace": 4_000_000_000_000, "freespace": 2_000_000_000_000}}},
        ]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            vols = client._get_volumes("sid")
        assert len(vols) == 1


# ── _get_system_info() ────────────────────────────────────────────────────────

class TestGetSystemInfo:
    def test_parses_system_info_correctly(self, client):
        with patch("modules.synology_client.requests.get",
                   return_value=_make_resp(SYSTEM_RESP)):
            info = client._get_system_info("sid")
        assert info["model"]            == "DS420+"
        assert info["ram_mb"]           == 2048
        assert info["temperature"]      == 42
        assert info["temperature_warn"] is False
        assert info["uptime_s"]         == 86400
        assert info["version"]          == "DSM 7.2.1-69057"

    def test_temperature_can_be_none(self, client):
        resp = {"success": True, "data": {
            "model": "DS420+", "ram": 2048,
            "uptime": 0, "version_string": "DSM 7",
        }}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            info = client._get_system_info("sid")
        assert info["temperature"] is None

    def test_returns_empty_on_api_failure(self, client):
        fail = {"success": False, "error": {"code": 105}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(fail)):
            assert client._get_system_info("sid") == {}

    def test_returns_empty_on_network_error(self, client):
        import requests as req_lib
        with patch("modules.synology_client.requests.get",
                   side_effect=req_lib.RequestException("conn refused")):
            assert client._get_system_info("sid") == {}


# ── fetch() ───────────────────────────────────────────────────────────────────

class TestFetch:
    def _mock_api(self, client, auth_ok=True, vol_resp=None, sys_resp=None):
        """Patch login + volumes (FileStation) + system (DSM.Info) pour simuler un fetch complet."""
        vol_resp = vol_resp or VOLUMES_RESP
        sys_resp = sys_resp or SYSTEM_RESP
        auth_resp = _auth_ok() if auth_ok else _auth_fail()

        def side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            api = params.get("api", "")
            if "FileStation" in api:
                return _make_resp(vol_resp)
            if "DSM.Info" in api:
                return _make_resp(sys_resp)
            return _make_resp({"success": True})   # logout

        post_mock = MagicMock(return_value=_make_resp(auth_resp))
        get_mock  = MagicMock(side_effect=side_effect)
        return patch("modules.synology_client.requests.post", post_mock), \
               patch("modules.synology_client.requests.get",  get_mock)

    def test_fetch_returns_dict_with_volumes_and_system(self, client):
        p_post, p_get = self._mock_api(client)
        with p_post, p_get:
            result = client.fetch()
        assert result is not None
        assert "volumes" in result
        assert "system"  in result
        assert len(result["volumes"]) == 2
        assert result["system"]["model"] == "DS420+"

    def test_fetch_updates_cache(self, client):
        p_post, p_get = self._mock_api(client)
        with p_post, p_get:
            result = client.fetch()
        assert client._cache is result
        assert client._cache_ts > 0

    def test_fetch_uses_cache_within_ttl(self, client):
        client._cache    = {"volumes": [], "system": {}, "fetched_at": time.time()}
        client._cache_ts = time.time()
        with patch("modules.synology_client.requests.post") as mock_post:
            result = client.fetch()
        mock_post.assert_not_called()
        assert result is client._cache

    def test_fetch_force_bypasses_cache(self, client):
        client._cache    = {"volumes": [], "system": {}, "fetched_at": time.time()}
        client._cache_ts = time.time()
        p_post, p_get = self._mock_api(client)
        with p_post, p_get:
            result = client.fetch(force=True)
        assert len(result["volumes"]) == 2   # données fraîches chargées

    def test_fetch_returns_none_when_not_configured(self, unconfigured_client):
        with patch("modules.synology_client.requests.post") as mock_post:
            result = unconfigured_client.fetch()
        mock_post.assert_not_called()
        assert result is None

    def test_fetch_returns_old_cache_on_login_failure(self, client):
        old_cache = {"volumes": [{"path": "/v1"}], "system": {}}
        client._cache    = old_cache
        client._cache_ts = time.time() - 7200   # expiré
        with patch("modules.synology_client.requests.post",
                   return_value=_make_resp(_auth_fail())):
            result = client.fetch()
        assert result is old_cache

    def test_fetch_calls_logout_after_success(self, client):
        mock_post = MagicMock(return_value=_make_resp(_auth_ok("sid_xyz")))

        def get_side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            api = params.get("api", "")
            if "FileStation" in api:
                return _make_resp(VOLUMES_RESP)
            if "DSM.Info" in api:
                return _make_resp(SYSTEM_RESP)
            return _make_resp({"success": True})   # logout

        with patch("modules.synology_client.requests.post", mock_post):
            with patch("modules.synology_client.requests.get", side_effect=get_side_effect) as mock_get:
                client.fetch()

        logout_calls = [c for c in mock_get.call_args_list if "logout" in str(c)]
        assert len(logout_calls) == 1

    def test_fetch_calls_logout_even_on_volumes_error(self, client):
        """Le logout doit être appelé même si _get_volumes lève une exception."""
        import requests as req_lib
        post_mock = MagicMock(return_value=_make_resp(_auth_ok("sid_x")))

        call_log = []
        def get_side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            api = params.get("api", "")
            if "FileStation" in api:
                raise req_lib.RequestException("error")
            if "DSM.Info" in api:
                return _make_resp(SYSTEM_RESP)
            # logout (SYNO.API.Auth)
            call_log.append("logout")
            return _make_resp({"success": True})

        with patch("modules.synology_client.requests.post", post_mock):
            with patch("modules.synology_client.requests.get", side_effect=get_side_effect):
                client.fetch()
        assert "logout" in call_log


# ── NAS_STALE_SECS constant ───────────────────────────────────────────────────

class TestNasStaleConst:
    def test_stale_secs_is_six_hours(self):
        assert NAS_STALE_SECS == 6 * 3600
