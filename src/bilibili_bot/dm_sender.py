#!/usr/bin/env python3
"""Bilibili 私信发送模块。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import requests

from bot_config import build_cookie_header, parse_cookies_file, random_user_agent


SEND_MSG_URL = "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg"


@dataclass
class SendResult:
    success: bool
    message: str = ""
    retriable: bool = False


def send_dm(receiver_id: int, content: str, config: dict) -> SendResult:
    cookies_file = config["cookie"]["cookies_file"]
    cookies = parse_cookies_file(cookies_file)
    my_uid = int(cookies.get("DedeUserID", 0))
    bili_jct = cookies.get("bili_jct", "")

    if not my_uid:
        return SendResult(success=False, message="无法获取发送者 UID")
    if not bili_jct:
        return SendResult(success=False, message="无法获取 bili_jct")

    headers = {
        "User-Agent": random_user_agent(),
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": build_cookie_header(cookies),
    }

    timestamp = int(time.time())
    msg_json = json.dumps({"content": content}, ensure_ascii=False)

    data = {
        "msg[sender_uid]": my_uid,
        "msg[receiver_id]": receiver_id,
        "msg[receiver_type]": 1,
        "msg[msg_type]": 1,
        "msg[msg_status]": 0,
        "msg[dev_id]": "B0CB5998-CE3C-4069-8B15-5C4F5B7A3A3D",
        "msg[timestamp]": timestamp,
        "msg[new_face_version]": 0,
        "msg[content]": msg_json,
        "csrf_token": bili_jct,
        "csrf": bili_jct,
    }

    timeout = config["bot"].get("request_timeout_seconds", 25)

    try:
        response = requests.post(SEND_MSG_URL, headers=headers, data=data, timeout=timeout)
        response.raise_for_status()
        result = response.json()

        if result.get("code") != 0:
            msg = result.get("message", "未知错误")
            return SendResult(success=False, message=f"发送失败: {msg}", retriable=True)

        return SendResult(success=True, message="发送成功")

    except requests.RequestException as e:
        return SendResult(success=False, message=f"请求异常: {e}", retriable=True)
