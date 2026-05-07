"""联网搜索工具 —— 使用 Exa search 获取网络信息。"""

from __future__ import annotations


def web_search(query: str, num_results: int = 3) -> str:
    """搜索网络并返回结果摘要。

    使用 websearch 工具进行搜索。当不可用时，返回提示信息。
    """
    return (
        f"搜索「{query}」的功能尚未配置独立的搜索 API。"
        "建议在回复中告知用户：你可以自己搜索这个关键词，或者稍后再试。"
    )
