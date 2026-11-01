from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .api import WEB_UA


class BrowserLoginError(RuntimeError):
    pass


def _validate_cached_cookies(cookies: list[dict[str, Any]]) -> bool:
    try:
        import requests

        session = requests.Session()
        for cookie in cookies:
            name = cookie.get("name")
            val = cookie.get("value", "")
            domain = cookie.get("domain")
            if name and domain:
                session.cookies.set(name, val, domain=domain, path=cookie.get("path") or "/")
        resp = session.get(
            "https://pan.quark.cn/account/info",
            params={"fr": "pc", "platform": "pc"},
            headers={"User-Agent": WEB_UA, "Origin": "https://pan.quark.cn"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if (data.get("data") or {}).get("nickname"):
                return True
    except Exception:
        pass
    return False


def ensure_quark_login(
    profile_dir: Path,
    *,
    headless: bool = False,
    timeout_seconds: int = 180,
    keep_open: bool = False,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Open a persistent browser profile and return authenticated cookies.

    Checks cached cookies first via account_info to avoid launching browser when valid.
    The browser is deliberately official-site-first: users scan Quark's own QR code in the
    opened page. No password, SMS code, cookie, token, or generated URL is requested.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    cookie_cache_path = profile_dir / "cookies.json"
    if not force_refresh and cookie_cache_path.exists():
        try:
            cached = json.loads(cookie_cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, list) and _validate_cached_cookies(cached):
                return cached
        except Exception:
            pass

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
                    cookies = context.cookies()
                    try:
                        cookie_cache_path.write_text(
                            json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
                    return cookies
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
