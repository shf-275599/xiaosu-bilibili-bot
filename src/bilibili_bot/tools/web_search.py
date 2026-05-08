"""联网搜索工具 —— Tavily Search，带月度配额控制。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

QUOTA_FILE = Path("data/search_quota.json")


def web_search(query: str, num_results: int = 3, monthly_limit: int = 30) -> str:
    if not query.strip():
        return "错误：未提供搜索关键词"

    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return "搜索功能不可用：未配置 TAVILY_API_KEY"

    if not _check_quota(monthly_limit):
        return f"本月搜索次数已用尽（上限 {monthly_limit} 次），请下月再试"

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": num_results,
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=15,
        )
        data = resp.json()

        if resp.status_code != 200:
            err = data.get("detail", {}).get("error", str(data))
            return f"Tavily 搜索失败: {err}"

        _increment_quota()
        results = data.get("results", [])
        if not results:
            return f"未找到与「{query}」相关的结果"

        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            content = r.get("content", "")[:200]
            url = r.get("url", "")
            lines.append(f"{i}. {title}")
            lines.append(f"   {content}")
            if url:
                lines.append(f"   {url}")
        return "\n".join(lines)

    except Exception as e:
        return f"Tavily 搜索请求失败: {e}"


def _check_quota(limit: int) -> bool:
    if limit <= 0:
        return True
    current = _read_quota()
    if current["month"] != _current_month():
        return True
    return current["count"] < limit


def _increment_quota() -> None:
    current = _read_quota()
    month = _current_month()
    if current["month"] != month:
        current = {"month": month, "count": 0}
    current["count"] += 1
    QUOTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUOTA_FILE.write_text(json.dumps(current), encoding="utf-8")


def _read_quota() -> dict:
    if not QUOTA_FILE.exists():
        return {"month": _current_month(), "count": 0}
    try:
        data = json.loads(QUOTA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "month" in data and "count" in data:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"month": _current_month(), "count": 0}


def _current_month() -> str:
    return time.strftime("%Y-%m")
