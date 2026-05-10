from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()

CST = timezone(timedelta(hours=8))


def generate_daily_report(store) -> str:
    """生成每日回复统计报告（过去 24 小时），返回格式化中文文本。"""
    now = time.time()
    day_start = now - 86400

    comment_count = 0
    dm_count = 0
    video_summary_count = 0
    search_count = 0
    error_count = 0
    source_counts: dict[str, int] = {}
    total_reply_chars = 0

    if store.reply_history_path.exists():
        with store.reply_history_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                replied_at = record.get("replied_at", 0)
                if replied_at < day_start:
                    continue

                event = record.get("event", {})
                source_type = event.get("source_type", "")
                source_counts[source_type] = source_counts.get(source_type, 0) + 1

                if source_type == "dm":
                    dm_count += 1
                else:
                    comment_count += 1

                reply_text = record.get("reply_text", "")

                total_reply_chars += len(reply_text)

                tool_calls = record.get("tool_calls", [])
                if "get_video_content" in tool_calls:
                    video_summary_count += 1
                if "search_web" in tool_calls:
                    search_count += 1

    # 从 processed.jsonl 统计回复失败记录（按 event_key 去重）
    failed_events: set[str] = set()
    if store.processed_path.exists():
        with store.processed_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen_at = record.get("seen_at", 0)
                if seen_at < day_start:
                    continue
                if record.get("reply_status") != "replied":
                    key = record.get("event_key", "")
                    if key:
                        failed_events.add(key)
    error_count = len(failed_events)

    total_replies = comment_count + dm_count
    quota_used, quota_total = _read_search_quota(store)
    api_estimate = _estimate_api_calls(total_replies, video_summary_count, search_count)
    token_estimate = _estimate_tokens(total_replies, total_reply_chars)
    source_health_line = _source_health(store)

    lines = [
        f"📊 今日报告",
        f"─" * 20,
        f"评论回复: {comment_count} 条 | 私信: {dm_count} 条 | 总计: {total_replies} 条",
        f"工具调用: 视频总结 {video_summary_count} 次 | 搜索 {search_count} 次",
    ]

    if source_counts:
        source_detail = "  ".join(f"{k}:{v}" for k, v in sorted(source_counts.items()))
        lines.append(f"来源分布: {source_detail}")

    lines.extend([
        f"─",
        f"API 调用估算: ~{api_estimate} 次",
        f"Token 估算: ~{token_estimate}",
        f"Tavily 搜索配额: 已用 {quota_used} / 共 {quota_total}，剩余 {quota_total - quota_used}",
        f"错误: {error_count} 次",
    ])

    source_health_line = _source_health(store)
    if source_health_line:
        lines.append(f"─")
        lines.append(source_health_line)

    return "\n".join(lines)


def _estimate_api_calls(total_replies: int, video_summaries: int, searches: int) -> int:
    return total_replies + video_summaries + searches


def _estimate_tokens(total_replies: int, total_chars: int) -> str:
    reply_tokens = int(total_chars / 2)  # 中文约 1.5-2 字符/token
    prompt_tokens = total_replies * 800
    total = reply_tokens + prompt_tokens
    if total >= 10000:
        return f"{total / 1000:.1f}k"
    return str(total)


def _source_health(store) -> str:
    state = store.load_state()
    sc = state.get("rate_limit", {}).get("source_cooldowns", {})
    now = time.time()
    problems = []
    for name, until in sc.items():
        if until > now:
            remaining = int(until - now)
            problems.append(f"{name} 冷却中({remaining}s)")
    if problems:
        return "⚠️ " + " ".join(problems)
    return ""


def _read_search_quota(store) -> tuple[int, int]:
    quota_path = store.root / "search_quota.json"
    default_total = 30

    if not quota_path.exists():
        return 0, default_total

    try:
        with quota_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        current_day = datetime.now(CST).strftime("%Y-%m-%d")
        if data.get("day") != current_day:
            return 0, default_total

        return data.get("count", 0), default_total
    except (json.JSONDecodeError, OSError):
        return 0, default_total
