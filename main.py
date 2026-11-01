from __future__ import annotations

import sys
from pathlib import Path

# Ensure src/ is on sys.path so it runs immediately without pip install or venv
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from quarkdl.api import QuarkApi  # noqa: E402
from quarkdl.browser import BrowserLoginError, ensure_quark_login  # noqa: E402
from quarkdl.cli import DEFAULT_PROFILE, command_list, main  # noqa: E402


def interactive_wizard() -> int:
    print("=" * 60)
    print("                 Quark Cloud Downloader")
    print("=" * 60)
    try:
        url = input("\nEnter Quark share URL (or press Enter to exit): ").strip()
        if not url or url.lower() in {"q", "quit", "exit"}:
            return 0
        print("\nChecking login...")
        cookies = ensure_quark_login(DEFAULT_PROFILE)
        api = QuarkApi.from_playwright_cookies(cookies)
        return command_list(api, url)
    except (BrowserLoginError, KeyboardInterrupt, ValueError, RuntimeError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # If run with no arguments: interactive wizard (prompt URL -> list -> download)
    if len(sys.argv) == 1:
        raise SystemExit(interactive_wizard())

    # If run with just a URL: `python main.py https://pan.quark.cn/s/...`
    if len(sys.argv) == 2 and sys.argv[1].startswith(("http://", "https://")):
        url = sys.argv[1]
        try:
            cookies = ensure_quark_login(DEFAULT_PROFILE)
            api = QuarkApi.from_playwright_cookies(cookies)
            raise SystemExit(command_list(api, url))
        except (BrowserLoginError, KeyboardInterrupt, ValueError, RuntimeError) as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            raise SystemExit(1) from None

    # Standard CLI invocation: `python main.py list ...`, `python main.py download ...`
    raise SystemExit(main())
