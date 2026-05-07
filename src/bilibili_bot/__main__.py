"""Bilibili 评论自动回复机器人 v2 入口。"""

import signal
import sys
import threading

from bilibili_bot.config import BotConfig
from bilibili_bot.log import setup_logging
from bilibili_bot.client import BilibiliSession
from bilibili_bot.state import StateStore
from bilibili_bot.pipeline.dedup import DedupService
from bilibili_bot.pipeline.rate_limit import RateController
from bilibili_bot.providers.manager import ProviderManager
from bilibili_bot.cookie import CookieRefreshManager
from bilibili_bot.sources.msgfeed import MsgFeedReplySource
from bilibili_bot.sources.mention import MentionMsgFeedSource
from bilibili_bot.sources.own_video import OwnVideoCommentSource
from bilibili_bot.sources.own_dynamic import OwnDynamicCommentSource
from bilibili_bot.sources.dm import DMSource
from bilibili_bot.pipeline.base import Pipeline, PipelineContext
from bilibili_bot.pipeline.dedup import DedupStage
from bilibili_bot.pipeline.filter import FilterStage
from bilibili_bot.pipeline.rate_limit import RateLimitStage
from bilibili_bot.pipeline.generate import AIGenerateStage
from bilibili_bot.pipeline.safety import SafetyCheckStage
from bilibili_bot.pipeline.send import SendStage

import argparse
import time
import structlog

logger = structlog.get_logger()


def create_comment_pipeline() -> Pipeline:
    return Pipeline([
        DedupStage(),
        FilterStage(),
        RateLimitStage(),
        AIGenerateStage(),
        SafetyCheckStage(),
        SendStage(),
    ])


def create_dm_pipeline() -> Pipeline:
    return Pipeline([
        DedupStage(),
        RateLimitStage(),
        AIGenerateStage(),
        SafetyCheckStage(),
        SendStage(),
    ])


def run_once(config: BotConfig, dry_run: bool = False) -> None:
    client = BilibiliSession(config.cookie.cookies_file, config.bot.request_timeout_seconds)
    store = StateStore()
    dedup = DedupService(store)
    rate_limiter = RateController(config, store)
    providers = ProviderManager(config)
    cookie_manager = CookieRefreshManager(config, store)

    cookie_status = cookie_manager.maybe_refresh()
    logger.info("cookie_health", valid=cookie_status.valid, message=cookie_status.message)

    if not cookie_status.valid:
        return

    context = PipelineContext(
        config=config,
        client=client,
        dedup=dedup,
        providers=providers,
        rate_limiter=rate_limiter,
        dry_run=dry_run,
    )

    comment_pipeline = create_comment_pipeline()
    dm_pipeline = create_dm_pipeline()

    state = store.load_state()
    source_last_run = state.get("source_last_run", {})
    now = time.time()

    sources = []
    source_intervals = {}

    if config.sources.msgfeed.enabled:
        sources.append(("MsgFeedReplySource", MsgFeedReplySource(config)))
        source_intervals["MsgFeedReplySource"] = config.sources.msgfeed.poll_interval_seconds
    if config.sources.mention.enabled:
        sources.append(("MentionMsgFeedSource", MentionMsgFeedSource(config)))
        source_intervals["MentionMsgFeedSource"] = config.sources.mention.poll_interval_seconds
    if config.sources.own_video.enabled:
        sources.append(("OwnVideoCommentSource", OwnVideoCommentSource(config)))
        source_intervals["OwnVideoCommentSource"] = config.sources.own_video.poll_interval_seconds
    if config.sources.own_dynamic.enabled:
        sources.append(("OwnDynamicCommentSource", OwnDynamicCommentSource(config)))
        source_intervals["OwnDynamicCommentSource"] = config.sources.own_dynamic.poll_interval_seconds

    for source_name, source in sources:
        interval = source_intervals.get(source_name, config.bot.poll_interval_seconds)
        last_run = source_last_run.get(source_name, 0)

        if now - last_run < interval:
            logger.debug("skip_source", source=source_name, reason="interval_not_reached")
            continue

        allowed, reason = rate_limiter.can_run_source(source_name)
        if not allowed:
            logger.warning("skip_source", source=source_name, reason=reason)
            continue

        try:
            events = source.fetch()
            rate_limiter.record_source_success(source_name)
            source_last_run[source_name] = now
            logger.info("source_fetched", source=source_name, count=len(events))

            for event in events:
                comment_pipeline.run(event, context)

        except Exception as e:
            delay = rate_limiter.record_source_failure(source_name)
            logger.error("source_failed", source=source_name, error=str(e), retry_delay=delay)

    if config.sources.dm.enabled:
        dm_source = DMSource(config)
        try:
            dm_events = dm_source.fetch_new_messages()
            logger.info("dm_fetched", count=len(dm_events))

            for event in dm_events:
                dm_pipeline.run(event, context)

        except Exception as e:
            logger.error("dm_failed", error=str(e))

    state["source_last_run"] = source_last_run
    store.save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili 评论自动回复机器人 v2")
    parser.add_argument("--config", default="config/bot-config.toml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--dry-run", action="store_true", help="只生成回复，不实际发送")
    args = parser.parse_args()

    config = BotConfig.from_toml(args.config)
    setup_logging(config.bot.log_level)

    if args.once:
        run_once(config, dry_run=args.dry_run)
        return 0

    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("received_signal", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("starting_daemon", interval=config.bot.poll_interval_seconds)

    while not shutdown_event.is_set():
        try:
            run_once(config, dry_run=args.dry_run)
        except Exception as e:
            logger.error("daemon_error", error=str(e))

        shutdown_event.wait(timeout=config.bot.poll_interval_seconds)

    logger.info("daemon_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
