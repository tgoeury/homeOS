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


VOLUMES_RESP = {
    "success": True,
    "data": {
        "volumes": [
            {
                "volume_path":    "/volume1",
                "size_total_byte": 8_000_000_000_000,
                "size_used_byte":  2_000_000_000_000,
                "status":          "normal",
                "fs_type":         "btrfs",
            },
            {
                "volume_path":    "/volume2",
                "size_total_byte": 4_000_000_000_000,
                "size_used_byte":  3_600_000_000_000,
                "status":          "normal",
                "fs_type":         "ext4",
            },
        ]
    },
}

DISKS_RESP = {
    "success": True,
    "data": {
        "disks": [
            {"diskno": "Disk 1", "model": "ST4000VX007", "temp": 35, "status": "normal"},
            {"diskno": "Disk 2", "model": "WD40PURZ",    "temp": 38, "status": "normal"},
        ]
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
        client._cache = {"volumes": [], "disks": [], "fetched_at": time.time()}
        client._cache_ts = time.time()
        assert client.is_stale(3600) is False

    def test_stale_after_ttl(self, client):
        client._cache = {"volumes": [], "disks": [], "fetched_at": time.time()}
        client._cache_ts = time.time() - 3700
        assert client.is_stale(3600) is True

    def test_stale_at_custom_threshold(self, client):
        client._cache_ts = time.time() - 7_200
        client._cache = {"volumes": [], "disks": []}
        assert client.is_stale(6 * 3600) is False
        assert client.is_stale(3600) is True

    def test_cache_age_returns_elapsed(self, client):
        client._cache_ts = time.time() - 1800
        client._cache = {}
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
        assert vols[0]["path"] == "/volume1"
        assert vols[0]["total_bytes"] == 8_000_000_000_000
        assert vols[0]["used_bytes"]  == 2_000_000_000_000
        assert vols[0]["free_bytes"]  == 6_000_000_000_000
        assert vols[0]["used_pct"]    == 25
        assert vols[0]["status"]      == "normal"

    def test_calculates_used_pct(self, client):
        resp = {"success": True, "data": {"volumes": [{
            "volume_path": "/v1",
            "size_total_byte": 1000,
            "size_used_byte":  750,
            "status": "normal",
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

    def test_zero_division_safe_when_total_zero(self, client):
        resp = {"success": True, "data": {"volumes": [{
            "volume_path": "/v1",
            "size_total_byte": 0,
            "size_used_byte":  0,
            "status": "normal",
        }]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            vols = client._get_volumes("sid")
        assert vols[0]["used_pct"] == 0


# ── _get_disks() ─────────────────────────────────────────────────────────────

class TestGetDisks:
    def test_parses_disks_correctly(self, client):
        with patch("modules.synology_client.requests.get",
                   return_value=_make_resp(DISKS_RESP)):
            disks = client._get_disks("sid")
        assert len(disks) == 2
        assert disks[0]["name"]   == "Disk 1"
        assert disks[0]["model"]  == "ST4000VX007"
        assert disks[0]["temp"]   == 35
        assert disks[0]["status"] == "normal"

    def test_uses_diskno_over_name(self, client):
        resp = {"success": True, "data": {"disks": [{
            "diskno": "Disk 3", "name": "sdc",
            "model": "WD", "temp": 32, "status": "normal",
        }]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            disks = client._get_disks("sid")
        assert disks[0]["name"] == "Disk 3"

    def test_returns_empty_on_api_failure(self, client):
        fail = {"success": False, "error": {"code": 105}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(fail)):
            assert client._get_disks("sid") == []

    def test_temp_can_be_none(self, client):
        resp = {"success": True, "data": {"disks": [{
            "diskno": "Disk 1", "model": "X", "status": "normal",
        }]}}
        with patch("modules.synology_client.requests.get", return_value=_make_resp(resp)):
            disks = client._get_disks("sid")
        assert disks[0]["temp"] is None


# ── fetch() ───────────────────────────────────────────────────────────────────

class TestFetch:
    def _mock_api(self, client, auth_ok=True, vol_resp=None, disk_resp=None):
        """Patch login + volumes + disks GET pour simuler un fetch complet."""
        vol_resp  = vol_resp  or VOLUMES_RESP
        disk_resp = disk_resp or DISKS_RESP
        auth_resp = _auth_ok() if auth_ok else _auth_fail()

        def side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            api = params.get("api", "")
            if "Volume" in api:
                return _make_resp(vol_resp)
            if "Disk" in api:
                return _make_resp(disk_resp)
            # logout
            return _make_resp({"success": True})

        post_mock = MagicMock(return_value=_make_resp(auth_resp))
        get_mock  = MagicMock(side_effect=side_effect)
        return patch("modules.synology_client.requests.post", post_mock), \
               patch("modules.synology_client.requests.get",  get_mock)

    def test_fetch_returns_dict_with_volumes_and_disks(self, client):
        p_post, p_get = self._mock_api(client)
        with p_post, p_get:
            result = client.fetch()
        assert result is not None
        assert "volumes" in result
        assert "disks"   in result
        assert len(result["volumes"]) == 2
        assert len(result["disks"])   == 2

    def test_fetch_updates_cache(self, client):
        p_post, p_get = self._mock_api(client)
        with p_post, p_get:
            result = client.fetch()
        assert client._cache is result
        assert client._cache_ts > 0

    def test_fetch_uses_cache_within_ttl(self, client):
        client._cache    = {"volumes": [], "disks": [], "fetched_at": time.time()}
        client._cache_ts = time.time()
        with patch("modules.synology_client.requests.post") as mock_post:
            result = client.fetch()
        mock_post.assert_not_called()
        assert result is client._cache

    def test_fetch_force_bypasses_cache(self, client):
        client._cache    = {"volumes": [], "disks": [], "fetched_at": time.time()}
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
        old_cache = {"volumes": [{"path": "/v1"}], "disks": []}
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
            if "Volume" in api:
                return _make_resp(VOLUMES_RESP)
            if "Disk" in api:
                return _make_resp(DISKS_RESP)
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
            if "Volume" in params.get("api", ""):
                raise req_lib.RequestException("error")
            # logout
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
