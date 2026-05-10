"""PydanticAI Tool 定义测试。"""
from bilibili_bot.tools import TOOLS, get_video_content, search_web


def test_tools_list_contains_two():
    """TOOLS 列表包含两个工具。"""
    assert len(TOOLS) == 2


def test_tool_names_match():
    """工具名称正确。"""
    names = {t.name for t in TOOLS}
    assert names == {"get_video_content", "search_web"}


def test_get_video_content_has_correct_name():
    """get_video_content 函数名正确。"""
    assert get_video_content.__name__ == "get_video_content"


def test_search_web_has_correct_name():
    """search_web 函数名正确。"""
    assert search_web.__name__ == "search_web"


def test_get_video_content_empty_bvid():
    """空 BV 号返回错误。"""
    result = get_video_content(bvid="")
    assert "错误" in result


def test_search_web_empty_query():
    """空搜索关键词返回错误。"""
    result = search_web(query="")
    assert "错误" in result


def test_tools_are_importable():
    """TOOLS 可以正常导入。"""
    from bilibili_bot.tools import TOOLS
    assert isinstance(TOOLS, list)
