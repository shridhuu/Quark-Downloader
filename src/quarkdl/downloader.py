from __future__ import annotations

import concurrent.futures
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import requests

from .api import PC_UA, QuarkApi
from .core import DownloadItem, plan_ranges, sanitize_rel_path

ProgressCallback = Callable[[DownloadItem, int, int], None]


class DownloadError(RuntimeError):
    pass


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
        status_path: Path | None = None,
    ) -> None:
        self.api = api
        self.output_dir = output_dir
        self.temp_parent = temp_parent
        self.chunk_size = chunk_size
        self.workers = workers
        self.range_threshold = range_threshold
        self.status_path = status_path or output_dir / ".quarkdl-status.json"
        self.status = self._load_status()

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

    def download_many(
        self, items: list[DownloadItem], progress: ProgressCallback | None = None
    ) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        total = len(items)
        for index, item in enumerate(items, 1):
            print(f"[{index}/{total}] {item.path} ({item.size} bytes)")
            self.download_one(item, progress=progress)

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
            return dest
        if dest.exists() and dest.stat().st_size != expected:
            dest.rename(dest.with_name(dest.name + f".size_mismatch_{dest.stat().st_size}"))

        info = self.api.get_download_info(item.fid)
        url = info["download_url"]
        if expected >= self.range_threshold:
            self._download_ranged(url, item, dest, part, chunks_dir, record, progress)
        else:
            self._download_stream(url, item, dest, part, record, progress)
        return dest

    def _download_stream(
        self,
        url: str,
        item: DownloadItem,
        dest: Path,
        part: Path,
        record: dict,
        progress: ProgressCallback | None,
    ) -> None:
        expected = int(item.size)
        existing = part.stat().st_size if part.exists() else 0
        mode = "ab" if existing else "wb"
        headers = _cdn_headers()
        if 0 < existing < expected:
            headers["Range"] = f"bytes={existing}-"
        elif existing >= expected:
            part.replace(dest)
            record.update({"status": "completed", "bytes": expected})
            self._save_status()
            return

        with self.api.session.get(url, headers=headers, stream=True, timeout=(30, 180)) as response:
            if existing > 0 and response.status_code == 200:
                existing = 0
                mode = "wb"
            elif response.status_code not in (200, 206):
                raise DownloadError(f"CDN returned HTTP {response.status_code} for {item.path}")
            written = existing
            with part.open(mode) as fh:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    fh.write(block)
                    written += len(block)
                    record.update({"status": "downloading", "bytes": written})
                    if progress:
                        progress(item, written, expected)
        if part.stat().st_size != expected:
            raise DownloadError(
                f"size mismatch for {item.path}: got {part.stat().st_size}, expected {expected}"
            )
        part.replace(dest)
        record.update({"status": "completed", "bytes": expected})
        self._save_status()

    def _download_ranged(
        self,
        url: str,
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

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(self.workers, max(1, len(pending)))
        ) as executor:
            futures = {
                executor.submit(
                    _download_chunk,
                    self.api.session,
                    url,
                    start,
                    end,
                    chunk_path,
                    item.path,
                ): (idx, start, end)
                for idx, start, end, chunk_path in pending
            }
            completed = len(ranges) - len(pending)
            for future in concurrent.futures.as_completed(futures):
                idx, start, end = futures[future]
                got = future.result()
                done_bytes += got
                completed += 1
                record.update(
                    {
                        "status": "downloading",
                        "bytes": done_bytes,
                        "chunks_done": completed,
                        "chunks_total": len(ranges),
                    }
                )
                if progress and (completed % 16 == 0 or completed == len(ranges)):
                    progress(item, done_bytes, expected)
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


def _copy_session(source: requests.Session) -> requests.Session:
    session = requests.Session()
    session.cookies.update(source.cookies)
    return session


def _download_chunk(
    source_session: requests.Session,
    url: str,
    start: int,
    end: int,
    chunk_path: Path,
    display_path: str,
    retries: int = 4,
) -> int:
    expected = end - start + 1
    if chunk_path.exists() and chunk_path.stat().st_size == expected:
        return expected
    tmp = chunk_path.with_suffix(chunk_path.suffix + ".tmp")
    headers = _cdn_headers()
    headers["Range"] = f"bytes={start}-{end}"
    last_error = "unknown"
    for attempt in range(1, retries + 1):
        try:
            session = _copy_session(source_session)
            with session.get(url, headers=headers, stream=True, timeout=(25, 180)) as response:
                if response.status_code != 206:
                    last_error = f"HTTP {response.status_code}"
                    time.sleep(min(2**attempt, 12))
                    continue
                written = 0
                with tmp.open("wb") as fh:
                    for block in response.iter_content(chunk_size=256 * 1024):
                        if not block:
                            continue
                        fh.write(block)
                        written += len(block)
                if written != expected:
                    last_error = f"chunk size {written}, expected {expected}"
                    tmp.unlink(missing_ok=True)
                    time.sleep(min(2**attempt, 12))
                    continue
                tmp.replace(chunk_path)
                return written
        except Exception as exc:  # pragma: no cover - network-specific retry path
            last_error = str(exc)
            tmp.unlink(missing_ok=True)
            time.sleep(min(2**attempt, 12))
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
