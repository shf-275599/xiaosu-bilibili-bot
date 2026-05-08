"""联网搜索工具 —— 使用 DuckDuckGo（零配置，无 API Key）。"""

from __future__ import annotations


def web_search(query: str, num_results: int = 3) -> str:
    if not query.strip():
        return "错误：未提供搜索关键词"

    try:
        from ddgs import DDGS
    except ImportError:
        return "搜索功能不可用：缺少 ddgs 包（pip install ddgs）"

    try:
        results = list(DDGS().text(query, max_results=num_results))
    except Exception as e:
        return f"搜索请求失败: {e}"

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
