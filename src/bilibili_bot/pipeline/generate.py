from __future__ import annotations

import structlog
from datetime import datetime, timedelta, timezone

from bilibili_bot.events import Event, CommentEvent, DMEvent
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult

CST = timezone(timedelta(hours=8))

logger = structlog.get_logger()


def summarize_conversation(summary_text: str, provider) -> str:
    """调用 LLM 生成对话摘要，失败时返回空字符串。"""
    try:
        messages = [
            {"role": "system", "content": "你是一个对话摘要助手。用一句中文简洁总结以下对话的内容和当前话题。"},
            {"role": "user", "content": f"对话历史:\n{summary_text}"},
        ]
        result = provider.generate(messages)
        if result.success and result.text:
            return result.text[:100]
    except Exception as e:
        logger.warning("summarize_failed", error=str(e))
    return ""


def build_comment_messages(event: CommentEvent, config) -> list[dict[str, str]]:
    business_labels = {"video": "视频", "dynamic": "动态", "dynamic_draw": "图文动态"}
    business_label = business_labels.get(event.business_type, event.business_type)

    now = datetime.now(CST)
    parts = [f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}", f"来源：{business_label}"]

    if event.video_title:
        label = "动态内容" if event.business_type in ("dynamic", "dynamic_draw") else "内容标题"
        parts.append(f"{label}：{event.video_title}")
    if event.bvid and event.business_type == "video":
        parts.append(f"视频BV号：{event.bvid}")
    if event.video_desc:
        parts.append(f"视频简介：{event.video_desc}")
    if event.thread_context:
        parts.append(f"对话上下文：{event.thread_context}")
    if event.author_follower:
        parts.append("注：对方是你的粉丝")

    if event.author_level > 0:
        parts.append(f"用户等级：Lv{event.author_level}")
    if event.author_fans_count > 0:
        parts.append(f"粉丝数：{event.author_fans_count}")
    if event.interaction_count > 0:
        parts.append(f"历史互动次数：{event.interaction_count}")

    if event.video_view_count > 0:
        parts.append(f"视频播放量：{event.video_view_count}")
    if event.video_like_count > 0:
        parts.append(f"视频点赞数：{event.video_like_count}")
    if event.video_favorite_count > 0:
        parts.append(f"视频收藏数：{event.video_favorite_count}")
    if event.up_name:
        parts.append(f"UP主：{event.up_name}")

    if event.conversation_summary:
        parts.append(f"对话背景摘要：{event.conversation_summary}")
    if event.recent_replies:
        parts.append("历史对话：")
        for hist in event.recent_replies[-10:]:
            role_label = "我" if hist["role"] == "bot" else "对方"
            parts.append(f"  {role_label}：{hist['content'][:100]}")

    if event.parent_content:
        parts.append(f"被回复的评论：{event.parent_content}")

    parts.append(f"是否@我：{'是' if event.at_me else '否'}")
    parts.append(f"评论作者：{event.author_name}")
    parts.append(f"评论内容：{event.content_text}")
    parts.append("")
    parts.append("请直接生成一条适合在B站公开回复的中文回复。")

    return [
        {"role": "system", "content": config.reply.system_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]


def build_dm_messages(event: DMEvent, config) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.reply.system_prompt},
    ]

    if event.conversation_summary:
        messages.append({"role": "system", "content": f"对话背景摘要：{event.conversation_summary}"})

    if event.recent_messages:
        for hist in event.recent_messages[-15:]:
            messages.append({"role": "user" if hist["role"] == "user" else "assistant", "content": hist["content"]})

    now = datetime.now(CST)
    messages.append({
        "role": "user",
        "content": f"当前时间：{now.strftime('%Y年%m月%d日 %H:%M')}\n用户 {event.talker_name} 发来最新私信：{event.content}",
    })

    return messages


class AIGenerateStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        if isinstance(event, CommentEvent):
            messages = build_comment_messages(event, context.config)
        elif isinstance(event, DMEvent):
            if event.conversation_summary and len(event.recent_messages) > 15:
                event.conversation_summary = summarize_conversation(
                    event.conversation_summary, context.providers.primary
                )
            messages = build_dm_messages(event, context.config)
        else:
            logger.error("unknown_event_type", event_type=type(event).__name__)
            return StageResult.SKIP

        reply = _generate_reply_with_tools(context, messages)

        if not reply.success:
            logger.error("generate_failed", event_key=event.event_key, error=reply.error)
            context.dedup.mark_failed(event, reply.error, reply.provider)
            context.rate_limiter.record_failure(reply.retriable)
            return StageResult.HALT

        context.reply_text = reply.text
        context.provider_used = reply.provider
        context.tool_calls = reply.tool_calls
        return StageResult.CONTINUE


def _generate_reply_with_tools(context, messages):
    """尝试带 tool calling 的生成，失败则降级为普通生成。"""
    from bilibili_bot.providers.openai_compat import OpenAICompatibleProvider

    primary = context.providers.primary

    if isinstance(primary, OpenAICompatibleProvider) and context.config.ai.tools_enabled:
        try:
            return primary.generate_with_tools(messages)
        except Exception as e:
            logger.warning("tools_generation_failed", error=str(e))

    return context.providers.generate_reply(messages)
