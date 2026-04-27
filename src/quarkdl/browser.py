from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class BrowserLoginError(RuntimeError):
    pass


def ensure_quark_login(
    profile_dir: Path,
    *,
    headless: bool = False,
    timeout_seconds: int = 180,
    keep_open: bool = False,
) -> list[dict[str, Any]]:
    """Open a persistent browser profile and return authenticated cookies.

    The browser is deliberately official-site-first: users scan Quark's own QR code in the
    opened page. No password, SMS code, cookie, token, or generated URL is requested.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://pan.quark.cn/list#/list/all", wait_until="domcontentloaded")

            deadline = time.time() + timeout_seconds
            announced = False
            while time.time() < deadline:
                if _logged_in(page):
                    return context.cookies()
                if not announced:
                    print(
                        "If a Quark login QR is visible in the browser, "
                        "scan it with the official Quark app."
                    )
                    announced = True
                time.sleep(2)
            raise BrowserLoginError("Timed out waiting for Quark QR login")
        finally:
            if keep_open:
                print("Browser left open; press Ctrl+C in this process to close it.")
                try:
                    while True:
                        time.sleep(3600)
                except KeyboardInterrupt:
                    pass
            context.close()


def _logged_in(page: Any) -> bool:
    try:
        result = page.evaluate(
            """
            async () => {
              try {
                const url = 'https://pan.quark.cn/account/info?fr=pc&platform=pc';
                const r = await fetch(url, {credentials:'include'});
                const j = await r.json();
                return !!(j && j.data && j.data.nickname);
              } catch (e) {
                return false;
              }
            }
            """
        )
        if result:
            return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        return False
    return False
