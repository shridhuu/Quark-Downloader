from __future__ import annotations

import concurrent.futures
import json
import queue
import random
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from .api import PC_UA, QuarkApi, QuarkApiError
from .core import DownloadItem, plan_ranges, sanitize_rel_path

ProgressCallback = Callable[[DownloadItem, int, int], None]

DEFAULT_RETRIES = 5
STATUS_SAVE_INTERVAL = 5.0
AUTH_EXPIRED_CODES = (401, 403)
MAX_RETRY_AFTER = 60.0


class DownloadError(RuntimeError):
    pass


class _SessionPool:
    """Reusable keep-alive sessions for CDN range requests.

    Every chunk used to build a brand-new ``requests.Session``, paying a TCP + TLS handshake per
    chunk. Sessions are now created lazily, handed to one worker at a time and returned to the
    pool, so their connections stay open across chunks and files.
    """

    def __init__(self, source: requests.Session) -> None:
        self._source = source
        self._idle: queue.SimpleQueue[requests.Session] = queue.SimpleQueue()
        self._created: list[requests.Session] = []
        self._lock = threading.Lock()

    def _new(self) -> requests.Session:
        session = requests.Session()
        session.cookies.update(self._source.cookies)
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with self._lock:
            self._created.append(session)
        return session

    @contextmanager
    def acquire(self) -> Iterator[requests.Session]:
        try:
            session = self._idle.get_nowait()
        except queue.Empty:
            session = self._new()
        try:
            yield session
        finally:
            self._idle.put(session)

    def close(self) -> None:
        with self._lock:
            sessions, self._created = self._created, []
        for session in sessions:
            session.close()
        while True:
            try:
                self._idle.get_nowait()
            except queue.Empty:
                break


class _UrlProvider:
    """Thread-safe holder of a file's CDN URL that can mint a fresh one when it expires."""

    def __init__(self, api: QuarkApi, fid: str, url: str) -> None:
        self._api = api
        self._fid = fid
        self._url = url
        self._lock = threading.Lock()

    def current(self) -> str:
        return self._url

    def refresh(self, stale_url: str) -> str:
        """Return a fresh URL; concurrent callers holding the same stale URL share one refresh."""
        with self._lock:
            if self._url == stale_url:
                self._url = self._api.get_download_info(self._fid)["download_url"]
            return self._url


class ResumableDownloader:
    def __init__(
        self,
        api: QuarkApi,
        output_dir: Path,
        *,
        temp_parent: str | None = None,
        chunk_size: int = 1024 * 1024,
        workers: int = 32,
        range_threshold: int = 2 * 1024 * 1024,
        retries: int = DEFAULT_RETRIES,
        status_path: Path | None = None,
    ) -> None:
        self.api = api
        self.output_dir = output_dir
        self.temp_parent = temp_parent
        self.chunk_size = chunk_size
        self.workers = workers
        self.range_threshold = range_threshold
        self.retries = max(1, retries)
        self.status_path = status_path or output_dir / ".quarkdl-status.json"
        self.status = self._load_status()
        self._pool: _SessionPool | None = None
        self._last_status_save = 0.0

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def __enter__(self) -> ResumableDownloader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _session_pool(self) -> _SessionPool:
        if self._pool is None:
            self._pool = _SessionPool(self.api.session)
        return self._pool

    def _load_status(self) -> dict:
        if self.status_path.exists():
            try:
                return json.loads(self.status_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"items": {}, "updated_at": None}

    def _save_status(self) -> None:
        self.status["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_path.with_suffix(self.status_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.status_path)
        self._last_status_save = time.monotonic()

    def _save_status_throttled(self) -> None:
        if time.monotonic() - self._last_status_save >= STATUS_SAVE_INTERVAL:
            self._save_status()

    def download_many(
        self, items: list[DownloadItem], progress: ProgressCallback | None = None
    ) -> list[tuple[DownloadItem, str]]:
        """Download every item; a failing file no longer aborts the rest.

        Returns the ``(item, error message)`` pairs that failed.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        total = len(items)
        failures: list[tuple[DownloadItem, str]] = []
        for index, item in enumerate(items, 1):
            print(f"[{index}/{total}] {item.path} ({item.size} bytes)")
            try:
                self.download_one(item, progress=progress)
            except (DownloadError, QuarkApiError, requests.RequestException, ValueError) as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                failures.append((item, str(exc)))
                self._save_status()
        return failures

    def download_one(self, item: DownloadItem, progress: ProgressCallback | None = None) -> Path:
        if item.is_dir:
            raise DownloadError("download_one expects a file, not a directory")
        rel_path = sanitize_rel_path(item.path, self.temp_parent)
        rel_key = rel_path.as_posix()
        expected = int(item.size)
        dest = self.output_dir / rel_path
        part = dest.with_name(dest.name + ".part")
        chunks_dir = dest.with_name(dest.name + ".chunks")
        dest.parent.mkdir(parents=True, exist_ok=True)

        record = self.status["items"].setdefault(
            rel_key, {"fid": item.fid, "expected_size": expected, "status": "pending"}
        )
        if dest.exists() and dest.stat().st_size == expected:
            record.update({"status": "completed", "bytes": expected})
            self._save_status()
            if progress:
                progress(item, expected, expected)
            return dest
        if dest.exists() and dest.stat().st_size != expected:
            dest.rename(dest.with_name(dest.name + f".size_mismatch_{dest.stat().st_size}"))

        if expected == 0:
            # Nothing to fetch; avoids a pointless URL request and an empty-file edge case.
            dest.write_bytes(b"")
            record.update({"status": "completed", "bytes": 0})
            self._save_status()
            if progress:
                progress(item, 0, 0)
            return dest

        info = self.api.get_download_info(item.fid)
        urls = _UrlProvider(self.api, item.fid, info["download_url"])
        if expected >= self.range_threshold:
            self._download_ranged(urls, item, dest, part, chunks_dir, record, progress)
        else:
            self._download_stream(urls, item, dest, part, record, progress)
        return dest

    def _download_stream(
        self,
        urls: _UrlProvider,
        item: DownloadItem,
        dest: Path,
        part: Path,
        record: dict,
        progress: ProgressCallback | None,
    ) -> None:
        expected = int(item.size)
        url = urls.current()
        if progress:
            progress(item, part.stat().st_size if part.exists() else 0, expected)

        last_error = "unknown"
        complete = False
        for attempt in range(1, self.retries + 1):
            existing = part.stat().st_size if part.exists() else 0
            if existing > expected:
                part.unlink()
                existing = 0
            if existing == expected:
                complete = True
                break

            headers = _cdn_headers()
            if existing:
                headers["Range"] = f"bytes={existing}-"
            retry_after: float | None = None
            try:
                with self.api.session.get(
                    url, headers=headers, stream=True, timeout=(30, 180)
                ) as response:
                    code = response.status_code
                    if code in AUTH_EXPIRED_CODES:
                        last_error = f"HTTP {code} (download link expired?)"
                        url = urls.refresh(url)
                    elif code not in (200, 206):
                        last_error = f"HTTP {code}"
                        retry_after = _retry_after(response)
                    else:
                        if existing and code == 200:  # server ignored Range; restart the file
                            existing = 0
                        written = existing
                        with part.open("ab" if existing else "wb") as fh:
                            for block in response.iter_content(chunk_size=1024 * 1024):
                                if not block:
                                    continue
                                fh.write(block)
                                written += len(block)
                                record.update({"status": "downloading", "bytes": written})
                                if progress:
                                    progress(item, written, expected)
                                self._save_status_throttled()
                        if part.stat().st_size == expected:
                            complete = True
                            break
                        last_error = f"size {part.stat().st_size}, expected {expected}"
            except (requests.RequestException, QuarkApiError) as exc:
                last_error = str(exc)
            if attempt < self.retries:
                _sleep_backoff(attempt, retry_after)

        if not complete:
            self._save_status()
            raise DownloadError(f"download failed for {item.path}: {last_error}")
        part.replace(dest)
        record.update({"status": "completed", "bytes": expected})
        self._save_status()

    def _download_ranged(
        self,
        urls: _UrlProvider,
        item: DownloadItem,
        dest: Path,
        part: Path,
        chunks_dir: Path,
        record: dict,
        progress: ProgressCallback | None,
    ) -> None:
        expected = int(item.size)
        if part.exists():
            part.unlink()
        chunks_dir.mkdir(parents=True, exist_ok=True)
        ranges = plan_ranges(expected, self.chunk_size)
        pending = []
        done_bytes = 0
        for idx, (start, end) in enumerate(ranges):
            chunk_path = chunks_dir / f"{idx:06d}.part"
            chunk_len = end - start + 1
            if chunk_path.exists() and chunk_path.stat().st_size == chunk_len:
                done_bytes += chunk_len
            else:
                pending.append((idx, start, end, chunk_path))
        record.update(
            {
                "status": "downloading",
                "bytes": done_bytes,
                "chunks_done": len(ranges) - len(pending),
                "chunks_total": len(ranges),
            }
        )
        self._save_status()
        if progress:
            progress(item, done_bytes, expected)

        pool = self._session_pool()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.workers, max(1, len(pending)))
        ) as executor:
            futures = {
                executor.submit(
                    _download_chunk,
                    pool,
                    urls,
                    start,
                    end,
                    chunk_path,
                    item.path,
                    self.retries,
                ): (idx, start, end)
                for idx, start, end, chunk_path in pending
            }
            completed = len(ranges) - len(pending)
            try:
                for future in concurrent.futures.as_completed(futures):
                    done_bytes += future.result()
                    completed += 1
                    record.update(
                        {
                            "status": "downloading",
                            "bytes": done_bytes,
                            "chunks_done": completed,
                            "chunks_total": len(ranges),
                        }
                    )
                    if progress:
                        progress(item, done_bytes, expected)
                    self._save_status_throttled()
            except BaseException:
                # Don't make the user wait for the rest of the file once one chunk is hopeless.
                for future in futures:
                    future.cancel()
                self._save_status()
                raise
        _assemble(chunks_dir, part, ranges)
        if part.stat().st_size != expected:
            raise DownloadError(
                f"size mismatch for {item.path}: got {part.stat().st_size}, expected {expected}"
            )
        part.replace(dest)
        shutil.rmtree(chunks_dir, ignore_errors=True)
        record.update({"status": "completed", "bytes": expected, "chunks_done": len(ranges)})
        self._save_status()


def _cdn_headers() -> dict[str, str]:
    return {
        "User-Agent": PC_UA,
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
    }


def _retry_after(response: requests.Response) -> float | None:
    """Parse a numeric ``Retry-After`` header (seconds), capped to something sane."""
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER)
    except ValueError:
        return None


def _sleep_backoff(attempt: int, retry_after: float | None = None) -> None:
    """Sleep with exponential backoff and jitter, or as long as the server asked."""
    if retry_after is not None:
        time.sleep(retry_after)
        return
    time.sleep(min(2**attempt, 12) * (0.5 + random.random() / 2))


def _download_chunk(
    pool: _SessionPool,
    urls: _UrlProvider,
    start: int,
    end: int,
    chunk_path: Path,
    display_path: str,
    retries: int = DEFAULT_RETRIES,
) -> int:
    expected = end - start + 1
    if chunk_path.exists() and chunk_path.stat().st_size == expected:
        return expected
    tmp = chunk_path.with_suffix(chunk_path.suffix + ".tmp")
    headers = _cdn_headers()
    headers["Range"] = f"bytes={start}-{end}"
    url = urls.current()
    last_error = "unknown"
    for attempt in range(1, retries + 1):
        retry_after: float | None = None
        try:
            with pool.acquire() as session:
                with session.get(url, headers=headers, stream=True, timeout=(25, 180)) as response:
                    code = response.status_code
                    if code == 206:
                        written = 0
                        with tmp.open("wb") as fh:
                            for block in response.iter_content(chunk_size=256 * 1024):
                                if not block:
                                    continue
                                fh.write(block)
                                written += len(block)
                        if written == expected:
                            tmp.replace(chunk_path)
                            return written
                        last_error = f"chunk size {written}, expected {expected}"
                        tmp.unlink(missing_ok=True)
                    elif code in AUTH_EXPIRED_CODES:
                        last_error = f"HTTP {code} (download link expired?)"
                        url = urls.refresh(url)
                    else:
                        last_error = f"HTTP {code}"
                        retry_after = _retry_after(response)
        except (requests.RequestException, QuarkApiError) as exc:
            last_error = str(exc)
            tmp.unlink(missing_ok=True)
        if attempt < retries:
            _sleep_backoff(attempt, retry_after)
    raise DownloadError(f"chunk failed for {display_path} bytes={start}-{end}: {last_error}")


def _assemble(chunks_dir: Path, part: Path, ranges: list[tuple[int, int]]) -> None:
    tmp = part.with_suffix(part.suffix + ".assembling")
    with tmp.open("wb") as out:
        for idx, (start, end) in enumerate(ranges):
            chunk_path = chunks_dir / f"{idx:06d}.part"
            expected = end - start + 1
            if not chunk_path.exists() or chunk_path.stat().st_size != expected:
                raise DownloadError(f"missing or invalid chunk {idx}")
            with chunk_path.open("rb") as fh:
                shutil.copyfileobj(fh, out, length=1024 * 1024)
    tmp.replace(part)
