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


_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


def _clean_component(part: str) -> str:
    """Make one path component safe on Windows (and harmless elsewhere)."""
    cleaned = "".join(
        "_" if ord(ch) < 32 or ord(ch) == 127 or ch in _WINDOWS_INVALID_CHARS else ch
        for ch in part
    )
    # Windows silently drops trailing dots/spaces, which can collide or fail.
    cleaned = cleaned.rstrip(" .")
    if cleaned and cleaned.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned


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
        cleaned = _clean_component(part)
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


def parse_rate_limit(value: str) -> int:
    """Parse strings like '5M', '500K', '10MB/s', '1048576' into bytes per second."""
    raw = value.strip().upper()
    if not raw:
        raise ValueError("rate limit cannot be empty")
    if raw.endswith("B/S") or raw.endswith("BPS"):
        raw = raw[:-3].rstrip("/")
    elif raw.endswith("/S"):
        raw = raw[:-2]

    multiplier = 1
    if raw.endswith("KIB") or raw.endswith("KB"):
        multiplier = 1024
        raw = raw.rstrip("KIB").rstrip("KB")
    elif raw.endswith("MIB") or raw.endswith("MB"):
        multiplier = 1024 * 1024
        raw = raw.rstrip("MIB").rstrip("MB")
    elif raw.endswith("GIB") or raw.endswith("GB"):
        multiplier = 1024 * 1024 * 1024
        raw = raw.rstrip("GIB").rstrip("GB")
    elif raw.endswith("K"):
        multiplier = 1024
        raw = raw[:-1]
    elif raw.endswith("M"):
        multiplier = 1024 * 1024
        raw = raw[:-1]
    elif raw.endswith("G"):
        multiplier = 1024 * 1024 * 1024
        raw = raw[:-1]
    elif raw.endswith("B"):
        multiplier = 1
        raw = raw[:-1]

    try:
        rate = float(raw)
    except ValueError as exc:
        raise ValueError(f"invalid rate limit: {value!r}") from exc
    if rate <= 0:
        raise ValueError("rate limit must be > 0")
    return int(rate * multiplier)
