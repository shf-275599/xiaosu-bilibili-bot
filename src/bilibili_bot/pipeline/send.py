from __future__ import annotations

import uuid

import structlog

from bilibili_bot.events import Event, CommentEvent, DMEvent
from bilibili_bot.pipeline.base import PipelineStage, PipelineContext, StageResult

logger = structlog.get_logger()

COMMENT_TYPE_MAP = {"video": 1, "dynamic": 17, "dynamic_draw": 11, "article": 12}

DM_FATAL_CODES = {-101, -403}


def _classify_dm_error(code: int) -> tuple[bool, bool]:
    """返回 (is_success, is_retriable)。"""
    if code == 0:
        return True, False
    if code in DM_FATAL_CODES or str(code).startswith("1205"):
        return False, False
    return False, True


def send_comment_reply(event: CommentEvent, reply_text: str, client) -> tuple[bool, str, bool]:
    csrf = client.get_cookie("bili_jct", "")

    # WBI 签名参数（URL query）
    query_params = client.sign_wbi({
        "type": COMMENT_TYPE_MAP.get(event.business_type, 1),
        "oid": event.oid,
        "root": event.root_rpid if event.root_rpid and event.root_rpid != "0" else event.rpid,
        "parent": event.rpid,
    })

    # 表单数据（POST body）
    form_data = {
        "type": COMMENT_TYPE_MAP.get(event.business_type, 1),
        "oid": event.oid,
        "root": event.root_rpid if event.root_rpid and event.root_rpid != "0" else event.rpid,
        "parent": event.rpid,
        "message": reply_text,
        "csrf": csrf,
        "plat": 1,
    }

    try:
        resp = client.post(
            "https://api.bilibili.com/x/v2/reply/add",
            params=query_params,
            data=form_data,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") == 0:
            return True, "发送成功", False

        code = data.get("code", -1)
        message = data.get("message", "未知错误")
        retriable = code in {-509, 12051} or str(code).startswith("12")
        return False, f"发送失败 code={code} message={message}", retriable

    except Exception as e:
        return False, f"请求异常: {e}", True


def send_dm_reply(event: DMEvent, reply_text: str, client) -> tuple[bool, str, bool]:
    csrf = client.get_cookie("bili_jct", "")
    sender_uid = client.get_cookie("DedeUserID", "")
    receiver_id = event.talker_id

    import json as json_lib
    data = {
        "msg[sender_uid]": sender_uid,
        "msg[receiver_id]": receiver_id,
        "msg[receiver_type]": 1,
        "msg[msg_type]": 1,
        "msg[msg_status]": 0,
        "msg[content]": json_lib.dumps({"content": reply_text}),
        "msg[dev_id]": str(uuid.uuid4()),
        "msg[new_face_version]": 0,
        "msg[timestamp]": int(__import__("time").time()),
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

    try:
        resp = client.post(
            "https://api.vc.bilibili.com/web_im/v1/web_im/send_msg",
            params=query_params,
            data=data,
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") == 0:
            return True, "发送成功", False

        _success, retriable = _classify_dm_error(result.get("code", -1))
        logger.warning(
            "send_dm_raw_response",
            code=result.get("code"),
            msg=result.get("msg"),
        )
        return False, f"发送失败 code={result.get('code')} msg={result.get('msg')}", retriable

    except Exception as e:
        return False, f"请求异常: {e}", True


class SendStage(PipelineStage):
    def process(self, event: Event, context: PipelineContext) -> StageResult:
        delay = context.rate_limiter.wait_before_send()
        logger.info("send_delay", event_key=event.event_key, delay=round(delay, 2))

        if context.dry_run:
            logger.info("dry_run", event_key=event.event_key, reply=context.reply_text[:50])
            context.dedup.mark_replied(event, context.reply_text, f"{context.provider_used}:dry-run", context.tool_calls)
            context.rate_limiter.record_success(user_id=event.author_id, oid=event.target_id)

            return StageResult.CONTINUE

        if isinstance(event, CommentEvent):
            success, message, retriable = send_comment_reply(event, context.reply_text, context.client)
        elif isinstance(event, DMEvent):
            success, message, retriable = send_dm_reply(event, context.reply_text, context.client)
        else:
            logger.error("unknown_event_type", event_type=type(event).__name__)
            return StageResult.SKIP

        if success:
            logger.info("send_success", event_key=event.event_key)
            context.dedup.mark_replied(event, context.reply_text, context.provider_used, context.tool_calls)
            context.rate_limiter.record_success(user_id=event.author_id, oid=event.target_id)

            if isinstance(event, CommentEvent) and context.store:
                pass  # v3: Agent 管理对话历史，不再持久化到 bot-state.json
        else:
            logger.error("send_failed", event_key=event.event_key, error=message)
            context.dedup.mark_failed(event, message, context.provider_used)
            context.rate_limiter.record_failure(retriable)
            if not retriable and isinstance(event, CommentEvent) and context.auto_skip:
                context.auto_skip.record_fatal(event.event_key, event.author_mid, event.source_type)

        return StageResult.CONTINUE
