from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .api import QuarkApi, QuarkApiError, ShareEntry
from .browser import BrowserLoginError, ensure_quark_login
from .core import (
    DownloadItem,
    ShareLink,
    human_bytes,
    parse_index_selection,
    parse_rate_limit,
    parse_share_url,
    sanitize_rel_path,
    select_items,
)
from .downloader import DEFAULT_RETRIES, DownloadError, ResumableDownloader
from .progress import ProgressReporter

DEFAULT_PROFILE = Path.home() / ".quarkdl" / "browser-profile"


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quarkdl",
        description=(
            "Download authorized Quark Cloud Drive shares with QR login "
            "and resumable ranges."
        ),
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE,
        help="Playwright browser profile directory",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (not useful for first QR login)",
    )
    parser.add_argument(
        "--login-timeout", type=int, default=180, help="Seconds to wait for QR login"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List a share and optionally download items")
    list_parser.add_argument("url", help="https://pan.quark.cn/s/... share link")

    download = sub.add_parser("download", help="Download a share")
    download.add_argument("url", help="https://pan.quark.cn/s/... share link")
    download.add_argument(
        "-o", "--output", type=Path, default=Path("downloads"), help="Output directory"
    )
    download.add_argument(
        "--yes", action="store_true", help="Do not prompt; download all selected files"
    )
    download.add_argument(
        "--pick",
        action="store_true",
        help="Interactively choose individual files instead of root entries",
    )
    download.add_argument(
        "--include", action="append", default=[], help="Include glob, repeatable (e.g. '**/*.zip')"
    )
    download.add_argument("--exclude", action="append", default=[], help="Exclude glob, repeatable")
    download.add_argument(
        "--workers", type=_positive_int, default=32, help="Concurrent range workers per file"
    )
    download.add_argument(
        "--parallel-files",
        type=_positive_int,
        default=1,
        help="Number of files to download concurrently (default: %(default)s)",
    )
    download.add_argument(
        "--chunk-size", type=_positive_int, default=1024 * 1024, help="Range chunk size in bytes"
    )
    download.add_argument(
        "--retries",
        type=_positive_int,
        default=DEFAULT_RETRIES,
        help="Attempts per chunk/file before giving up (default: %(default)s)",
    )
    download.add_argument(
        "--limit-rate",
        type=str,
        default=None,
        help="Maximum download speed, e.g. '5M', '500K', '10MB/s' (default: unlimited)",
    )
    download.add_argument(
        "--keep-cloud", action="store_true", help="Keep temporary folder in your Quark drive"
    )
    download.add_argument("--temp-folder", default="", help="Temporary cloud folder name")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cookies = ensure_quark_login(
            args.profile_dir,
            headless=args.headless,
            timeout_seconds=args.login_timeout,
        )
        api = QuarkApi.from_playwright_cookies(cookies)
        if args.command == "list":
            return command_list(api, args.url)
        if args.command == "download":
            return command_download(api, args)
        return 2
    except (BrowserLoginError, QuarkApiError, DownloadError, KeyboardInterrupt, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def command_list(api: QuarkApi, url: str) -> int:
    link = parse_share_url(url)
    stoken = api.get_stoken(link.pwd_id, link.passcode)
    root_entries = api.list_share_folder(link.pwd_id, stoken)
    all_entries = api.walk_share(link.pwd_id, stoken)
    files = [entry for entry in all_entries if not entry.is_dir]
    folders = [entry for entry in all_entries if entry.is_dir]
    total_size = human_bytes(sum(file.size for file in files))
    print(
        f"\nShare {link.pwd_id}: {len(folders)} folders, "
        f"{len(files)} files, {total_size}\n"
    )
    for idx, entry in enumerate(files, 1):
        print(f"  [{idx:>3}] {human_bytes(entry.size):>10}  {entry.path}")

    # In a non-interactive shell/pipe, just return cleanly
    if not sys.stdin.isatty() or not files:
        return 0

    print()
    raw = input(
        "Download from this list? (e.g. 1-3, 5, 'all', or press Enter to exit): "
    ).strip()
    if not raw:
        return 0

    indexes = parse_index_selection(raw, len(files))
    if not indexes:
        print("Nothing selected.")
        return 0

    selected_file_paths = {entry.path for idx, entry in enumerate(files, 1) if idx in indexes}
    selected_roots = roots_covering_file_paths(root_entries, selected_file_paths)
    out_dir_str = input("Download directory [downloads]: ").strip()
    output_dir = Path(out_dir_str) if out_dir_str else Path("downloads")

    download_args = argparse.Namespace(
        url=url,
        output=output_dir,
        yes=True,
        pick=False,
        include=[],
        exclude=[],
        workers=32,
        parallel_files=1,
        chunk_size=1024 * 1024,
        retries=DEFAULT_RETRIES,
        limit_rate=None,
        keep_cloud=False,
        temp_folder="",
    )
    return _execute_download(api, link, stoken, selected_roots, selected_file_paths, download_args)


def command_download(api: QuarkApi, args: argparse.Namespace) -> int:
    link = parse_share_url(args.url)
    stoken = api.get_stoken(link.pwd_id, link.passcode)
    root_entries = api.list_share_folder(link.pwd_id, stoken)
    all_entries = api.walk_share(link.pwd_id, stoken)
    all_files = [entry for entry in all_entries if not entry.is_dir]
    print(
        f"Share {link.pwd_id}: {len([e for e in all_entries if e.is_dir])} folders, "
        f"{len(all_files)} files, {human_bytes(sum(e.size for e in all_files))}"
    )

    selected_file_paths: set[str] | None = None
    if args.yes:
        selected_roots = root_entries
    elif args.pick:
        selected_file_paths = _prompt_file_selection(all_files)
        selected_roots = roots_covering_file_paths(root_entries, selected_file_paths)
    else:
        selected_roots = _prompt_root_selection(root_entries)
    if not selected_roots:
        print("Nothing selected.")
        return 0

    return _execute_download(api, link, stoken, selected_roots, selected_file_paths, args)


def _execute_download(
    api: QuarkApi,
    link: ShareLink,
    stoken: str,
    selected_roots: list[ShareEntry],
    selected_file_paths: set[str] | None,
    args: argparse.Namespace,
) -> int:
    temp_name = args.temp_folder or f"quarkdl-{link.pwd_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    existing_folders = {item.name: item.fid for item in api.list_drive_folder("0") if item.is_dir}
    if temp_name in existing_folders:
        temp_fid = existing_folders[temp_name]
        print(f"Reusing existing cloud folder: {temp_name}")
    else:
        temp_fid = api.create_folder(temp_name)
        print(f"Temporary cloud folder created: {temp_name}")

    failures: list[tuple[DownloadItem, str]] = [
        (DownloadItem(fid="placeholder", path="", size=0), "interrupted")
    ]
    try:
        task_id = api.save_share_entries(link.pwd_id, stoken, selected_roots, temp_fid)
        print("Saving share into your Quark drive...")
        api.poll_task(task_id, "save share")
        print("Save completed; listing saved files...")
        drive_items = api.walk_drive_folder(temp_fid, temp_name)
        files = [item for item in drive_items if not item.is_dir]
        if selected_file_paths is not None:
            # Downloaded paths are sanitized (e.g. ':' -> '_'), so compare in sanitized form.
            selected_rel_paths = {sanitize_rel_path(p).as_posix() for p in selected_file_paths}
            files = [
                item
                for item in files
                if sanitize_rel_path(item.path, temp_name).as_posix() in selected_rel_paths
            ]
        files = select_items(files, include=args.include or None, exclude=args.exclude or None)
        if not files:
            print("No files matched the selection.")
            failures.clear()
            return 0
        total_size = sum(item.size for item in files)
        print(f"Downloading {len(files)} files ({human_bytes(total_size)}) to {args.output}")
        if not args.yes and not _confirm("Proceed with download?", default=True):
            failures.clear()
            return 0

        rate_limit_bps = parse_rate_limit(args.limit_rate) if args.limit_rate else None
        reporter = ProgressReporter(total_bytes=total_size)
        downloader = ResumableDownloader(
            api,
            args.output,
            temp_parent=temp_name,
            chunk_size=args.chunk_size,
            workers=args.workers,
            parallel_files=args.parallel_files,
            retries=args.retries,
            rate_limit=rate_limit_bps,
            on_bytes=reporter.record_bytes,
        )

        def _connection_status() -> str:
            text = f"conn {downloader.active_connections}/{args.workers}"
            if downloader.slow_restarts:
                text += f" (slow restarts {downloader.slow_restarts})"
            return text

        reporter.set_extra(_connection_status)
        try:
            failures = downloader.download_many(files, progress=reporter)
        finally:
            downloader.close()
        print(reporter.summary())
        if failures:
            print(f"{len(failures)} file(s) failed:", file=sys.stderr)
            for item, error in failures:
                print(f"  {item.path}: {error}", file=sys.stderr)
            print(
                "Re-run the same command to resume; completed files and chunks are kept.",
                file=sys.stderr,
            )
            return 1
        _verify_output(args.output, temp_name, files)
        print("All files downloaded and byte-size verified.")
        return 0
    finally:
        if args.keep_cloud or failures:
            if failures and not args.keep_cloud:
                print(
                    f"Cloud folder preserved for resume: {temp_name} "
                    f"(use --temp-folder {temp_name} on next run)"
                )
            else:
                print(f"Temporary cloud folder kept: {temp_name}")
        else:
            print("Deleting temporary cloud folder...")
            try:
                api.delete_file(temp_fid)
                print("Temporary cloud folder deleted.")
            except Exception as exc:  # pragma: no cover - network cleanup best-effort
                print(f"Warning: cloud cleanup failed; delete manually: {temp_name}. Detail: {exc}")


def _prompt_root_selection(entries: list[ShareEntry]) -> list[ShareEntry]:
    print("Root entries:")
    for idx, entry in enumerate(entries, 1):
        kind = "folder" if entry.is_dir else "file"
        size = "-" if entry.is_dir else human_bytes(entry.size)
        print(f"  {idx:>2}. {kind:6} {size:>10}  {entry.file_name}")
    raw = input("Select entries (all, or numbers/ranges like 1,3,5-8) [all]: ").strip()
    indexes = parse_index_selection(raw, len(entries))
    return [entry for idx, entry in enumerate(entries, 1) if idx in indexes]


def _prompt_file_selection(entries: list[ShareEntry]) -> set[str]:
    files = [entry for entry in entries if not entry.is_dir]
    print("Files:")
    for idx, entry in enumerate(files, 1):
        print(f"  {idx:>3}. {human_bytes(entry.size):>10}  {entry.path}")
    raw = input("Select files (all, or numbers/ranges like 1,3,5-8) [all]: ").strip()
    indexes = parse_index_selection(raw, len(files))
    return {entry.path for idx, entry in enumerate(files, 1) if idx in indexes}


def roots_covering_file_paths(
    root_entries: list[ShareEntry], selected_file_paths: set[str]
) -> list[ShareEntry]:
    selected: list[ShareEntry] = []
    for entry in root_entries:
        if entry.is_dir:
            prefix = entry.path.rstrip("/") + "/"
            if any(path.startswith(prefix) for path in selected_file_paths):
                selected.append(entry)
        elif entry.path in selected_file_paths:
            selected.append(entry)
    return selected


def _confirm(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"{question} {suffix} ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _verify_output(output: Path, temp_parent: str, items: list[DownloadItem]) -> None:
    missing = []
    mismatched = []
    for item in items:
        rel = sanitize_rel_path(item.path, temp_parent)
        path = output / rel
        if not path.exists():
            missing.append(rel.as_posix())
        elif path.stat().st_size != item.size:
            mismatched.append((rel.as_posix(), item.size, path.stat().st_size))
    if missing or mismatched:
        raise RuntimeError(
            f"verification failed: missing={len(missing)} mismatched={len(mismatched)}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
