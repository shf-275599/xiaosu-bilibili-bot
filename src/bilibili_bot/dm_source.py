#!/usr/bin/env python3
"""Bilibili 私信会话轮询模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from bot_config import build_cookie_header, parse_cookies_file, random_user_agent


SESSION_LIST_URL = "https://api.vc.bilibili.com/session_svr/v1/session_svr/get_sessions"
SESSION_MSGS_URL = "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"


@dataclass
class DMEvent:
    talker_id: int
    talker_name: str
    content: str
    msg_type: int
    timestamp: int
    msg_key: int

    def event_key(self) -> str:
        return f"dm:{self.talker_id}:{self.msg_key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "talker_id": self.talker_id,
            "talker_name": self.talker_name,
            "content": self.content,
            "msg_type": self.msg_type,
            "timestamp": self.timestamp,
            "msg_key": self.msg_key,
        }


class DMSource:
    def __init__(self, config: dict):
        self.config = config
        self.cookies_file = config["cookie"]["cookies_file"]
        self.timeout = config["bot"].get("request_timeout_seconds", 25)
        self.my_uid = 0

    def _headers(self) -> dict[str, str]:
        cookies = parse_cookies_file(self.cookies_file)
        self.my_uid = int(cookies.get("DedeUserID", 0))
        return {
            "User-Agent": random_user_agent(),
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Cookie": build_cookie_header(cookies),
        }

    def fetch_sessions(self) -> list[dict[str, Any]]:
        headers = self._headers()
        response = requests.get(
            SESSION_LIST_URL,
            headers=headers,
            params={
                "session_type": 1,
                "group_fold_rule": 0,
                "sort_rule": 0,
                "build": 0,
                "mobi_app": "web",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取会话列表失败: {data.get('message')}")
        return data.get("data", {}).get("session_list", [])

    def fetch_messages(self, talker_id: int, size: int = 10) -> list[dict[str, Any]]:
        headers = self._headers()
        response = requests.get(
            SESSION_MSGS_URL,
            headers=headers,
            params={
                "talker_id": talker_id,
                "session_type": 1,
                "size": size,
                "sender_device_id": 1,
                "build": 0,
                "mobi_app": "web",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取私信记录失败: {data.get('message')}")
        return data.get("data", {}).get("messages", [])

    def fetch_new_messages(self) -> list[DMEvent]:
        events = []
        sessions = self.fetch_sessions()

        for session in sessions:
            talker_id = session.get("talker_id", 0)
            talker_name = f"用户{talker_id}"
            unread_count = session.get("unread_count", 0)
            last_msg = session.get("last_msg", {})

            if unread_count <= 0:
                continue

            if last_msg.get("sender_uid") == self.my_uid:
                continue

            msg_type = last_msg.get("msg_type", 1)
            if msg_type != 1:
                continue

            content = ""
            try:
                import json
                msg_content = json.loads(last_msg.get("content", "{}"))
                content = msg_content.get("content", "")
            except Exception:
                content = last_msg.get("content", "")

            if not content:
                continue

            events.append(DMEvent(
                talker_id=talker_id,
                talker_name=talker_name,
                content=content,
                msg_type=msg_type,
                timestamp=last_msg.get("timestamp", 0),
                msg_key=last_msg.get("msg_key", 0),
            ))

        return events
