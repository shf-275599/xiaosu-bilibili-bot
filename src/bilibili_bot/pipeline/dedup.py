from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

from bilibili_bot.events import Event
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult
from bilibili_bot.atomic_state import AtomicStateStore

logger = structlog.get_logger()

MAX_RETRIES = 5
RETRY_COOLDOWN_SECONDS = 300
FATAL_COOLDOWN_SECONDS = 3600

FATAL_ERROR_KEYWORDS = ["已经被删除", "已被删除", "不存在", "已关闭", "已过期"]


class DedupStatus(Enum):
    NEW = "new"
    REPLIED = "replied"
    SEEN = "seen"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FATAL = "failed_fatal"


@dataclass
class DedupRecord:
    event_key: str
    status: str
    seen_at: float
    retries: int = 0
    last_retry_at: float = 0
    reason: str = ""
    provider_used: str = ""
    reply_text_hash: int = 0
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class DedupService:
    def __init__(self, store: AtomicStateStore, max_size: int = 50000, ttl_days: int = 7):
        self._store = store
        self._seen: dict[str, DedupRecord] = {}
        self._max_size = max_size
        self._ttl = ttl_days * 86400

    def is_duplicate(self, key: str) -> DedupStatus:
        record = self._store.get_record(key)
        if not record:
            return DedupStatus.NEW

        status = record.get("reply_status")
        if status == "replied":
            return DedupStatus.REPLIED
        if status == "seen":
            return DedupStatus.SEEN

        if status == "failed":
            reason = record.get("reason", "")
            if self._is_fatal_error(reason):
                return DedupStatus.FAILED_FATAL

            retries = record.get("retries", 0)
            if retries >= MAX_RETRIES:
                last_retry = record.get("last_retry_at", 0)
                if time.time() - last_retry < FATAL_COOLDOWN_SECONDS:
                    return DedupStatus.FAILED_FATAL
                return DedupStatus.NEW

            last_retry = record.get("last_retry_at", 0)
            if time.time() - last_retry < RETRY_COOLDOWN_SECONDS:
                return DedupStatus.FAILED_RETRYABLE

            return DedupStatus.NEW

        return DedupStatus.NEW

    def mark_seen(self, event: Event, reason: str) -> None:
        self._store.append_processed({
            "event_key": event.event_key,
            "seen_at": time.time(),
            "reply_status": "seen",
            "reason": reason,
            "event": event.to_dict(),
        })

    def mark_failed(self, event: Event, reason: str, provider: str | None = None) -> None:
        record = self._store.get_record(event.event_key)
        retries = record.get("retries", 0) + 1 if record else 1

        self._store.append_processed({
            "event_key": event.event_key,
            "seen_at": time.time(),
            "reply_status": "failed",
            "reason": reason,
            "provider_used": provider,
            "retries": retries,
            "last_retry_at": time.time(),
            "event": event.to_dict(),
        })

    def mark_replied(self, event: Event, reply_text: str, provider: str, tool_calls: list[str] | None = None) -> None:
        ts = time.time()
        self._store.append_processed({
            "event_key": event.event_key,
            "seen_at": ts,
            "replied_at": ts,
            "reply_status": "replied",
            "provider_used": provider,
            "reply_text_hash": hash(reply_text),
            "event": event.to_dict(),
        })
        self._store.append_history({
            "event_key": event.event_key,
            "replied_at": ts,
            "provider_used": provider,
            "reply_text": reply_text,
            "tool_calls": tool_calls or [],
            "event": event.to_dict(),
        })

    def _is_fatal_error(self, reason: str) -> bool:
        return any(keyword in reason for keyword in FATAL_ERROR_KEYWORDS)


class DedupStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        status = context.dedup.is_duplicate(event.event_key)

        if status == DedupStatus.NEW:
            return StageResult.CONTINUE

        if status in (DedupStatus.REPLIED, DedupStatus.SEEN, DedupStatus.FAILED_FATAL):
            logger.debug("skip_duplicate", event_key=event.event_key, status=status.value)
            return StageResult.SKIP

        if status == DedupStatus.FAILED_RETRYABLE:
            logger.debug("retry_event", event_key=event.event_key)
            return StageResult.CONTINUE

        return StageResult.SKIP
