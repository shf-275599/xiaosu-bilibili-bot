from __future__ import annotations

import time

import structlog

from bilibili_bot.events import Event, CommentEvent
from bilibili_bot.sources.base import BaseSource

logger = structlog.get_logger()


class OwnDynamicCommentSource(BaseSource):
    def __init__(self, config):
        self.config = config
        self.dynamic_page_size = config.sources.own_dynamic.dynamic_page_size
        self.comment_page_size = config.sources.own_dynamic.comment_page_size

    def fetch(self) -> list[Event]:
        from bilibili_bot.client import BilibiliSession
        client = BilibiliSession(self.config.cookie.cookies_file, self.config.bot.request_timeout_seconds)

        dynamics = self._fetch_dynamics(client)
        events = []

        for dynamic in dynamics[:self.dynamic_page_size]:
            dynamic_id = dynamic.get("id_str", "")
            try:
                comments = self._fetch_comments(client, dynamic_id, dynamic)
                for comment in comments[:self.comment_page_size]:
                    event = self._normalize_comment(comment, dynamic_id, dynamic)
                    if event:
                        events.append(event)
            except Exception as e:
                logger.warning("dynamic_comments_failed", dynamic_id=dynamic_id, error=str(e))

        return events

    def _fetch_dynamics(self, client) -> list[dict]:
        resp = client.get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params={"host_mid": client.get_cookies().get("DedeUserID", "")},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            return []

        items = data.get("data", {}).get("items", [])
        return [item for item in items if item.get("type") in ("DYNAMIC_TYPE_AV", "DYNAMIC_TYPE_DRAW")]

    def _fetch_comments(self, client, dynamic_id: str, dynamic: dict) -> list[dict]:
        dynamic_type = dynamic.get("type", "")
        if dynamic_type == "DYNAMIC_TYPE_AV":
            comment_type = 1
            modules = dynamic.get("modules", {})
            dynamic_module = modules.get("module_dynamic", {})
            major = dynamic_module.get("major", {})
            archive = major.get("archive", {})
            aid = archive.get("aid", 0)
            oid = aid
        else:
            comment_type = 11
            oid = dynamic_id

        resp = client.get(
            "https://api.bilibili.com/x/v2/reply",
            params={"type": comment_type, "oid": oid, "pn": 1, "ps": self.comment_page_size},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            return []

        return data.get("data", {}).get("replies", []) or []

    def _normalize_comment(self, reply: dict, dynamic_id: str, dynamic: dict) -> CommentEvent | None:
        member = reply.get("member", {})
        content = reply.get("content", {})

        dynamic_type = dynamic.get("type", "")
        if dynamic_type == "DYNAMIC_TYPE_AV":
            business_type = "video"
            modules = dynamic.get("modules", {})
            dynamic_module = modules.get("module_dynamic", {})
            major = dynamic_module.get("major", {})
            archive = major.get("archive", {})
            oid = str(archive.get("aid", dynamic_id))
            video_title = archive.get("title", "")
            dynamic_desc = ""
        else:
            business_type = "dynamic_draw"
            oid = dynamic_id
            video_title = ""
            modules = dynamic.get("modules", {})
            dynamic_module = modules.get("module_dynamic", {})
            desc = dynamic_module.get("desc", {})
            dynamic_desc = desc.get("text", "")

        parent_content = ""
        parent_reply = reply.get("parent_reply")
        if parent_reply and isinstance(parent_reply, dict):
            parent_content = parent_reply.get("content", {}).get("message", "")

        return CommentEvent(
            source_type="own_dynamic",
            event_key=f"{business_type}:{oid}:{reply.get('rpid')}",
            created_at=reply.get("ctime", 0),
            raw_payload=reply,
            business_type=business_type,
            oid=oid,
            rpid=str(reply.get("rpid", "")),
            root_rpid=str(reply.get("root", "")),
            parent_rpid=str(reply.get("parent", "")),
            author_mid=str(member.get("mid", "")),
            author_name=member.get("uname", ""),
            content_text=content.get("message", ""),
            at_me=False,
            video_title=video_title or dynamic_desc,
            parent_content=parent_content,
        )
