"""Tests for slow-connection handling and live transfer accounting."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

import quarkdl.downloader as dl
from quarkdl.core import DownloadItem
from quarkdl.downloader import ResumableDownloader

PAYLOAD = bytes(range(256)) * 240  # 60 KiB


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        state = self.server.state
        with state["lock"]:
            state["requests"] += 1
            count = state["requests"]
            state["ranges"].append(self.headers.get("Range"))
        data = PAYLOAD
        start_s, _, end_s = self.headers["Range"].removeprefix("bytes=").partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else len(data) - 1
        body = data[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        if count <= state["trickle_first"]:
            for i in range(0, len(body), 1024):  # ~50 KB/s
                self.wfile.write(body[i : i + 1024])
                self.wfile.flush()
                threading.Event().wait(0.02)
        else:
            self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # the client hangs up on purpose
        pass


class FakeApi:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.session = requests.Session()

    def get_download_info(self, fid: str):
        return {"download_url": f"{self.base_url}/{fid}"}


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)  # no real retry backoff
    monkeypatch.setattr(dl, "SLOW_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(dl, "MIN_CONNECTION_SPEED", 500_000)
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.state = {"lock": threading.Lock(), "requests": 0, "ranges": [], "trickle_first": 0}
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _downloader(server, tmp_path, **kwargs):
    api = FakeApi(f"http://127.0.0.1:{server.server_port}")
    kwargs.setdefault("workers", 1)
    kwargs.setdefault("chunk_size", len(PAYLOAD))
    return ResumableDownloader(api, tmp_path / "out", range_threshold=0, **kwargs)


def _item():
    return DownloadItem(fid="f.bin", path="root/f.bin", size=len(PAYLOAD), name="f.bin")


def test_crawling_connection_is_replaced_and_partial_bytes_are_kept(server, tmp_path):
    server.state["trickle_first"] = 1
    with _downloader(server, tmp_path) as downloader:
        dest = downloader.download_one(_item())
        slow_restarts = downloader.slow_restarts

    assert dest.read_bytes() == PAYLOAD
    assert slow_restarts == 1
    first, second = server.state["ranges"][:2]
    assert first == f"bytes=0-{len(PAYLOAD) - 1}"
    resumed_from = int(second.removeprefix("bytes=").split("-")[0])
    assert 0 < resumed_from < len(PAYLOAD)  # did not start over from zero


def test_slow_restarts_are_capped_so_a_throttled_cdn_still_completes(server, tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "MAX_SLOW_RESTARTS", 2)
    server.state["trickle_first"] = 10_000  # every connection crawls
    with _downloader(server, tmp_path) as downloader:
        dest = downloader.download_one(_item())
        slow_restarts = downloader.slow_restarts

    assert dest.read_bytes() == PAYLOAD
    assert slow_restarts == 2


def test_rate_limit_disables_slow_connection_detection(server, tmp_path):
    server.state["trickle_first"] = 1
    with _downloader(server, tmp_path, rate_limit=500_000_000) as downloader:
        dest = downloader.download_one(_item())
        slow_restarts = downloader.slow_restarts

    assert dest.read_bytes() == PAYLOAD
    assert slow_restarts == 0


def test_wire_bytes_and_connection_count_are_reported(server, tmp_path):
    received: list[int] = []
    with _downloader(server, tmp_path, on_bytes=received.append) as downloader:
        dest = downloader.download_one(_item())
        assert downloader.active_connections == 0

    assert dest.read_bytes() == PAYLOAD
    assert sum(received) == len(PAYLOAD)
    assert len(received) > 1  # reported block by block, not once per chunk

