from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from quarkdl.browser import _validate_cached_cookies, ensure_quark_login
from quarkdl.downloader import RateLimiter


def test_rate_limiter_consumes_and_tracks():
    limiter = RateLimiter(bytes_per_sec=1_000_000)
    # small consume should not block or fail
    limiter.consume(1000)
    assert limiter.rate == 1_000_000


def test_validate_cached_cookies_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"nickname": "TestUser"}}

    with patch("requests.Session.get", return_value=mock_resp):
        valid = _validate_cached_cookies([{"name": "test", "value": "val", "domain": "quark.cn"}])
        assert valid is True


def test_validate_cached_cookies_invalid_expired():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": None, "code": 401}

    with patch("requests.Session.get", return_value=mock_resp):
        valid = _validate_cached_cookies([{"name": "test", "value": "val", "domain": "quark.cn"}])
        assert valid is False


def test_ensure_quark_login_returns_cached(tmp_path: Path):
    cookies = [{"name": "auth", "value": "xyz", "domain": "quark.cn"}]
    cache = tmp_path / "cookies.json"
    cache.write_text(json.dumps(cookies), encoding="utf-8")

    with patch("quarkdl.browser._validate_cached_cookies", return_value=True):
        result = ensure_quark_login(tmp_path)
        assert result == cookies
