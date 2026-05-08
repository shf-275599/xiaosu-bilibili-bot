from __future__ import annotations

import structlog

from bilibili_bot.events import Event, CommentEvent, DMEvent
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult

logger = structlog.get_logger()


def build_comment_messages(event: CommentEvent, config) -> list[dict[str, str]]:
    business_labels = {"video": "视频", "dynamic": "动态", "dynamic_draw": "图文动态"}
    business_label = business_labels.get(event.business_type, event.business_type)

    parts = [f"来源：{business_label}"]

    if event.video_title:
        parts.append(f"内容标题：{event.video_title}")
    if event.bvid:
        parts.append(f"视频BV号：{event.bvid}")

    if event.parent_content:
        parts.append(f"被回复的评论：{event.parent_content}")

    parts.append(f"是否@我：{'是' if event.at_me else '否'}")
    parts.append(f"评论作者：{event.author_name}")
    parts.append(f"评论内容：{event.content_text}")
    parts.append("")
    parts.append(
        f"请直接生成一条适合在B站公开回复的中文回复。要求：自然、友好、简洁，"
        f"不超过 {config.ai.max_reply_chars} 个汉字，不要解释自己，"
        f"不要输出多版本，不要加引号。"
    )

    return [
        {"role": "system", "content": config.reply.system_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]


def build_dm_messages(event: DMEvent, config) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": config.reply.system_prompt},
    ]

    if event.recent_messages:
        for hist in event.recent_messages[-5:]:
            messages.append({"role": "user" if hist["role"] == "user" else "assistant", "content": hist["content"]})

    messages.append({
        "role": "user",
        "content": f"用户 {event.talker_name} 发来最新私信：{event.content}",
    })

    return messages


class AIGenerateStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        if isinstance(event, CommentEvent):
            messages = build_comment_messages(event, context.config)
        elif isinstance(event, DMEvent):
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
        return StageResult.CONTINUE


def _generate_reply_with_tools(context, messages):
    """尝试带 tool calling 的生成，失败则降级为普通生成。"""
    from bilibili_bot.providers.openai_compat import OpenAICompatibleProvider
    from bilibili_bot.tools import TOOL_DEFINITIONS, execute_tool

    primary = context.providers.primary

    if isinstance(primary, OpenAICompatibleProvider) and context.config.ai.tools_enabled:
        try:
            return primary.generate_with_tools(
                messages,
                TOOL_DEFINITIONS,
                execute_tool,
                max_iterations=context.config.ai.tool_max_iterations,
            )
        except Exception as e:
            logger.warning("tools_generation_failed", error=str(e))

    return context.providers.generate_reply(messages)
