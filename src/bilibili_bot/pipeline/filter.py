from __future__ import annotations

import re

import structlog

from bilibili_bot.events import Event, CommentEvent
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult

logger = structlog.get_logger()

PURE_SYMBOL_RE = re.compile(r"[\W_]+")


class FilterStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        if not isinstance(event, CommentEvent):
            return StageResult.CONTINUE

        config = context.config.filters

        if config.skip_self:
            my_uid = context.client.get_cookies().get("DedeUserID", "")
            if event.author_mid == my_uid:
                logger.debug("skip_self", event_key=event.event_key)
                return StageResult.SKIP

        if event.author_mid in [str(mid) for mid in config.blacklist_mids]:
            logger.debug("skip_blacklist", event_key=event.event_key, mid=event.author_mid)
            return StageResult.SKIP

        if config.skip_empty and not event.content_text.strip():
            logger.debug("skip_empty", event_key=event.event_key)
            return StageResult.SKIP

        if config.skip_pure_emoji:
            cleaned = PURE_SYMBOL_RE.sub("", event.content_text)
            if not cleaned:
                logger.debug("skip_pure_emoji", event_key=event.event_key)
                return StageResult.SKIP

        if len(event.content_text.strip()) < config.min_meaningful_length:
            logger.debug("skip_short", event_key=event.event_key, length=len(event.content_text))
            return StageResult.SKIP

        if config.followed_only and not event.author_follower:
            logger.debug("skip_not_follower", event_key=event.event_key, author_mid=event.author_mid)
            return StageResult.SKIP

        if context.auto_skip and context.auto_skip.should_skip(event.author_mid, event.source_type):
            logger.info(
                "skip_auto_skip",
                event_key=event.event_key,
                author_mid=event.author_mid,
                source_type=event.source_type,
            )
            return StageResult.SKIP

        return StageResult.CONTINUE
