#!/usr/bin/env python3
"""Bilibili 评论自动回复机器人 Phase 1。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from bot_config import DEFAULT_CONFIG_PATH, load_config, parse_cookies_file
from content_safety import ContentSafetyChecker
from cookie_refresh import CookieRefreshManager
from comment_dedup import DedupService
from comment_filters import should_skip_event
from comment_sender import send_reply
from comment_sources import MsgFeedReplySource, MentionMsgFeedSource, OwnDynamicCommentSource, OwnVideoCommentSource
from dm_source import DMSource
from dm_sender import send_dm
from dm_dedup import DMDedupService
from dm_prompt import build_dm_messages
from rate_control import RateController
from reply_prompt import build_messages
from reply_providers import ReplyProviderManager
from state_store import JsonlStateStore, utc_timestamp


LOGGER = logging.getLogger("bilibili-comment-bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def run_msgfeed_once(config: dict, dry_run: bool = False) -> int:
    store = JsonlStateStore()
    dedup = DedupService(store)
    cookie_manager = CookieRefreshManager(config)
    source_factories = []
    if config["sources"].get("msgfeed", {}).get("enabled", True):
        source_factories.append(MsgFeedReplySource(config))
    if config["sources"].get("mention", {}).get("enabled", False):
        source_factories.append(MentionMsgFeedSource(config))
    if config["sources"].get("own_video", {}).get("enabled", False):
        source_factories.append(OwnVideoCommentSource(config))
    if config["sources"].get("own_dynamic", {}).get("enabled", False):
        source_factories.append(OwnDynamicCommentSource(config))
    providers = ReplyProviderManager(config)
    rate_controller = RateController(config, store)
    my_uid = parse_cookies_file(config["cookie"]["cookies_file"]).get("DedeUserID")

    cookie_status = cookie_manager.maybe_refresh()
    LOGGER.info("Cookie 健康状态: valid=%s refresh=%s refreshed=%s message=%s", cookie_status.valid, cookie_status.should_refresh, cookie_status.refreshed, cookie_status.message)
    if not cookie_status.valid:
        state = store.update_state({
            "cookie_health": {
                "valid": cookie_status.valid,
                "should_refresh": cookie_status.should_refresh,
                "refreshed": cookie_status.refreshed,
                "message": cookie_status.message,
                "timestamp_ms": cookie_status.timestamp_ms,
            },
            "last_msgfeed_run_at": utc_timestamp(),
            "last_processed_count": 0,
        })
        LOGGER.warning("Cookie 无效，停止本轮发送，仅记录状态")
        return 0

    LOGGER.info("开始执行一轮多来源扫描")
    events = []
    state = store.load_state()
    source_last_run = state.get("source_last_run", {})
    now = time.time()

    for source in source_factories:
        source_name = source.__class__.__name__

        source_config = config["sources"].get(source_name.replace("Source", "").lower(), {})
        interval = source_config.get("poll_interval_seconds", config["bot"].get("poll_interval_seconds", 30))
        last_run = source_last_run.get(source_name, 0)
        if now - last_run < interval:
            LOGGER.info("跳过来源 %s: 间隔未满 %s 秒", source_name, interval)
            continue

        allowed, reason = rate_controller.can_run_source(source_name)
        if not allowed:
            LOGGER.warning("跳过来源 %s: %s", source_name, reason)
            continue
        try:
            rate_controller.wait_for_request_slot()
            batch = source.fetch()
            rate_controller.record_source_success(source_name)
            source_last_run[source_name] = now
            LOGGER.info("来源 %s 采集到 %s 条事件", source_name, len(batch))
            events.extend(batch)
        except Exception as exc:  # noqa: BLE001
            delay = rate_controller.record_source_failure(source_name)
            LOGGER.error("来源 %s 采集失败: %s；记录退避 %.2f 秒", source_name, exc, delay)

    state["source_last_run"] = source_last_run
    store.save_state(state)
    LOGGER.info("本轮总计采集到 %s 条事件", len(events))
    processed = 0

    for event in events:
        if dedup.already_handled(event, include_dry_run=dry_run):
            LOGGER.info("跳过已处理事件: %s", event.event_key())
            continue

        skip, reason = should_skip_event(event, config, my_uid)
        if skip:
            LOGGER.info("跳过事件 %s: %s", event.event_key(), reason)
            dedup.mark_seen(event, reason)
            continue

        allowed, reason = rate_controller.can_send(user_id=event.author_mid, oid=event.oid)
        if not allowed:
            LOGGER.warning("暂停发送 %s: %s", event.event_key(), reason)
            dedup.mark_failed(event, reason)
            continue

        rate_controller.wait_for_request_slot()
        messages = build_messages(event, config)
        reply = providers.generate_reply(messages)
        if not reply.success:
            LOGGER.error("回复生成失败 %s: %s", event.event_key(), reply.error)
            dedup.mark_failed(event, reply.error, reply.provider)
            rate_controller.record_failure(reply.retriable)
            continue

        safety = ContentSafetyChecker(config.get("content_safety"))
        check = safety.check(reply.text)
        if not check.safe:
            LOGGER.error("内容安全审查未通过 %s: %s [风险等级: %s]", event.event_key(), check.reason, check.risk_level)
            dedup.mark_failed(event, f"内容安全审查: {check.reason}", reply.provider)
            continue

        LOGGER.info("回复生成成功 [%s]: %s", reply.provider, reply.text)
        delay = rate_controller.wait_before_send()
        LOGGER.info("发送前随机等待 %.2f 秒", delay)

        if dry_run:
            LOGGER.info("dry-run 模式，不实际发送: %s", event.event_key())
            dedup.mark_dry_run(event, reply.text, reply.provider)
            processed += 1
            rate_controller.record_success(user_id=event.author_mid, oid=event.oid)
            continue

        success, message, retriable = send_reply(event, reply.text, config)
        if success:
            LOGGER.info("发送成功 %s", event.event_key())
            dedup.mark_replied(event, reply.text, reply.provider)
            processed += 1
            rate_controller.record_success(user_id=event.author_mid, oid=event.oid)
        else:
            LOGGER.error("发送失败 %s: %s", event.event_key(), message)
            dedup.mark_failed(event, message, reply.provider)
            rate_controller.record_failure(retriable)

    state = store.load_state()
    state["last_msgfeed_run_at"] = utc_timestamp()
    state["last_processed_count"] = processed
    state["cookie_health"] = {
        "valid": cookie_status.valid,
        "should_refresh": cookie_status.should_refresh,
        "refreshed": cookie_status.refreshed,
        "message": cookie_status.message,
        "timestamp_ms": cookie_status.timestamp_ms,
    }
    state["rate_limit"] = rate_controller.snapshot()
    store.save_state(state)
    return processed


def run_dm_once(config: dict, dry_run: bool = False) -> int:
    dm_config = config.get("sources", {}).get("dm", {})
    if not dm_config.get("enabled", False):
        return 0

    store = JsonlStateStore()
    dedup = DMDedupService(store)
    providers = ReplyProviderManager(config)
    rate_controller = RateController(config, store)

    dm_source = DMSource(config)
    max_reply = dm_config.get("max_reply_per_round", 5)
    skip_keywords = dm_config.get("skip_keywords", [])
    whitelist_mids = dm_config.get("whitelist_mids", [])

    try:
        events = dm_source.fetch_new_messages()
        LOGGER.info("私信轮询采集到 %s 条新消息", len(events))
    except Exception as exc:
        LOGGER.error("私信采集失败: %s", exc)
        return 0

    processed = 0
    for event in events:
        if processed >= max_reply:
            LOGGER.info("本轮私信回复已达上限 %s", max_reply)
            break

        if dedup.already_handled(event, include_dry_run=dry_run):
            LOGGER.info("跳过已处理私信: %s", event.event_key())
            continue

        if whitelist_mids and event.talker_id not in whitelist_mids:
            LOGGER.info("跳过非白名单用户: %s (%s)", event.talker_name, event.talker_id)
            dedup.mark_seen(event, "非白名单用户")
            continue

        skip = False
        for keyword in skip_keywords:
            if keyword in event.content:
                LOGGER.info("跳过含关键词私信: %s", event.event_key())
                dedup.mark_seen(event, f"含关键词: {keyword}")
                skip = True
                break
        if skip:
            continue

        allowed, reason = rate_controller.can_send(user_id=str(event.talker_id))
        if not allowed:
            LOGGER.warning("暂停私信发送 %s: %s", event.event_key(), reason)
            dedup.mark_failed(event, reason)
            continue

        rate_controller.wait_for_request_slot()
        messages = build_dm_messages(event, config)
        reply = providers.generate_reply(messages)
        if not reply.success:
            LOGGER.error("私信回复生成失败 %s: %s", event.event_key(), reply.error)
            dedup.mark_failed(event, reply.error, reply.provider)
            rate_controller.record_failure(reply.retriable)
            continue

        safety = ContentSafetyChecker(config.get("content_safety"))
        check = safety.check(reply.text)
        if not check.safe:
            LOGGER.error("私信内容安全审查未通过 %s: %s [风险等级: %s]", event.event_key(), check.reason, check.risk_level)
            dedup.mark_failed(event, f"内容安全审查: {check.reason}", reply.provider)
            continue

        LOGGER.info("私信回复生成成功 [%s]: %s", reply.provider, reply.text)
        delay = rate_controller.wait_before_send()
        LOGGER.info("发送前随机等待 %.2f 秒", delay)

        if dry_run:
            LOGGER.info("dry-run 模式，不实际发送私信: %s", event.event_key())
            dedup.mark_dry_run(event, reply.text, reply.provider)
            processed += 1
            rate_controller.record_success(user_id=str(event.talker_id))
            continue

        result = send_dm(event.talker_id, reply.text, config)
        if result.success:
            LOGGER.info("私信发送成功 %s", event.event_key())
            dedup.mark_replied(event, reply.text, reply.provider)
            processed += 1
            rate_controller.record_success(user_id=str(event.talker_id))
        else:
            LOGGER.error("私信发送失败 %s: %s", event.event_key(), result.message)
            dedup.mark_failed(event, result.message, reply.provider)
            rate_controller.record_failure(result.retriable)

    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili 评论自动回复机器人 Phase 1")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--dry-run", action="store_true", help="只生成回复，不实际发送")
    parser.add_argument("--print-msgfeed", action="store_true", help="打印标准化后的 msgfeed 事件")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["bot"].get("log_level", "INFO"))

    if args.print_msgfeed:
        try:
            source = MsgFeedReplySource(config)
            events = [event.to_dict() for event in source.fetch()]
            print(json.dumps(events, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("打印 msgfeed 失败: %s", exc)
            return 1

    if args.once:
        try:
            run_msgfeed_once(config, dry_run=args.dry_run)
            run_dm_once(config, dry_run=args.dry_run)
            return 0
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("单轮执行失败: %s", exc)
            return 1

    interval = config["bot"].get("poll_interval_seconds", 30)
    LOGGER.info("进入守护模式，轮询间隔 %s 秒", interval)
    while True:
        try:
            run_msgfeed_once(config, dry_run=args.dry_run)
            run_dm_once(config, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("守护循环异常: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
