"""Bilibili Bot 工具系统 —— LLM Function Calling 工具定义与执行。"""

from __future__ import annotations

import subprocess
import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

SCRIPTS_DIR = Path("/home/shf/.config/opencode/scripts/bilibili scripts")
COOKIES_FILE = "/home/shf/bilibili-bot/config/bilibili-cookies.txt"

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_video_summary",
            "description": (
                "获取B站视频的AI摘要。当用户询问'这个视频讲了什么'、"
                "'视频内容总结'或需要了解视频内容时调用。需要提供BV号。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bvid": {"type": "string", "description": "视频的BV号，如 BV1xx411c7mD"}
                },
                "required": ["bvid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_video_transcript",
            "description": (
                "获取B站视频的Whisper语音转录文本。当用户询问'字幕'、"
                "'完整内容'或需要视频文字稿时调用。需要提供BV号。"
                "注意：此操作较慢（30-120秒），适用于长视频转录场景。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bvid": {"type": "string", "description": "视频的BV号，如 BV1xx411c7mD"}
                },
                "required": ["bvid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "搜索互联网获取信息。当用户询问实时新闻、特定知识点、"
                "或需要查找资料时调用。返回搜索结果摘要。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词，用中文或英文"}
                },
                "required": ["query"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """执行工具并返回结果字符串。"""
    try:
        if name == "get_video_summary":
            return _get_video_summary(arguments.get("bvid", ""))
        elif name == "get_video_transcript":
            return _get_video_transcript(arguments.get("bvid", ""))
        elif name == "search_web":
            return _search_web(arguments.get("query", ""))
        else:
            return f"错误：未知工具 {name}"
    except Exception as e:
        logger.warning("tool_execution_failed", tool=name, error=str(e))
        return f"工具执行失败: {e}"


def _get_video_summary(bvid: str) -> str:
    if not bvid:
        return "错误：未提供 BV 号"

    script = SCRIPTS_DIR / "bilibili_wbi.py"
    if not script.exists():
        return f"错误：找不到摘要脚本 {script}"

    try:
        result = subprocess.run(
            ["python3", str(script), bvid, COOKIES_FILE],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return f"获取视频摘要失败: {result.stderr[:500]}"
        return result.stdout[:3000]
    except subprocess.TimeoutExpired:
        return "获取视频摘要超时（60秒）"
    except Exception as e:
        return f"获取视频摘要异常: {e}"


def _get_video_transcript(bvid: str) -> str:
    if not bvid:
        return "错误：未提供 BV 号"

    script = SCRIPTS_DIR / "bilibili-whisper-transcribe.sh"
    if not script.exists():
        return f"错误：找不到转录脚本 {script}"

    try:
        result = subprocess.run(
            ["bash", str(script), f"https://www.bilibili.com/video/{bvid}", COOKIES_FILE, "auto"],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            return f"获取视频转录失败: {result.stderr[:500]}"
        return result.stdout[:5000]
    except subprocess.TimeoutExpired:
        return "获取视频转录超时（180秒）"
    except Exception as e:
        return f"获取视频转录异常: {e}"


def _search_web(query: str) -> str:
    if not query:
        return "错误：未提供搜索关键词"

    try:
        from bilibili_bot.tools.web_search import web_search
        return web_search(query)
    except ImportError:
        return "搜索功能不可用: 缺少 web_search 模块"
