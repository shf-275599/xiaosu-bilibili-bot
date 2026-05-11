from __future__ import annotations

import re
import structlog

from bilibili_bot.events import Event, CommentEvent, BUSINESS_TYPE_MAP
from bilibili_bot.sources.base import BaseSource

logger = structlog.get_logger()

# 动态标题缓存：opus_id → title（mention 事件写入，reply 事件读取）
_dynamic_title_cache: dict[str, str] = {}


def _extract_dynamic_id(uri: str) -> str:
    """从 B站动态 URI 中提取动态 ID。
    opus格式: 'https://www.bilibili.com/opus/1197036032889978896' → '1197036032889978896'
    t格式:    'https://t.bilibili.com/381040221372962034' → '381040221372962034'
    """
    m = re.search(r"opus/(\d+)", uri)
    if m:
        return m.group(1)
    m = re.search(r"t\.bilibili\.com/(\d+)", uri)
    if m:
        return m.group(1)
    return ""


def _extract_opus_text(opus_data: dict) -> str:
    """从 opus/detail API 响应中提取文字（modules 为数组格式，含 MODULE_TYPE_CONTENT）。"""
    item = (opus_data.get("item", {}) or {})
    modules = item.get("modules", []) or []
    for mod in modules:
        if mod.get("module_type") == "MODULE_TYPE_CONTENT":
            content = mod.get("module_content", {}) or {}
            paragraphs = content.get("paragraphs", []) or []
            parts = []
            for para in paragraphs:
                text_node = para.get("text", {}) or {}
                for node in (text_node.get("nodes", []) or []):
                    word = node.get("word", {}) or {}
                    w = word.get("words", "")
                    if w:
                        parts.append(w)
            return "".join(parts)
    return ""


class MsgFeedReplySource(BaseSource):
    def __init__(self, config):
        self.config = config
        self.page_size = config.sources.msgfeed.page_size

    def fetch(self) -> list[Event]:
        from bilibili_bot.client import BilibiliSession
        client = BilibiliSession(self.config.cookie.cookies_file, self.config.bot.request_timeout_seconds)

        resp = client.get(
            "https://api.bilibili.com/x/msgfeed/reply",
            params={"platform": "web", "build": 0, "mobi_app": "web"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.error("msgfeed_failed", code=data.get("code"), message=data.get("message"))
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

        self._enrich_events(events, client)
        return events

    def _enrich_events(self, events: list[CommentEvent], client) -> None:
        cache: dict[str, dict] = {}
        for event in events:
            if not event.oid:
                continue

            if event.business_type == "video":
                need_bvid = not event.bvid
                need_title = not event.video_title
                if not need_bvid and not need_title:
                    continue

                oid = event.oid
                if oid not in cache:
                    try:
                        resp = client.get(
                            "https://api.bilibili.com/x/web-interface/view",
                            params={"aid": oid},
                        )
                        data = resp.json()
                        if data.get("code") == 0:
                            cache[oid] = data.get("data", {})
                    except Exception as e:
                        logger.debug("event_enrich_failed", oid=oid, error=str(e))

                info = cache.get(oid, {})
                if info:
                    if need_bvid:
                        event.bvid = info.get("bvid", "")
                    if need_title:
                        event.video_title = info.get("title", "")
                    if not event.video_desc and info.get("desc"):
                        event.video_desc = info["desc"][:200]

                    stat = info.get("stat", {})
                    event.video_view_count = stat.get("view", 0)
                    event.video_like_count = stat.get("like", 0)
                    event.video_favorite_count = stat.get("favorite", 0)

                    owner = info.get("owner", {})
                    event.up_name = owner.get("name", "")

            elif event.business_type == "article":
                if event.video_title:
                    continue

                uri = (event.raw_payload.get("item", {}) or {}).get("uri", "")
                if not uri:
                    uri = (event.raw_payload.get("uri", "") or "")
                dynamic_id = _extract_dynamic_id(uri)

                # 从 detail API 中提取真实文章 ID
                real_article_id = ""
                if dynamic_id:
                    if dynamic_id not in cache:
                        try:
                            resp = client.get(
                                "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail",
                                params={"id": dynamic_id, "timezone_offset": -480},
                            )
                            data = resp.json()
                            if data.get("code") == 0:
                                cache[dynamic_id] = data.get("data", {})
                        except Exception as e:
                            logger.debug("article_detail_failed", dynamic_id=dynamic_id, error=str(e))

                    detail_data = cache.get(dynamic_id, {})
                    if detail_data:
                        item_d = (detail_data.get("item", {}) or {})
                        modules_d = (item_d.get("modules", {}) or {})
                        dyn_d = (modules_d.get("module_dynamic", {}) or {})
                        major_d = dyn_d.get("major", {}) or {}
                        article_info = major_d.get("article", {}) or {}
                        real_article_id = str(article_info.get("id", ""))

                if not real_article_id:
                    real_article_id = event.oid

                if real_article_id not in cache:
                    try:
                        resp = client.get(
                            "https://api.bilibili.com/x/article/view",
                            params={"id": real_article_id},
                        )
                        data = resp.json()
                        if data.get("code") == 0:
                            cache[real_article_id] = data.get("data", {})
                    except Exception as e:
                        logger.debug("article_enrich_failed", oid=real_article_id, error=str(e))

                info = cache.get(real_article_id, {})
                if info:
                    event.video_title = info.get("title", "") or ""
                    summary = info.get("summary", "") or ""
                    if summary and summary != event.video_title:
                        event.video_desc = summary[:500]

            elif event.business_type in ("dynamic", "dynamic_draw"):
                uri = (event.raw_payload.get("item", {}) or {}).get("uri", "")
                if not uri:
                    uri = (event.raw_payload.get("uri", "") or "")
                dynamic_id = _extract_dynamic_id(uri)
                if not dynamic_id:
                    continue

                is_opus = "opus/" in uri

                # mention 源：将标题写入缓存（拒绝转发动态的无效标题）
                if event.source_type == "mention" and event.video_title and event.video_title not in ("转发动态", "分享动态"):
                    _dynamic_title_cache[dynamic_id] = event.video_title
                    continue

                # reply 源：先查缓存
                if dynamic_id in _dynamic_title_cache:
                    event.video_title = _dynamic_title_cache[dynamic_id]
                    continue

                # 缓存没有，调详情 API
                if dynamic_id not in cache:
                    try:
                        resp = client.get(
                            "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail",
                            params={"id": dynamic_id, "timezone_offset": -480},
                        )
                        data = resp.json()
                        if data.get("code") == 0:
                            cache[dynamic_id] = data.get("data", {})
                    except Exception as e:
                        logger.debug("dynamic_enrich_failed", dynamic_id=dynamic_id, error=str(e))

                    if is_opus:
                        opus_cache_key = f"opus_{dynamic_id}"
                        try:
                            resp = client.get(
                                "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail",
                                params={"id": dynamic_id, "timezone_offset": -480},
                            )
                            data = resp.json()
                            if data.get("code") == 0:
                                cache[opus_cache_key] = data.get("data", {})
                        except Exception as e:
                            logger.debug("opus_enrich_failed", dynamic_id=dynamic_id, error=str(e))

                info = cache.get(dynamic_id, {})
                if info:
                    item = (info.get("item", {}) or {})
                    modules = (item.get("modules", {}) or {})
                    dyn = (modules.get("module_dynamic", {}) or {})

                    if is_opus:
                        opus_cache_key = f"opus_{dynamic_id}"
                        opus_info = cache.get(opus_cache_key, {})
                        text = _extract_opus_text(opus_info) if opus_info else ""
                        if text:
                            event.video_title = text[:500]
                            _dynamic_title_cache[dynamic_id] = event.video_title
                    else:
                        desc = (dyn.get("desc", {}) or {})
                        text = (desc.get("text", "") or "").strip()
                        if not text and item.get("type") == "DYNAMIC_TYPE_FORWARD":
                            orig = item.get("orig", {}) or {}
                            om = (orig.get("modules", {}) or {}).get("module_dynamic", {}) or {}
                            od = (om.get("desc", {}) or {})
                            text = (od.get("text", "") or "").strip()
                        if text:
                            event.video_title = text[:500]
                            _dynamic_title_cache[dynamic_id] = event.video_title

                    # 提取图片 URL（统一从 detail API 取，含转发动态）
                    major = dyn.get("major", {}) or {}
                    draw = major.get("draw", {}) or {}
                    draw_items = draw.get("items", []) or []
                    if draw_items:
                        event.images = [d.get("src", "") for d in draw_items if d.get("src")]
                    if not event.images and item.get("type") == "DYNAMIC_TYPE_FORWARD":
                        orig = item.get("orig", {}) or {}
                        om = (orig.get("modules", {}) or {}).get("module_dynamic", {}) or {}
                        om_draw = (om.get("major", {}) or {}).get("draw", {}) or {}
                        om_items = om_draw.get("items", []) or []
                        if om_items:
                            event.images = [d.get("src", "") for d in om_items if d.get("src")]

        self._enrich_users(events, client)

    def _enrich_users(self, events: list[CommentEvent], client) -> None:
        mids = {e.author_mid for e in events if e.author_mid}
        if not mids:
            return
        for mid in mids:
            try:
                params = client.sign_wbi({"mid": mid})
                resp = client.get(
                    "https://api.bilibili.com/x/space/wbi/acc/info",
                    params=params,
                )
                data = resp.json()
                if data.get("code") == 0:
                    info = data.get("data", {})
                    is_followed = info.get("relation", {}).get("is_followed", 0)
                    level = info.get("level", 0)
                    fans_count = info.get("follower", 0)
                    for e in events:
                        if e.author_mid == mid:
                            if is_followed:
                                e.author_follower = True
                            e.author_level = level
                            e.author_fans_count = fans_count
            except Exception as e:
                logger.debug("user_enrich_failed", mid=mid, error=str(e))

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
            source_type="msgfeed",
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
