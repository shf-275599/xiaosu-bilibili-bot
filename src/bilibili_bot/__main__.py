"""Bilibili 评论自动回复机器人 v3 入口。"""

import argparse
import json as json_lib
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from bilibili_bot.atomic_state import AtomicStateStore
from bilibili_bot.auto_skip import AutoSkipTracker
from bilibili_bot.client import BilibiliSession
from bilibili_bot.config import BotConfig
from bilibili_bot.cookie import CookieRefreshManager
from bilibili_bot.cookie_store import CookieStore
from bilibili_bot.feedback import check_reply_quality, get_quality_summary, save_feedback
from bilibili_bot.log import setup_logging
from bilibili_bot.pipeline.base import Pipeline, PipelineContext
from bilibili_bot.pipeline.dedup import DedupService, DedupStage
from bilibili_bot.pipeline.filter import FilterStage
from bilibili_bot.pipeline.generate import AIGenerateStage
from bilibili_bot.pipeline.rate_limit import RateController, RateLimitStage
from bilibili_bot.pipeline.safety import SafetyCheckStage
from bilibili_bot.pipeline.send import SendStage
from bilibili_bot.providers.manager import ProviderManager
from bilibili_bot.sources.dm import DMSource
from bilibili_bot.sources.msgfeed import MentionMsgFeedSource
from bilibili_bot.sources.msgfeed import MsgFeedReplySource
from bilibili_bot.sources.own_dynamic import OwnDynamicCommentSource
from bilibili_bot.sources.own_video import OwnVideoCommentSource

logger = structlog.get_logger()

CST = timezone(timedelta(hours=8))


def _send_report_dm(config: BotConfig, report_text: str, cookie_store: CookieStore) -> bool:
    client = BilibiliSession(cookie_store, config.bot.request_timeout_seconds)
    csrf = client.get_cookie("bili_jct", "")
    sender_uid = client.get_cookie("DedeUserID", "")
    receiver_id = config.bot.report_owner_uid

    data = {
        "msg[sender_uid]": sender_uid,
        "msg[receiver_id]": receiver_id,
        "msg[receiver_type]": 1,
        "msg[msg_type]": 1,
        "msg[msg_status]": 0,
        "msg[content]": json_lib.dumps({"content": report_text}),
        "msg[dev_id]": str(uuid.uuid4()),
        "msg[new_face_version]": 0,
        "msg[timestamp]": int(time.time()),
        "from_firework": 0,
        "build": 0,
        "mobi_app": "web",
        "csrf_token": csrf,
        "csrf": csrf,
    }

    query_params = client.sign_wbi({
        "w_sender_uid": sender_uid,
        "w_receiver_id": receiver_id,
    })

    resp = client.post(
        "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg",
        params=query_params,
        data=data,
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get("code") == 0


def _maybe_send_daily_report(config: BotConfig, cookie_store: CookieStore) -> None:
    from bilibili_bot.stats import generate_daily_report

    now = datetime.now(CST)
    today_str = now.strftime("%Y-%m-%d")

    if now.hour != config.bot.report_hour:
        return

    atomic_store = AtomicStateStore()
    state = atomic_store.load_state()

    if state.get("last_report_date") == today_str:
        return

    report_text = generate_daily_report(atomic_store)

    if _send_report_dm(config, report_text, cookie_store):
        logger.info("daily_report_sent")
        state["last_report_date"] = today_str
        atomic_store.save_state(state)


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


def run_once(config: BotConfig, dry_run: bool = False,
             providers: ProviderManager = None,
             cookie_store: CookieStore = None) -> None:
    if cookie_store is None:
        cookie_store = CookieStore(config.cookie.cookies_file)
    client = BilibiliSession(cookie_store, config.bot.request_timeout_seconds)
    atomic_store = AtomicStateStore()
    dedup = DedupService(atomic_store)
    rate_limiter = RateController(config, atomic_store)
    if providers is None:
        providers = ProviderManager(config)
    cookie_manager = CookieRefreshManager(config, cookie_store, atomic_store)
    auto_skip_tracker = AutoSkipTracker(atomic_store)

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
        auto_skip=auto_skip_tracker,
        store=atomic_store,
    )

    comment_pipeline = create_comment_pipeline()
    dm_pipeline = create_dm_pipeline()

    state = atomic_store.load_state()
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

    if config.sources.dm.enabled:
        dm_source = DMSource(config)
        dm_interval = config.sources.dm.poll_interval_seconds
        dm_last_run = source_last_run.get("DMSource", 0)

        if now - dm_last_run >= dm_interval:
            allowed, reason = rate_limiter.can_run_source("DMSource")
            if allowed:
                try:
                    dm_events = dm_source.fetch_new_messages()
                    rate_limiter.record_source_success("DMSource")
                    source_last_run["DMSource"] = now
                    logger.info("dm_fetched", count=len(dm_events))

                    for event in dm_events:
                        dm_pipeline.run(event, context)

                except Exception as e:
                    delay = rate_limiter.record_source_failure("DMSource")
                    logger.error("dm_failed", error=str(e), retry_delay=delay)
            else:
                logger.warning("skip_source", source="DMSource", reason=reason)
        else:
            logger.debug("skip_source", source="DMSource", reason="interval_not_reached")

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

    feedback_check_interval = 6 * 3600
    last_feedback_check = state.get("last_feedback_check", 0)
    if now - last_feedback_check >= feedback_check_interval:
        try:
            feedback_results = check_reply_quality(client, atomic_store)
            if feedback_results:
                save_feedback(atomic_store, feedback_results)
            summary = get_quality_summary(atomic_store)
            state["last_feedback_check"] = int(now)
            logger.info(
                "feedback_check_done",
                checked=len(feedback_results),
                summary=summary,
            )
        except Exception as e:
            logger.error("feedback_check_failed", error=str(e))

    state["source_last_run"] = source_last_run

    compact_interval = 86400
    last_compact = state.get("last_compact_check", 0)
    if now - last_compact >= compact_interval:
        try:
            file_size = atomic_store.processed_path.stat().st_size if atomic_store.processed_path.exists() else 0
            entry_count = len(atomic_store._processed_index)
            if file_size > 10_000_000 or entry_count > 5000:
                freed = atomic_store.compact_processed()
                logger.info("processed_compacted", freed_bytes=freed, entries_before=entry_count)
        except Exception as e:
            pass
        state["last_compact_check"] = int(now)

    atomic_store.save_state(state)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili 评论自动回复机器人 v3")
    parser.add_argument("--config", default="config/bot-config.toml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--dry-run", action="store_true", help="只生成回复，不实际发送")
    args = parser.parse_args()

    config = BotConfig.from_toml(args.config)
    setup_logging(config.bot.log_level)

    if args.once:
        run_once(config, dry_run=args.dry_run)
        return 0

    cookie_store = CookieStore(config.cookie.cookies_file)
    providers = ProviderManager(config)  # 只创建一次，保持会话
    shutdown_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("received_signal", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("starting_daemon", interval=config.bot.poll_interval_seconds)

    while not shutdown_event.is_set():
        try:
            run_once(config, dry_run=args.dry_run,
                     providers=providers, cookie_store=cookie_store)
        except Exception as e:
            logger.error("daemon_error", error=str(e))

        if config.bot.report_enabled:
            try:
                _maybe_send_daily_report(config, cookie_store)
            except Exception as e:
                logger.error("daily_report_error", error=str(e))

        shutdown_event.wait(timeout=config.bot.poll_interval_seconds)

    logger.info("daemon_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
