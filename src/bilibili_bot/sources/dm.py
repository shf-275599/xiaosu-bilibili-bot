from __future__ import annotations

import structlog

from bilibili_bot.events import Event, DMEvent
from bilibili_bot.sources.base import BaseSource

logger = structlog.get_logger()


class DMSource(BaseSource):
    def __init__(self, config):
        self.config = config
        self.max_reply_per_round = config.sources.dm.max_reply_per_round
        self.whitelist_mids = config.sources.dm.whitelist_mids

    def fetch(self) -> list[Event]:
        return self.fetch_new_messages()

    def fetch_new_messages(self) -> list[Event]:
        from bilibili_bot.client import BilibiliSession
        client = BilibiliSession(self.config.cookie.cookies_file, self.config.bot.request_timeout_seconds)

        my_uid = client.get_cookies().get("DedeUserID", "")
        if not my_uid:
            logger.error("dm_no_deduid")
            return []

        sessions = self._fetch_sessions(client)
        if not sessions:
            logger.debug("dm_no_sessions")
            return []

        logger.debug("dm_sessions_fetched", count=len(sessions))
        events = []

        for session in sessions:
            if len(events) >= self.max_reply_per_round:
                break

            talker_id = session.get("talker_id", 0)
            unread_count = session.get("unread_count", 0)

            if unread_count == 0:
                logger.debug("dm_skip_no_unread", talker_id=talker_id)
                continue

            if str(talker_id) == my_uid:
                logger.debug("dm_skip_self_session", talker_id=talker_id)
                continue

            try:
                messages = self._fetch_messages(client, talker_id)
                messages = [m for m in messages if isinstance(m, dict)]
                logger.debug("dm_messages_fetched", talker_id=talker_id, count=len(messages))

                recent, summary = _build_recent_history(messages, my_uid)

                for msg in messages:
                    sender_uid = msg.get("sender_uid", 0)
                    if str(sender_uid) == my_uid:
                        continue

                    event = self._normalize_message(msg, session, my_uid)
                    if event is None:
                        continue

                    event.recent_messages = recent
                    event.conversation_summary = summary or ""

                    logger.info(
                        "dm_event_found",
                        event_key=event.event_key,
                        talker_id=event.talker_id,
                        content=event.content[:50],
                    )
                    events.append(event)
                    break

            except Exception as e:
                logger.warning("dm_fetch_failed", talker_id=talker_id, error=str(e))

        logger.info("dm_events_total", count=len(events))
        return events

    def _fetch_sessions(self, client) -> list[dict]:
        resp = client.get(
            "https://api.vc.bilibili.com/session_svr/v1/session_svr/get_sessions",
            params={"session_type": 1, "size": 20, "build": 0, "mobi_app": "web"},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.warning("dm_sessions_api_error", code=data.get("code"), message=data.get("message", ""))
            return []

        return data.get("data", {}).get("session_list", [])

    def _fetch_messages(self, client, talker_id: int) -> list[dict]:
        resp = client.get(
            "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs",
            params={"talker_id": talker_id, "size": 60},
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.warning("dm_messages_api_error", talker_id=talker_id, code=data.get("code"), message=data.get("message", ""))
            return []

        return data.get("data", {}).get("messages", []) or []

    def _normalize_message(self, msg: dict, session: dict, my_uid: str) -> DMEvent | None:
        """将私信消息规范化为 DMEvent。

        关键设计：
        - talker_id 使用 session 中的 talker_id（对话对方），而不是 sender_uid（消息发送者）
        - 这样无论谁发了消息，reply 目标都是对话对方
        - 由调用方负责过滤 sender_uid == my_uid 的自发自消息
        """
        msg_type = msg.get("msg_type", 0)
        if msg_type != 1:
            return None

        content_str = msg.get("content", "{}")
        try:
            import json
            content_data = json.loads(content_str)
            if isinstance(content_data, dict):
                text = content_data.get("content", "")
            else:
                text = str(content_data)
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = str(content_str)

        if not text.strip():
            return None

        # 使用 session 的 talker_id 而不是 sender_uid
        session_talker_id = session.get("talker_id", 0)

        return DMEvent(
            source_type="dm",
            event_key=f"dm:{msg.get('sender_uid')}:{msg.get('msg_key')}",
            created_at=msg.get("timestamp", 0),
            raw_payload=msg,
            talker_id=session_talker_id,
            talker_name=session.get("account_info", {}).get("name", ""),
            dm_content=text,
            msg_type=msg_type,
            msg_key=msg.get("msg_key", 0),
        )


def _build_recent_history(messages: list, my_uid: str) -> tuple[list[dict], str | None]:
    """返回 (recent, summary_text)。超过 20 条时 summary_text 包含最老 15 条的格式化文本。"""
    recent = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender_uid", 0))
        msg_type = msg.get("msg_type", 0)
        content_str = msg.get("content", "{}")

        if msg_type == 7:
            # B站视频/动态分享：提取 title + bvid 注入上下文
            try:
                import json as _json
                share_data = _json.loads(content_str)
                if isinstance(share_data, dict):
                    bvid = share_data.get("bvid", "")
                    title = share_data.get("title", "")
                    author_name = share_data.get("author", "")
                    if bvid and title:
                        text = f"[分享了视频：《{title}》({bvid})，UP主：{author_name}]" if author_name else f"[分享了视频：《{title}》({bvid})]"
                    elif bvid:
                        text = f"[分享了视频 {bvid}]"
                    else:
                        text = "[分享了视频]"
                else:
                    text = "[分享了视频]"
            except Exception:
                text = "[分享了视频]"
        else:
            try:
                import json as _json2
                content_data = _json2.loads(content_str)
                if isinstance(content_data, dict):
                    text = content_data.get("content", "")
                else:
                    text = str(content_data)
            except Exception:
                text = str(content_str)

        if text.strip() and not text.startswith("📊 今日报告"):
            recent.append({
                "role": "bot" if sender == my_uid else "user",
                "content": text[:200],
            })

    recent.reverse()  # 转为正序（最老在前），LLM 按时间顺序理解

    summary_text = None
    if len(recent) > 30:
        oldest_15 = recent[:15]  # 取最老的 15 条（正序后前 15 条即最老）
        lines = []
        for item in oldest_15:
            role_label = "我" if item["role"] == "bot" else "对方"
            lines.append(f"{role_label}: {item['content']}")
        summary_text = "\n".join(lines)

    return recent, summary_text
