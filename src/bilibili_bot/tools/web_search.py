"""联网搜索工具 —— Tavily 主搜索（带配额），DuckDuckGo 降级。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

QUOTA_FILE = Path("data/search_quota.json")


def web_search(query: str, num_results: int = 3, daily_limit: int = 30) -> str:
    if not query.strip():
        return "错误：未提供搜索关键词"

    api_key = os.environ.get("TAVILY_API_KEY", "")

    if api_key and _check_quota(daily_limit):
        result = _tavily_search(query, num_results, api_key)
        if result:
            return result

    return _duckduckgo_search(query, num_results)


def _tavily_search(query: str, num: int, api_key: str) -> str:
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": num,
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=15,
        )
        data = resp.json()

        if resp.status_code != 200:
            return ""

        results = data.get("results", [])
        if not results:
            return ""

        _increment_quota()

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '无标题')}")
            lines.append(f"   {r.get('content', '')[:200]}")
            if r.get("url"):
                lines.append(f"   {r['url']}")
        return "\n".join(lines)

    except Exception:
        return ""


def _duckduckgo_search(query: str, num: int) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        return "搜索功能不可用"

    try:
        results = list(DDGS().text(query, max_results=num))
    except Exception:
        return "搜索请求失败"

    if not results:
        return f"未找到与「{query}」相关的结果"

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        body = r.get("body", "")[:200]
        href = r.get("href", "")
        lines.append(f"{i}. {title}")
        lines.append(f"   {body}")
        if href:
            lines.append(f"   {href}")
    return "\n".join(lines)


def _check_quota(limit: int) -> bool:
    if limit <= 0:
        return True
    current = _read_quota()
    if current["day"] != _current_day():
        return True
    return current["count"] < limit


def _increment_quota() -> None:
    current = _read_quota()
    day = _current_day()
    if current["day"] != day:
        current = {"day": day, "count": 0}
    current["count"] += 1

    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = QUOTA_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(current), encoding="utf-8")
    tmp_path.replace(QUOTA_FILE)


def _read_quota() -> dict:
    if not QUOTA_FILE.exists():
        return {"day": _current_day(), "count": 0}
    try:
        data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "day" in data and "count" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"day": _current_day(), "count": 0}


def _current_day() -> str:
    return time.strftime("%Y-%m-%d")
