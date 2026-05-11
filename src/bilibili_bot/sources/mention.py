from __future__ import annotations

import structlog

from bilibili_bot.events import Event, CommentEvent, BUSINESS_TYPE_MAP
from bilibili_bot.sources.msgfeed import MsgFeedReplySource

logger = structlog.get_logger()


class MentionMsgFeedSource(MsgFeedReplySource):
    def __init__(self, config):
        super().__init__(config)
        self.page_size = config.sources.mention.page_size

    def fetch(self) -> list[Event]:
        from bilibili_bot.client import BilibiliSession
        client = BilibiliSession(self.config.cookie.cookies_file, self.config.bot.request_timeout_seconds)

        resp = client.get(
            "https://api.bilibili.com/x/msgfeed/at",
            params={"platform": "web", "build": 0, "mobi_app": "web"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.error("mention_failed", code=data.get("code"), message=data.get("message"))
            return []

        items = data.get("data", {}).get("items", [])
        events = []

        for item in items[:self.page_size]:
            try:
                event = self._normalize_item(item)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning("normalize_failed", error=str(e))

        if events:
            self._enrich_events(events, client)
        return events

    def _normalize_item(self, item: dict) -> CommentEvent | None:
        user = item.get("user", {})
        item_data = item.get("item", {})

        business_id = item_data.get("business_id", 1)
        business_type = BUSINESS_TYPE_MAP.get(business_id, "video")

        # 提取楼中楼上下文：target_reply_content 是用户回复的那条评论
        target_content = item_data.get("target_reply_content", "")[:200] if item_data.get("target_reply_content") else ""

        # 非视频事件（动态/图文）的标题直接从 msgfeed 取，不走后续 enrichment
        item_title = ""
        if business_type != "video":
            item_title = (item_data.get("title", "") or "")[:500]

        return CommentEvent(
            source_type="mention",
            event_key=f"{business_type}:{item_data.get('subject_id')}:{item_data.get('source_id')}",
            created_at=item.get("reply_time", 0),
            raw_payload=item,
            business_type=business_type,
            oid=str(item_data.get("subject_id", "")),
            rpid=str(item_data.get("source_id", "")),
            root_rpid=str(item_data.get("root_id", "")),
            parent_rpid=str(item_data.get("source_id", "")),
            author_mid=str(user.get("mid", "")),
            author_name=user.get("nickname", ""),
            content_text=item_data.get("source_content", ""),
            at_me=True,
            bvid="",
            parent_content=target_content,
            video_title=item_title,
        )
