"""Tests unitaires pour modules/chatbot_engine.py — état in-memory + HTTP mock."""
import time
from unittest.mock import MagicMock, patch

import pytest
import modules.chatbot_engine as ce


@pytest.fixture(autouse=True)
def clear_messages():
    """Vide le store de messages avant et après chaque test."""
    ce.clear_messages()
    ce._last_ping_ok = False
    yield
    ce.clear_messages()
    ce._last_ping_ok = False


# ── add_incoming_message / get_messages ───────────────────────────────────────

class TestStore:
    def test_get_messages_initially_empty(self):
        assert ce.get_messages() == []

    def test_add_incoming_appends_message(self):
        ce.add_incoming_message("Salut", "BotUser")
        msgs = ce.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["role"] == "bot"
        assert msgs[0]["text"] == "Salut"

    def test_add_incoming_sets_timestamp(self):
        before = time.time()
        ce.add_incoming_message("test")
        after = time.time()
        ts = ce.get_messages()[0]["ts"]
        assert before <= ts <= after

    def test_get_messages_returns_copy(self):
        ce.add_incoming_message("msg")
        result = ce.get_messages()
        result.clear()
        assert len(ce.get_messages()) == 1

    def test_multiple_messages_ordered(self):
        ce.add_incoming_message("premier")
        ce.add_incoming_message("second")
        msgs = ce.get_messages()
        assert msgs[0]["text"] == "premier"
        assert msgs[1]["text"] == "second"

    def test_clear_messages_empties_store(self):
        ce.add_incoming_message("test")
        ce.clear_messages()
        assert ce.get_messages() == []


# ── send_message() ────────────────────────────────────────────────────────────

class TestSendMessage:
    def _mock_post(self, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        return patch("modules.chatbot_engine.requests.post", return_value=resp)

    def test_send_stores_user_message(self):
        with self._mock_post():
            ce.send_message("bonjour")
        msgs = ce.get_messages()
        assert any(m["role"] == "user" and m["text"] == "bonjour" for m in msgs)

    def test_send_success_returns_true(self):
        with self._mock_post(200):
            result = ce.send_message("test")
        assert result is True

    def test_send_http_error_returns_false(self):
        with self._mock_post(500):
            result = ce.send_message("test")
        assert result is False

    def test_send_network_error_returns_false(self):
        import requests as req_lib
        with patch("modules.chatbot_engine.requests.post",
                   side_effect=req_lib.RequestException("timeout")):
            result = ce.send_message("test")
        assert result is False

    def test_send_updates_connection_status_on_success(self):
        with self._mock_post(200):
            ce.send_message("ok")
        assert ce.get_connection_status() is True

    def test_send_updates_connection_status_on_failure(self):
        with self._mock_post(503):
            ce.send_message("fail")
        assert ce.get_connection_status() is False


# ── get_connection_status() ───────────────────────────────────────────────────

class TestConnectionStatus:
    def test_false_before_any_send(self):
        assert ce.get_connection_status() is False


# ── _escape() ─────────────────────────────────────────────────────────────────

class TestEscape:
    def test_escapes_double_quotes(self):
        assert '\\"' in ce._escape('"hello"')

    def test_escapes_backslash(self):
        assert "\\\\" in ce._escape("a\\b")

    def test_escapes_newline(self):
        result = ce._escape("line1\nline2")
        assert "\\n" in result
        assert "\n" not in result

    def test_removes_carriage_return(self):
        result = ce._escape("a\rb")
        assert "\r" not in result

    def test_plain_text_unchanged(self):
        assert ce._escape("hello world") == "hello world"


# ── fmt_time() ────────────────────────────────────────────────────────────────

class TestFmtTime:
    def test_returns_hhmmss_format(self):
        import datetime
        ts = datetime.datetime(2024, 6, 1, 14, 30, 45).timestamp()
        result = ce.fmt_time(ts)
        assert result == "14:30:45"

    def test_pads_single_digit_values(self):
        import datetime
        ts = datetime.datetime(2024, 6, 1, 8, 5, 3).timestamp()
        result = ce.fmt_time(ts)
        assert result == "08:05:03"
