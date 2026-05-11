"""Bilibili Bot 工具系统 —— PydanticAI Tool 定义与执行。"""

from __future__ import annotations

import functools
import subprocess
import time
from pathlib import Path
from typing import Any

import structlog
from pydantic_ai import Tool

logger = structlog.get_logger()

# 包根目录（src/bilibili_bot/ 的上两级 → 项目根）
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = _PACKAGE_ROOT / "scripts"
COOKIES_FILE = str(_PACKAGE_ROOT / "config" / "bilibili-cookies.txt")
WHISPER_MODEL = str(
    _PACKAGE_ROOT / "models" / "whisper"
    / "models--Systran--faster-whisper-base"
    / "snapshots" / "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
)

TRANSCRIBE_COOLDOWN = 30
MAX_CACHE_SIZE = 50

_last_transcribe_at: float = 0
_transcript_cache: dict[str, str] = {}


def _with_tool_logging(func):
    """包装工具函数，记录 structlog 日志。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(
            "tool_call_start",
            tool=func.__name__,
            args=kwargs if kwargs else {},
        )
        result = func(*args, **kwargs)
        logger.info("tool_call_end", tool=func.__name__, result_preview=result[:200] if result else "")
        return result

    return wrapper


def _get_video_content(bvid: str) -> str:
    """摘要优先 → 不可用则 Whisper 转录降级。"""
    if not bvid:
        return "错误：未提供 BV 号"

    summary = _try_ai_summary(bvid)
    if summary and "不可用" not in summary and "失败" not in summary and "错误" not in summary:
        return f"【AI 摘要】{summary}"

    logger.info("summary_unavailable_fallback_transcript", bvid=bvid)
    transcript = _try_whisper_transcript(bvid)
    if transcript:
        return f"【语音转录】（AI摘要不可用，已自动使用语音识别）\n{transcript}"

    logger.warning("video_content_all_failed", bvid=bvid)
    return f"无法获取视频 {bvid} 的内容：AI 摘要和语音转录均不可用。"


def _try_ai_summary(bvid: str) -> str:
    script = SCRIPTS_DIR / "bilibili_wbi.py"
    if not script.exists():
        return ""

    try:
        result = subprocess.run(
            ["python3", str(script), bvid, COOKIES_FILE],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            if text and len(text) > 20:
                return text[:5000]
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("ai_summary_timeout", bvid=bvid)
        return ""
    except Exception as e:
        logger.warning("ai_summary_error", bvid=bvid, error=str(e))
        return ""


def _try_whisper_transcript(bvid: str) -> str:
    global _last_transcribe_at, _transcript_cache

    if bvid in _transcript_cache:
        logger.info("transcript_cache_hit", bvid=bvid)
        return _transcript_cache[bvid]

    now = time.time()
    if _last_transcribe_at > 0 and now - _last_transcribe_at < TRANSCRIBE_COOLDOWN:
        remaining = int(TRANSCRIBE_COOLDOWN - (now - _last_transcribe_at))
        logger.info("transcribe_cooldown", bvid=bvid, remaining=remaining)
        return f"语音转录冷却中（{remaining}秒后可重试）。请稍后再问。"

    _last_transcribe_at = now

    try:
        from bilibili_bot.tools.transcribe import transcribe_video
        result = transcribe_video(bvid, WHISPER_MODEL, COOKIES_FILE)
    except ImportError:
        return "语音转录模块不可用"
    except Exception as e:
        logger.warning("whisper_transcript_error", bvid=bvid, error=str(e))
        return f"语音转录失败: {e}"

    if result:
        _transcript_cache[bvid] = result
        if len(_transcript_cache) > MAX_CACHE_SIZE:
            _transcript_cache.pop(next(iter(_transcript_cache)))

    return result


def _search_web(query: str) -> str:
    if not query:
        return "错误：未提供搜索关键词"
    try:
        from bilibili_bot.config import BotConfig
        from bilibili_bot.tools.web_search import web_search

        limit = 30
        try:
            config = BotConfig.from_toml("config/bot-config.toml")
            limit = config.ai.search_quota_daily
        except Exception:
            pass

        return web_search(query, daily_limit=limit)
    except ImportError:
        return "搜索功能不可用"


# ── PydanticAI Tool 定义 ──

def get_video_content(bvid: str) -> str:
    """获取B站视频的内容总结。先尝试AI摘要，不可用时自动降级为语音转录。
    当用户询问'这个视频讲了什么'、'视频内容'或需要了解视频时调用。
    """
    return _get_video_content(bvid)


def search_web(query: str) -> str:
    """搜索互联网获取信息。当用户询问实时新闻、特定知识点、
    或需要查找资料时调用。返回搜索结果摘要。
    """
    return _search_web(query)


TOOLS = [
    Tool(_with_tool_logging(get_video_content)),
    Tool(_with_tool_logging(search_web)),
]
