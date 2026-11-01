"""End-to-end downloader tests against a local HTTP server that supports Range requests."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import requests

import quarkdl.downloader as dl
from quarkdl.core import DownloadItem
from quarkdl.downloader import DownloadError, ResumableDownloader

PAYLOAD = bytes(range(256)) * 12288  # 3 MiB: above the default 2 MiB range threshold


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, like the real CDN

    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):  # noqa: N802
        state = self.server.state
        with state["lock"]:
            state["connections"].add(self.client_address)
            state["requests"] += 1
            count = state["requests"]
        token = int(parse_qs(urlparse(self.path).query).get("v", ["0"])[0])
        if token < state["min_token"]:
            return self._reply(403, b"expired")
        if count <= state["fail_first"]:
            return self._reply(503, b"busy", {"Retry-After": "0"})
        data = state["files"][urlparse(self.path).path]
        rng = self.headers.get("Range")
        if rng and state["honor_range"]:
            start_s, _, end_s = rng.removeprefix("bytes=").partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            body = data[start : end + 1]
            return self._reply(
                206, body, {"Content-Range": f"bytes {start}-{end}/{len(data)}"}, state=state
            )
        self._reply(200, data, state=state)

    def _reply(self, code, body, headers=None, state=None):
        cut = state["truncate_first_ok"] if state else 0
        send = body
        if cut and state["truncations"] < cut:
            state["truncations"] += 1
            send = body[: len(body) // 2]
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(send)
        if send is not body:
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # clients drop connections on purpose
        pass


class FakeApi:
    def __init__(self, base_url: str, server_state: dict) -> None:
        self.base_url = base_url
        self.state = server_state
        self.session = requests.Session()
        self.info_calls = 0

    def get_download_info(self, fid: str):
        self.info_calls += 1
        # Each call mints a new "token"; the server can reject old ones to simulate expiry.
        return {"download_url": f"{self.base_url}/{fid}?v={self.info_calls}"}


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)  # no real backoff waits
    httpd = _Server(("127.0.0.1", 0), _Handler)
    httpd.state = {
        "lock": threading.Lock(),
        "connections": set(),
        "requests": 0,
        "files": {},
        "min_token": 0,
        "fail_first": 0,
        "honor_range": True,
        "truncate_first_ok": 0,
        "truncations": 0,
    }
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _make(server, tmp_path, **kwargs):
    api = FakeApi(f"http://127.0.0.1:{server.server_port}", server.state)
    return api, ResumableDownloader(api, tmp_path / "out", **kwargs)


def _item(fid="big.bin", size=None):
    size = len(PAYLOAD) if size is None else size
    return DownloadItem(fid=fid, path=f"root/{fid}", size=size, name=fid)


def test_ranged_download_reuses_connections(server, tmp_path):
    server.state["files"]["/big.bin"] = PAYLOAD
    api, downloader = _make(server, tmp_path, chunk_size=16 * 1024, workers=4, temp_parent=None)
    seen: list[tuple[int, int]] = []

    with downloader:
        dest = downloader.download_one(_item(), progress=lambda _i, d, t: seen.append((d, t)))

    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_name("big.bin.chunks").exists()
    assert seen[0] == (0, len(PAYLOAD))  # baseline report before any transfer
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    chunks = len(PAYLOAD) // (16 * 1024)  # one request per chunk, over keep-alive sessions
    assert server.state["requests"] == chunks
    assert len(server.state["connections"]) <= 4 * 2


def test_ranged_download_retries_transient_errors(server, tmp_path):
    server.state["files"]["/big.bin"] = PAYLOAD
    server.state["fail_first"] = 5
    _, downloader = _make(server, tmp_path, chunk_size=64 * 1024, workers=2)

    with downloader:
        dest = downloader.download_one(_item())

    assert dest.read_bytes() == PAYLOAD


def test_ranged_download_refreshes_expired_link(server, tmp_path):
    server.state["files"]["/big.bin"] = PAYLOAD
    server.state["min_token"] = 2  # the first URL (v=1) is "expired" immediately
    api, downloader = _make(server, tmp_path, chunk_size=128 * 1024, workers=4)

    with downloader:
        dest = downloader.download_one(_item())

    assert dest.read_bytes() == PAYLOAD
    assert 2 <= api.info_calls <= 1 + 4  # refreshes are shared between workers holding one URL


def test_ranged_download_gives_up_and_reports_chunk(server, tmp_path):
    server.state["files"]["/big.bin"] = PAYLOAD
    server.state["fail_first"] = 10_000
    _, downloader = _make(server, tmp_path, chunk_size=256 * 1024, workers=2, retries=2)

    with downloader, pytest.raises(DownloadError, match="chunk failed.*HTTP 503"):
        downloader.download_one(_item())


def test_ranged_download_resumes_from_existing_chunks(server, tmp_path):
    server.state["files"]["/big.bin"] = PAYLOAD
    _, downloader = _make(server, tmp_path, chunk_size=256 * 1024, workers=2)
    chunks_dir = tmp_path / "out" / "root" / "big.bin.chunks"
    chunks_dir.mkdir(parents=True)
    (chunks_dir / "000000.part").write_bytes(PAYLOAD[: 256 * 1024])
    (chunks_dir / "000001.part").write_bytes(PAYLOAD[256 * 1024 : 512 * 1024])

    with downloader:
        dest = downloader.download_one(_item())

    assert dest.read_bytes() == PAYLOAD
    assert server.state["requests"] == len(PAYLOAD) // (256 * 1024) - 2  # only missing chunks


def test_stream_download_resumes_partial_file_after_dropped_connection(server, tmp_path):
    data = PAYLOAD[:100_000]
    server.state["files"]["/small.bin"] = data
    server.state["truncate_first_ok"] = 1  # first response is cut in half, then connection closed
    _, downloader = _make(server, tmp_path)

    with downloader:
        dest = downloader.download_one(_item("small.bin", len(data)))

    assert dest.read_bytes() == data


def test_stream_download_restarts_when_server_ignores_range(server, tmp_path):
    data = PAYLOAD[:100_000]
    server.state["files"]["/small.bin"] = data
    server.state["honor_range"] = False
    _, downloader = _make(server, tmp_path)
    part = tmp_path / "out" / "root" / "small.bin.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"garbage-prefix")

    with downloader:
        dest = downloader.download_one(_item("small.bin", len(data)))

    assert dest.read_bytes() == data


def test_stream_download_refreshes_expired_link(server, tmp_path):
    data = PAYLOAD[:50_000]
    server.state["files"]["/small.bin"] = data
    server.state["min_token"] = 2
    api, downloader = _make(server, tmp_path)

    with downloader:
        dest = downloader.download_one(_item("small.bin", len(data)))

    assert dest.read_bytes() == data
    assert api.info_calls == 2


def test_zero_byte_file_needs_no_download_url(server, tmp_path):
    api, downloader = _make(server, tmp_path)

    with downloader:
        dest = downloader.download_one(_item("empty.bin", 0))

    assert dest.read_bytes() == b""
    assert api.info_calls == 0
    assert server.state["requests"] == 0


def test_download_many_continues_after_a_failed_file(server, tmp_path):
    server.state["files"]["/good.bin"] = PAYLOAD[:50_000]
    server.state["files"]["/bad.bin"] = PAYLOAD[:50_000]
    api, downloader = _make(server, tmp_path, retries=1)
    real_info = api.get_download_info

    def info(fid):
        if fid == "bad.bin":
            raise dl.QuarkApiError("download url: code=1 message=nope")
        return real_info(fid)

    api.get_download_info = info
    items = [_item("bad.bin", 50_000), _item("good.bin", 50_000)]

    with downloader:
        failures = downloader.download_many(items)

    assert [(i.fid, "nope" in msg) for i, msg in failures] == [("bad.bin", True)]
    assert (tmp_path / "out" / "root" / "good.bin").read_bytes() == PAYLOAD[:50_000]
