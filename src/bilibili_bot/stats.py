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
                provider_used = record.get("provider_used", "")

                total_reply_chars += len(reply_text)

                if _is_video_summary(reply_text, provider_used):
                    video_summary_count += 1

                if _is_search_call(reply_text, provider_used):
                    search_count += 1

                if _is_error(reply_text, provider_used):
                    error_count += 1

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
        f"搜索配额: {quota_used}/{quota_total}（{quota_total - quota_used} 剩余）",
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
    reply_tokens = total_replies * 200
    prompt_tokens = total_replies * 800
    total = reply_tokens + prompt_tokens
    if total >= 10000:
        return f"{total / 1000:.1f}k"
    return str(total)


def _is_video_summary(reply_text: str, provider_used: str) -> bool:
    video_markers = ["视频总结", "视频内容", "这个视频讲了", "视频主要", "视频介绍了"]
    return any(marker in reply_text for marker in video_markers)


def _is_search_call(reply_text: str, provider_used: str) -> bool:
    search_markers = ["搜索结果", "搜到", "找到", "搜索显示", "根据搜索"]
    return any(marker in reply_text for marker in search_markers)


def _is_error(reply_text: str, provider_used: str) -> bool:
    error_markers = ["错误", "失败", "异常", "出错", "暂时不可用", "稍后再试"]
    return any(marker in reply_text for marker in error_markers)


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
