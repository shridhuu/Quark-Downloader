from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ShareLink:
    pwd_id: str
    passcode: str = ""


@dataclass(frozen=True)
class DownloadItem:
    fid: str
    path: str
    size: int
    is_dir: bool = False
    name: str = ""
    pdir_fid: str = ""


def parse_share_url(url: str) -> ShareLink:
    parsed = urlparse(url.strip())
    if parsed.netloc not in {"pan.quark.cn", "www.pan.quark.cn"}:
        raise ValueError("Expected a Quark share link from pan.quark.cn/s/...")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "s" or not parts[1]:
        raise ValueError("Expected a Quark share link like https://pan.quark.cn/s/<id>")

    passcode = ""
    for source in (
        parsed.query,
        parsed.fragment.split("?", 1)[1] if "?" in parsed.fragment else "",
    ):
        if not source:
            continue
        qs = parse_qs(source)
        passcode = (qs.get("pwd") or qs.get("passcode") or [""])[0]
        if passcode:
            break
    return ShareLink(pwd_id=parts[1], passcode=passcode)


def sanitize_rel_path(path: str, temp_parent: str | None = None) -> Path:
    normalized = path.replace("\\", "/")
    if temp_parent:
        prefix = temp_parent.strip("/") + "/"
        if normalized == temp_parent.strip("/"):
            normalized = ""
        elif normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]

    parts: list[str] = []
    temp_component = temp_parent.strip("/") if temp_parent else None
    for raw in normalized.split("/"):
        part = raw.strip()
        if not part or part in {".", ".."} or (temp_component and part == temp_component):
            continue
        cleaned = "".join("_" if ord(ch) < 32 or ord(ch) == 127 else ch for ch in part)
        if cleaned:
            parts.append(cleaned)
    if not parts:
        raise ValueError(f"path has no usable file name: {path!r}")
    return Path(*parts)


def plan_ranges(size: int, chunk_size: int) -> list[tuple[int, int]]:
    if size < 0:
        raise ValueError("size must be >= 0")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < size:
        end = min(start + chunk_size - 1, size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def select_items(
    items: list[DownloadItem],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[DownloadItem]:
    includes = include or ["**"]
    excludes = exclude or []
    selected: list[DownloadItem] = []
    for item in items:
        if item.is_dir:
            continue
        path = item.path.replace("\\", "/")
        if not any(fnmatchcase(path, pattern) for pattern in includes):
            continue
        if any(fnmatchcase(path, pattern) for pattern in excludes):
            continue
        selected.append(item)
    return selected


def human_bytes(value: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} B"
            return f"{amount:.1f} {unit}"
        amount /= 1024
