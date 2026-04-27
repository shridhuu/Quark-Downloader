from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

import requests

from .core import DownloadItem

WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
    "Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"
)


@dataclass(frozen=True)
class ShareEntry:
    fid: str
    file_name: str
    path: str
    size: int
    is_dir: bool
    pdir_fid: str = "0"
    share_fid_token: str = ""

    def to_download_item(self) -> DownloadItem:
        return DownloadItem(
            fid=self.fid,
            path=self.path,
            size=self.size,
            is_dir=self.is_dir,
            name=self.file_name,
            pdir_fid=self.pdir_fid,
        )


class QuarkApiError(RuntimeError):
    pass


class QuarkApi:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @classmethod
    def from_playwright_cookies(cls, cookies: list[dict[str, Any]]) -> QuarkApi:
        session = requests.Session()
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value", "")
            domain = cookie.get("domain")
            if not name or not domain:
                continue
            session.cookies.set(name, value, domain=domain, path=cookie.get("path") or "/")
        return cls(session)

    @staticmethod
    def _headers(user_agent: str = WEB_UA) -> dict[str, str]:
        return {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://pan.quark.cn",
            "Referer": "https://pan.quark.cn/",
            "accept-language": "zh-CN,zh;q=0.9",
        }

    @staticmethod
    def _params(extra: dict[str, str] | None = None) -> dict[str, str]:
        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        if extra:
            params.update(extra)
        return params

    @staticmethod
    def _now_ms() -> str:
        return str(int(time.time() * 1000))

    def _json(self, response: requests.Response, context: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise QuarkApiError(
                f"{context}: non-JSON response HTTP {response.status_code}"
            ) from exc
        if response.status_code >= 400:
            code = data.get("code")
            message = data.get("message")
            raise QuarkApiError(
                f"{context}: HTTP {response.status_code} code={code} message={message}"
            )
        return data

    def account_info(self) -> dict[str, Any]:
        response = self.session.get(
            "https://pan.quark.cn/account/info",
            params={"fr": "pc", "platform": "pc"},
            headers=self._headers(),
            timeout=30,
        )
        data = self._json(response, "account_info")
        return data.get("data") or {}

    def get_stoken(self, pwd_id: str, passcode: str = "") -> str:
        url = "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token"
        response = self.session.post(
            url,
            params=self._params({"__dt": str(random.randint(400, 9999)), "__t": self._now_ms()}),
            json={"pwd_id": pwd_id, "passcode": passcode},
            headers=self._headers(),
            timeout=60,
        )
        data = self._json(response, "share token")
        stoken = (data.get("data") or {}).get("stoken")
        if not stoken:
            raise QuarkApiError(
                f"share token: code={data.get('code')} message={data.get('message')}"
            )
        return stoken

    def list_share_folder(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str = "0",
        path_prefix: str = "",
        page_size: int = 100,
    ) -> list[ShareEntry]:
        entries: list[ShareEntry] = []
        page = 1
        while True:
            params = self._params(
                {
                    "ver": "2",
                    "pwd_id": pwd_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid,
                    "force": "0",
                    "_page": str(page),
                    "_size": str(page_size),
                    "_fetch_banner": "1" if pdir_fid == "0" and page == 1 else "0",
                    "_fetch_share": "1" if pdir_fid == "0" and page == 1 else "0",
                    "fetch_relate_conversation": "0",
                    "_fetch_total": "1",
                    "_sort": "file_type:asc,file_name:asc",
                }
            )
            response = self.session.get(
                "https://drive-h.quark.cn/1/clouddrive/share/sharepage/detail",
                params=params,
                headers=self._headers(),
                timeout=60,
            )
            data = self._json(response, "share detail")
            if data.get("status") not in (200, None) and data.get("code") != 0:
                raise QuarkApiError(
                    f"share detail: code={data.get('code')} message={data.get('message')}"
                )
            rows = (data.get("data") or {}).get("list") or []
            for row in rows:
                name = row.get("file_name") or row.get("name") or row.get("fid")
                item_path = f"{path_prefix}/{name}" if path_prefix else name
                entries.append(
                    ShareEntry(
                        fid=row["fid"],
                        file_name=name,
                        path=item_path,
                        size=int(row.get("size") or 0),
                        is_dir=bool(row.get("dir")),
                        pdir_fid=pdir_fid,
                        share_fid_token=row.get("share_fid_token") or "",
                    )
                )
            if len(rows) < page_size:
                break
            page += 1
        return entries

    def walk_share(self, pwd_id: str, stoken: str) -> list[ShareEntry]:
        root = self.list_share_folder(pwd_id, stoken)
        all_entries = list(root)
        queue = [entry for entry in root if entry.is_dir]
        while queue:
            folder = queue.pop(0)
            children = self.list_share_folder(pwd_id, stoken, folder.fid, folder.path)
            all_entries.extend(children)
            queue.extend(child for child in children if child.is_dir)
        return all_entries

    def list_root(self) -> list[dict[str, Any]]:
        data = self._list_drive_folder_raw("0", page_size=200)
        return data

    def create_folder(self, name: str, parent_fid: str = "0") -> str:
        response = self.session.post(
            "https://drive-pc.quark.cn/1/clouddrive/file",
            params=self._params({"__dt": str(random.randint(100, 9999)), "__t": self._now_ms()}),
            json={
                "pdir_fid": parent_fid,
                "file_name": name,
                "dir_path": "",
                "dir_init_lock": False,
            },
            headers=self._headers(),
            timeout=60,
        )
        data = self._json(response, "create folder")
        if data.get("code") != 0 and data.get("status") != 200:
            raise QuarkApiError(
                f"create folder: code={data.get('code')} message={data.get('message')}"
            )
        fid = (data.get("data") or {}).get("fid")
        if not fid:
            raise QuarkApiError("create folder: response did not contain fid")
        return fid

    def save_share_entries(
        self,
        pwd_id: str,
        stoken: str,
        entries: list[ShareEntry],
        target_folder_fid: str,
    ) -> str:
        response = self.session.post(
            "https://drive.quark.cn/1/clouddrive/share/sharepage/save",
            params=self._params({"__dt": str(random.randint(600, 9999)), "__t": self._now_ms()}),
            json={
                "fid_list": [entry.fid for entry in entries],
                "fid_token_list": [entry.share_fid_token for entry in entries],
                "to_pdir_fid": target_folder_fid,
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": "0",
                "scene": "link",
            },
            headers=self._headers(),
            timeout=120,
        )
        data = self._json(response, "save share")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise QuarkApiError(
                f"save share: code={data.get('code')} message={data.get('message')}"
            )
        return task_id

    def poll_task(
        self, task_id: str, label: str = "task", retries: int = 100, interval: float = 0.9
    ) -> dict[str, Any]:
        last: dict[str, Any] = {}
        for retry_index in range(retries):
            time.sleep(interval)
            response = self.session.get(
                "https://drive-pc.quark.cn/1/clouddrive/task",
                params=self._params(
                    {
                        "task_id": task_id,
                        "retry_index": str(retry_index),
                        "__dt": "21192",
                        "__t": self._now_ms(),
                    }
                ),
                headers=self._headers(),
                timeout=60,
            )
            data = self._json(response, label)
            last = data
            task_data = data.get("data") or {}
            if data.get("message") == "ok" and task_data.get("status") == 2:
                return task_data
            if data.get("code") not in (0, None) and data.get("status") not in (200, None):
                raise QuarkApiError(
                    f"{label}: code={data.get('code')} message={data.get('message')}"
                )
        raise QuarkApiError(f"{label}: timed out; last={last.get('message')}")

    def _list_drive_folder_raw(self, pdir_fid: str, page_size: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.session.get(
                "https://drive-pc.quark.cn/1/clouddrive/file/sort",
                params=self._params(
                    {
                        "pdir_fid": pdir_fid,
                        "_page": str(page),
                        "_size": str(page_size),
                        "_fetch_total": "true",
                        "_fetch_sub_dirs": "1",
                        "_sort": "file_type:asc,file_name:asc",
                    }
                ),
                headers=self._headers(),
                timeout=60,
            )
            data = self._json(response, "drive folder list")
            if data.get("status") not in (200, None) and data.get("code") != 0:
                raise QuarkApiError(
                    f"drive folder list: code={data.get('code')} message={data.get('message')}"
                )
            batch = (data.get("data") or {}).get("list") or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return rows

    def list_drive_folder(self, pdir_fid: str, path_prefix: str = "") -> list[DownloadItem]:
        items: list[DownloadItem] = []
        rows = self._list_drive_folder_raw(pdir_fid)
        for row in rows:
            name = row.get("file_name") or row.get("fid")
            path = f"{path_prefix}/{name}" if path_prefix else name
            item = DownloadItem(
                fid=row["fid"],
                path=path,
                size=int(row.get("size") or 0),
                is_dir=bool(row.get("dir")),
                name=name,
                pdir_fid=pdir_fid,
            )
            items.append(item)
        return items

    def walk_drive_folder(self, root_fid: str, root_name: str) -> list[DownloadItem]:
        root = self.list_drive_folder(root_fid, root_name)
        all_items = list(root)
        queue = [item for item in root if item.is_dir]
        while queue:
            folder = queue.pop(0)
            children = self.list_drive_folder(folder.fid, folder.path)
            all_items.extend(children)
            queue.extend(child for child in children if child.is_dir)
        return all_items

    def get_download_info(self, fid: str) -> dict[str, Any]:
        response = self.session.post(
            "https://drive-pc.quark.cn/1/clouddrive/file/download",
            params=self._params({"sys": "win32", "ve": "2.5.56", "ut": "", "guid": ""}),
            json={"fids": [fid]},
            headers=self._headers(PC_UA),
            timeout=60,
        )
        data = self._json(response, "download url")
        rows = data.get("data") or []
        if data.get("status") != 200 or not rows or not rows[0].get("download_url"):
            raise QuarkApiError(
                f"download url: code={data.get('code')} message={data.get('message')}"
            )
        return rows[0]

    def delete_file(self, fid: str, retries: int = 100) -> None:
        response = self.session.post(
            "https://drive-pc.quark.cn/1/clouddrive/file/delete",
            params=self._params({"__dt": str(random.randint(100, 9999)), "__t": self._now_ms()}),
            json={"action_type": 2, "filelist": [fid], "exclude_fids": []},
            headers=self._headers(),
            timeout=60,
        )
        data = self._json(response, "delete file")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise QuarkApiError(
                f"delete file: code={data.get('code')} message={data.get('message')}"
            )
        self.poll_task(task_id, "delete file", retries=retries)
