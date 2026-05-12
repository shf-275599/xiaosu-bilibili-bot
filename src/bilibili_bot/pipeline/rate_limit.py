from __future__ import annotations

import random
import time
from typing import Any

import structlog

from bilibili_bot.events import Event
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult
from bilibili_bot.atomic_state import AtomicStateStore

logger = structlog.get_logger()


class RateController:
    def __init__(self, config, store: AtomicStateStore | None = None):
        self.config = config
        self.store = store
        cfg = config.rate_limit
        self.min_request_interval = cfg.min_request_interval_seconds
        self.reply_delay_min = cfg.reply_delay_min_seconds
        self.reply_delay_max = cfg.reply_delay_max_seconds
        self.backoff_base = cfg.backoff_base_seconds
        self.circuit_breaker_failures = cfg.circuit_breaker_failures
        self.circuit_breaker_cooldown = cfg.circuit_breaker_cooldown_seconds
        self.source_circuit_breaker_failures = cfg.source_circuit_breaker_failures
        self.max_hourly_replies = cfg.max_hourly_replies
        self.max_daily_replies = cfg.max_daily_replies
        self.max_replies_per_user_per_hour = cfg.max_replies_per_user_per_hour
        self.max_replies_per_oid_per_hour = cfg.max_replies_per_oid_per_hour

        self._load_state()

    def _load_state(self) -> None:
        if self.store is None:
            self._init_empty_state()
            return

        state = self.store.load_state()
        rate_state = state.get("rate_limit", {})

        self.failure_count = rate_state.get("failure_count", 0)
        self.cooldown_until = rate_state.get("cooldown_until", 0.0)
        self.last_request_at = rate_state.get("last_request_at", 0.0)
        self.reply_timestamps = rate_state.get("reply_timestamps", [])
        self.user_reply_timestamps = rate_state.get("user_reply_timestamps", {})
        self.oid_reply_timestamps = rate_state.get("oid_reply_timestamps", {})
        self.source_failures = rate_state.get("source_failures", {})
        self.source_cooldowns = rate_state.get("source_cooldowns", {})

        self._prune_reply_timestamps()

    def _init_empty_state(self) -> None:
        self.failure_count = 0
        self.cooldown_until = 0.0
        self.last_request_at = 0.0
        self.reply_timestamps: list[float] = []
        self.user_reply_timestamps: dict[str, list[float]] = {}
        self.oid_reply_timestamps: dict[str, list[float]] = {}
        self.source_failures: dict[str, int] = {}
        self.source_cooldowns: dict[str, float] = {}

    def _save_state(self) -> None:
        if self.store is None:
            return

        self._prune_reply_timestamps()
        state = self.store.load_state()
        state["rate_limit"] = {
            "failure_count": self.failure_count,
            "cooldown_until": self.cooldown_until,
            "last_request_at": self.last_request_at,
            "reply_timestamps": self.reply_timestamps,
            "user_reply_timestamps": self.user_reply_timestamps,
            "oid_reply_timestamps": self.oid_reply_timestamps,
            "source_failures": self.source_failures,
            "source_cooldowns": self.source_cooldowns,
        }
        self.store.save_state(state)

    def wait_for_request_slot(self) -> float:
        now = time.time()
        elapsed = now - self.last_request_at
        if elapsed < self.min_request_interval:
            sleep_time = self.min_request_interval - elapsed
            time.sleep(sleep_time)
            now = time.time()
        self.last_request_at = now
        self._save_state()
        return now

    def can_send(self, user_id: str = "", oid: str = "") -> tuple[bool, str]:
        self._prune_reply_timestamps()
        now = time.time()

        if now < self.cooldown_until:
            return False, f"熔断冷却中，直到 {int(self.cooldown_until)}"

        hourly_count = len([ts for ts in self.reply_timestamps if now - ts < 3600])
        if hourly_count >= self.max_hourly_replies:
            return False, "已达到每小时回复上限"

        daily_count = len([ts for ts in self.reply_timestamps if now - ts < 86400])
        if daily_count >= self.max_daily_replies:
            return False, "已达到每日回复上限"

        if user_id:
            user_ts = self.user_reply_timestamps.get(user_id, [])
            if len([ts for ts in user_ts if now - ts < 3600]) >= self.max_replies_per_user_per_hour:
                return False, f"用户 {user_id} 每小时回复已达上限"

        if oid:
            oid_ts = self.oid_reply_timestamps.get(oid, [])
            if len([ts for ts in oid_ts if now - ts < 3600]) >= self.max_replies_per_oid_per_hour:
                return False, f"内容 {oid} 每小时回复已达上限"

        return True, "允许发送"

    def can_run_source(self, name: str) -> tuple[bool, str]:
        until = self.source_cooldowns.get(name, 0.0)
        if time.time() < until:
            return False, f"来源 {name} 冷却中，直到 {int(until)}"
        return True, "允许采集"

    def wait_before_send(self) -> float:
        delay = random.uniform(self.reply_delay_min, self.reply_delay_max)
        time.sleep(delay)
        return delay

    def record_success(self, user_id: str = "", oid: str = "") -> None:
        now = time.time()
        self.failure_count = 0
        self.cooldown_until = 0.0
        self.reply_timestamps.append(now)
        if user_id:
            self.user_reply_timestamps.setdefault(user_id, []).append(now)
        if oid:
            self.oid_reply_timestamps.setdefault(oid, []).append(now)
        self._prune_reply_timestamps()
        self._save_state()

    def record_source_success(self, name: str) -> None:
        self.source_failures[name] = 0
        self.source_cooldowns[name] = 0.0
        self._save_state()

    def record_failure(self, retriable: bool) -> float:
        self.failure_count += 1
        delay = self.backoff_base * (2 ** max(0, self.failure_count - 1))
        if self.failure_count >= self.circuit_breaker_failures:
            self.cooldown_until = time.time() + self.circuit_breaker_cooldown
        self._save_state()
        if retriable:
            time.sleep(delay)
        return delay

    def record_source_failure(self, name: str) -> float:
        count = self.source_failures.get(name, 0) + 1
        self.source_failures[name] = count
        delay = self.backoff_base * max(1, count)
        if count >= self.source_circuit_breaker_failures:
            cooldown = self.config.bot.source_failure_cooldown_seconds
            self.source_cooldowns[name] = time.time() + cooldown
        self._save_state()
        return delay

    def _prune_reply_timestamps(self) -> None:
        now = time.time()
        self.reply_timestamps = [ts for ts in self.reply_timestamps if now - ts < 86400]

        for uid in list(self.user_reply_timestamps.keys()):
            self.user_reply_timestamps[uid] = [
                ts for ts in self.user_reply_timestamps[uid] if now - ts < 86400
            ]
            if not self.user_reply_timestamps[uid]:
                del self.user_reply_timestamps[uid]

        for oid in list(self.oid_reply_timestamps.keys()):
            self.oid_reply_timestamps[oid] = [
                ts for ts in self.oid_reply_timestamps[oid] if now - ts < 86400
            ]
            if not self.oid_reply_timestamps[oid]:
                del self.oid_reply_timestamps[oid]


class RateLimitStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        allowed, reason = context.rate_limiter.can_send(
            user_id=event.author_id,
            oid=event.target_id,
        )

        if not allowed:
            logger.warning("rate_limit_blocked", event_key=event.event_key, reason=reason)
            context.dedup.mark_failed(event, reason)
            return StageResult.SKIP

        context.rate_limiter.wait_for_request_slot()
        return StageResult.CONTINUE
