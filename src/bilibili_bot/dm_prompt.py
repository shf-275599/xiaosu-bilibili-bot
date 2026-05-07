#!/usr/bin/env python3
"""Bilibili 私信 Prompt 构建模块。"""

from __future__ import annotations

from dm_source import DMEvent


def build_dm_messages(event: DMEvent, config: dict) -> list[dict[str, str]]:
    dm_config = config.get("dm_reply", {})
    system_prompt = dm_config.get("system_prompt", "你是一个友善的B站用户。请根据私信内容生成简短自然的中文回复，不超过100字。")

    user_content = f"用户 {event.talker_name} 发来私信：{event.content}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
