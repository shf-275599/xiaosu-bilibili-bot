from __future__ import annotations

import time

import structlog

from bilibili_bot.events import Event, CommentEvent
from bilibili_bot.sources.base import BaseSource

logger = structlog.get_logger()


class OwnVideoCommentSource(BaseSource):
    def __init__(self, config):
        self.config = config
        self.video_page_size = config.sources.own_video.video_page_size
        self.comment_page_size = config.sources.own_video.comment_page_size
        self.max_retries = config.sources.own_video.max_retries
        self.retry_sleep = config.sources.own_video.retry_sleep_seconds

    def fetch(self) -> list[Event]:
        from bilibili_bot.client import BilibiliSession
        client = BilibiliSession(self.config.cookie.cookies_file, self.config.bot.request_timeout_seconds)

        my_uid = client.get_cookies().get("DedeUserID", "")
        if not my_uid:
            logger.error("no_deduid")
            return []

        videos = self._fetch_videos(client, my_uid)
        events = []

        for video in videos[:self.video_page_size]:
            bvid = video.get("bvid", "")
            aid = video.get("aid", 0)
            title = video.get("title", "")
            try:
                comments = self._fetch_comments(client, aid)
                for comment in comments[:self.comment_page_size]:
                    event = self._normalize_comment(comment, str(aid), bvid, title)
                    if event:
                        events.append(event)
            except Exception as e:
                logger.warning("video_comments_failed", bvid=bvid, error=str(e))

        return events

    def _fetch_videos(self, client, mid: str) -> list[dict]:
        resp = client.get(
            "https://api.bilibili.com/x/space/arc/search",
            params={"mid": mid, "ps": self.video_page_size, "pn": 1},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            return []

        return data.get("data", {}).get("list", {}).get("vlist", [])

    def _fetch_comments(self, client, aid: int) -> list[dict]:
        for attempt in range(self.max_retries):
            resp = client.get(
                "https://api.bilibili.com/x/v2/reply",
                params={"type": 1, "oid": aid, "pn": 1, "ps": self.comment_page_size},
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") == 0:
                return data.get("data", {}).get("replies", []) or []

            if data.get("code") == -799:
                time.sleep(self.retry_sleep)
                continue

            return []

        return []

    def _normalize_comment(self, reply: dict, aid: str, bvid: str, title: str = "") -> CommentEvent | None:
        member = reply.get("member", {})
        content = reply.get("content", {})

        # 提取父评论内容（楼中楼上下文）
        parent_content = ""
        parent_reply = reply.get("parent_reply")
        if parent_reply and isinstance(parent_reply, dict):
            parent_content = parent_reply.get("content", {}).get("message", "")

        return CommentEvent(
            source_type="own_video",
            event_key=f"video:{aid}:{reply.get('rpid')}",
            created_at=reply.get("ctime", 0),
            raw_payload=reply,
            business_type="video",
            oid=aid,
            rpid=str(reply.get("rpid", "")),
            root_rpid=str(reply.get("root", "")),
            parent_rpid=str(reply.get("parent", "")),
            author_mid=str(member.get("mid", "")),
            author_name=member.get("uname", ""),
            content_text=content.get("message", ""),
            at_me=False,
            video_title=title,
            parent_content=parent_content,
            bvid=bvid,
        )
