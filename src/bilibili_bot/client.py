from __future__ import annotations

import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bilibili_bot.cookie_store import CookieStore
from bilibili_bot.wbi import enc_wbi

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

WBI_KEYS_URL = "https://api.bilibili.com/x/web-interface/nav"


def _default_headers(cookie_store: CookieStore) -> dict[str, str]:
    return {
        "Cookie": cookie_store.get_header(),
        "User-Agent": USER_AGENT,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }


class BilibiliSession(requests.Session):
    def __init__(self, cookie_store: CookieStore, timeout: int = 25):
        super().__init__()
        self._cookie_store = cookie_store
        self._timeout = timeout
        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_keys_at: float = 0
        self._setup_retry()

    def _setup_retry(self) -> None:
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self.mount("https://", adapter)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("headers", {}).update(_default_headers(self._cookie_store))
        kwargs.setdefault("timeout", self._timeout)
        return super().request(method, url, **kwargs)

    def get_wbi_keys(self) -> tuple[str, str]:
        now = time.time()
        if self._wbi_keys and now - self._wbi_keys_at < 1800:
            return self._wbi_keys

        resp = self.get(WBI_KEYS_URL)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"获取 WBI 密钥失败: {data.get('message')}")
        nav_data = data.get("data")
        if not isinstance(nav_data, dict) or "wbi_img" not in nav_data:
            raise RuntimeError("WBI 密钥响应数据不完整")

        img_key = nav_data["wbi_img"]["img_url"].rsplit("/", 1)[-1].split(".")[0]
        sub_key = nav_data["wbi_img"]["sub_url"].rsplit("/", 1)[-1].split(".")[0]

        self._wbi_keys = (img_key, sub_key)
        self._wbi_keys_at = now
        return self._wbi_keys

    def sign_wbi(self, params: dict[str, Any]) -> dict[str, Any]:
        img_key, sub_key = self.get_wbi_keys()
        return enc_wbi(params, img_key, sub_key)

    def get_cookie(self, key: str, default: str = "") -> str:
        return self._cookie_store.get(key, default)

    def get_cookies(self) -> dict[str, str]:
        return self._cookie_store.get_all()

    def post_dynamic(self, content: str) -> tuple[bool, str]:
        """发布纯文字动态。"""
        csrf = self.get_cookie("bili_jct", "")
        if not csrf:
            return False, "缺少 bili_jct"
        try:
            resp = self.post(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create",
                data={
                    "dynamic_id": 0,
                    "type": 4,
                    "rid": 0,
                    "content": content,
                    "csrf": csrf,
                    "csrf_token": csrf,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                return True, "发送成功"
            return False, f"发送失败 code={data.get('code')} msg={data.get('message')}"
        except Exception as e:
            return False, f"请求异常: {e}"
