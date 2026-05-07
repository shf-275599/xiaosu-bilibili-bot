#!/usr/bin/env python3
"""Bilibili 私信去重服务。"""

from __future__ import annotations

from dm_source import DMEvent
from state_store import JsonlStateStore


class DMDedupService:
    def __init__(self, store: JsonlStateStore):
        self.store = store

    def already_handled(self, event: DMEvent, include_dry_run: bool = False) -> bool:
        state = self.store.load_state()
        handled = state.get("handled_dm", {})
        key = event.event_key()

        if key not in handled:
            return False

        record = handled[key]
        if include_dry_run:
            return record.get("status") in ("replied", "dry_run")

        return record.get("status") == "replied"

    def mark_seen(self, event: DMEvent, reason: str) -> None:
        state = self.store.load_state()
        if "handled_dm" not in state:
            state["handled_dm"] = {}

        state["handled_dm"][event.event_key()] = {
            "status": "skipped",
            "reason": reason,
            "talker_id": event.talker_id,
            "talker_name": event.talker_name,
            "content": event.content,
            "msg_key": event.msg_key,
        }
        self.store.save_state(state)

    def mark_replied(self, event: DMEvent, reply_text: str, provider: str) -> None:
        state = self.store.load_state()
        if "handled_dm" not in state:
            state["handled_dm"] = {}

        state["handled_dm"][event.event_key()] = {
            "status": "replied",
            "reply_text": reply_text,
            "provider": provider,
            "talker_id": event.talker_id,
            "talker_name": event.talker_name,
            "content": event.content,
            "msg_key": event.msg_key,
        }
        self.store.save_state(state)

    def mark_dry_run(self, event: DMEvent, reply_text: str, provider: str) -> None:
        state = self.store.load_state()
        if "handled_dm" not in state:
            state["handled_dm"] = {}

        state["handled_dm"][event.event_key()] = {
            "status": "dry_run",
            "reply_text": reply_text,
            "provider": provider,
            "talker_id": event.talker_id,
            "talker_name": event.talker_name,
            "content": event.content,
            "msg_key": event.msg_key,
        }
        self.store.save_state(state)

    def mark_failed(self, event: DMEvent, reason: str, provider: str = "") -> None:
        state = self.store.load_state()
        if "handled_dm" not in state:
            state["handled_dm"] = {}

        state["handled_dm"][event.event_key()] = {
            "status": "failed",
            "reason": reason,
            "provider": provider,
            "talker_id": event.talker_id,
            "talker_name": event.talker_name,
            "content": event.content,
            "msg_key": event.msg_key,
        }
        self.store.save_state(state)
