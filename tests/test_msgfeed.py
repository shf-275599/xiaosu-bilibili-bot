"""msgfeed 源模块内容补充（enrichment）功能的单元测试。

覆盖范围：
- _extract_dynamic_id（opus URI、t.bilibili.com URI、非法 URI）
- opus/detail 文字提取（MODULE_TYPE_CONTENT 段落节点遍历）
- _enrich_events 动态 enrich（dict 格式向后兼容、图片、转发动态）
- 文章 enrich（article_id 提取、API 失败回退）
- _dynamic_title_cache（mention 写入、reply 读取、转发标题过滤）
- _enrich_users 调用验证
"""

from unittest.mock import MagicMock, patch

import pytest

from bilibili_bot.events import CommentEvent
from bilibili_bot.sources.msgfeed import (
    MsgFeedReplySource,
    _dynamic_title_cache,
    _extract_dynamic_id,
)

# ---------------------------------------------------------------------------
# Helper: 从 opus/detail API 的 array-format modules 中提取文字
# 这是预期的 opus 文字提取逻辑，源文件后续应实现为独立函数。
# ---------------------------------------------------------------------------


def _extract_opus_text(data: dict) -> str:
    """从 opus/detail API 响应中提取纯文字。

    遍历 array 格式的 modules，找到 MODULE_TYPE_CONTENT，
    再遍历其 paragraphs → text → nodes → word.words。
    """
    item = data.get("item", {}) or {}
    modules = item.get("modules", []) or []
    for mod in modules:
        if mod.get("module_type") == "MODULE_TYPE_CONTENT":
            content = mod.get("module_content", {}) or {}
            paragraphs = content.get("paragraphs", []) or []
            parts = []
            for para in paragraphs:
                text_node = para.get("text", {}) or {}
                for node in text_node.get("nodes", []) or []:
                    word = node.get("word", {}) or {}
                    if word.get("words"):
                        parts.append(word["words"])
            return "".join(parts)
    return ""


def make_event(
    business_type: str,
    oid: str,
    uri: str = "",
    source_type: str = "msgfeed",
    video_title: str = "",
    author_mid: str = "789",
) -> CommentEvent:
    """快速构建带必要字段的 CommentEvent。"""
    return CommentEvent(
        source_type=source_type,
        event_key=f"{business_type}:{oid}:rpid123",
        created_at=1000,
        raw_payload={"item": {"uri": uri}, "uri": uri},
        business_type=business_type,
        oid=oid,
        rpid="rpid123",
        author_mid=author_mid,
        content_text="test content",
        video_title=video_title,
    )


def make_mock_client(json_data: dict) -> MagicMock:
    """创建 mock BilibiliSession，其 .get().json() 返回指定数据。"""
    client = MagicMock()
    resp = MagicMock()
    resp.json.return_value = json_data
    client.get.return_value = resp
    client.sign_wbi.return_value = {"mid": "789", "wts": "123456", "w_rid": "abc"}
    return client


# 屏蔽 _enrich_users 让 enrich 测试专注动态/文章逻辑
@pytest.fixture
def source():
    config = MagicMock()
    config.sources.msgfeed.page_size = 10
    obj = MsgFeedReplySource(config)
    obj._enrich_users = MagicMock()
    return obj


@pytest.fixture(autouse=True)
def clear_cache():
    _dynamic_title_cache.clear()


# ===================================================================
# _extract_dynamic_id
# ===================================================================

class TestExtractDynamicId:
    """从 B 站 URI 提取动态 ID。"""

    def test_opus_uri(self):
        uri = "https://www.bilibili.com/opus/1197036032889978896"
        assert _extract_dynamic_id(uri) == "1197036032889978896"

    def test_opus_uri_with_trailing_slash(self):
        uri = "https://www.bilibili.com/opus/1197036032889978896/"
        assert _extract_dynamic_id(uri) == "1197036032889978896"

    def test_opus_uri_with_query(self):
        uri = "https://www.bilibili.com/opus/1197036032889978896?spm_id_from=333.999"
        assert _extract_dynamic_id(uri) == "1197036032889978896"

    def test_t_bilibili_uri(self):
        uri = "https://t.bilibili.com/381040221372962034"
        assert _extract_dynamic_id(uri) == "381040221372962034"

    def test_t_bilibili_uri_with_query(self):
        uri = "https://t.bilibili.com/381040221372962034?tab=2"
        assert _extract_dynamic_id(uri) == "381040221372962034"

    def test_invalid_uri_returns_empty(self):
        assert _extract_dynamic_id("https://www.bilibili.com/video/BV1xx") == ""
        assert _extract_dynamic_id("https://space.bilibili.com/12345") == ""
        assert _extract_dynamic_id("") == ""
        assert _extract_dynamic_id("not-a-uri") == ""


# ===================================================================
# _extract_opus_text（测试辅助函数，文档化期望的 opus 文字提取逻辑）
# ===================================================================

class TestExtractOpusText:
    """opus/detail API 的 array-format modules → 纯文字。"""

    def test_with_content_module(self):
        """MODULE_TYPE_CONTENT 存在时正确提取文字。"""
        data = {
            "item": {
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "text": {
                                        "nodes": [{"word": {"words": "这是动态文字内容"}}]
                                    }
                                }
                            ]
                        },
                    },
                    {"module_type": "MODULE_TYPE_STAT"},
                ],
            }
        }
        assert _extract_opus_text(data) == "这是动态文字内容"

    def test_multiple_paragraphs_concat(self):
        """多段落的文字拼接在一起。"""
        data = {
            "item": {
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {"text": {"nodes": [{"word": {"words": "第一段"}}]}},
                                {"text": {"nodes": [{"word": {"words": "第二段"}}]}},
                            ]
                        },
                    }
                ],
            }
        }
        assert _extract_opus_text(data) == "第一段第二段"

    def test_no_content_module_returns_empty(self):
        """没有 MODULE_TYPE_CONTENT 时返回空字符串。"""
        data = {
            "item": {
                "modules": [{"module_type": "MODULE_TYPE_STAT"}],
            }
        }
        assert _extract_opus_text(data) == ""

    def test_empty_response_returns_empty(self):
        assert _extract_opus_text({}) == ""
        assert _extract_opus_text({"item": {}}) == ""
        assert _extract_opus_text({"item": {"modules": []}}) == ""


# ===================================================================
# _enrich_events — 动态 enrich（dict 格式 modules，向后兼容）
# ===================================================================

class TestDynamicEnrich:
    """非 opus 动态的标准 enrichment 路径。"""

    def test_desc_text_extracted(self, source):
        """detail API 返回 dict 格式 modules 含 desc.text → event.video_title 赋值。"""
        dynamic_id = "999"
        client = make_mock_client({
            "code": 0,
            "data": {
                "item": {
                    "id_str": dynamic_id,
                    "type": "DYNAMIC_TYPE_WORD",
                    "modules": {
                        "module_dynamic": {
                            "desc": {"text": "传统动态文字"},
                            "major": {"type": "MAJOR_TYPE_NONE"},
                        }
                    },
                }
            },
        })

        event = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        assert event.video_title == "传统动态文字"

        # 验证调用了正确的 detail 端点
        call_args = client.get.call_args
        assert call_args is not None
        assert "x/polymer/web-dynamic/v1/detail" in call_args[0][0]

    def test_text_and_images_extracted(self, source):
        """一次 enrich 同时提取文字（opus 格式）和图片（detail API）。"""
        dynamic_id = "1201036034656174087"

        # 两个不同 API 返回不同格式：detail API 有图片，opus/detail API 有文字
        detail_response = MagicMock()
        detail_response.json.return_value = {
            "code": 0,
            "data": {"item": {
                "id_str": dynamic_id, "type": "DYNAMIC_TYPE_DRAW",
                "modules": {"module_dynamic": {
                    "desc": None,
                    "major": {"type": "MAJOR_TYPE_DRAW", "draw": {
                        "items": [{"src": "http://e.g/img1.jpg"}, {"src": "http://e.g/img2.jpg"}]
                    }}
                }}
            }}
        }
        opus_response = MagicMock()
        opus_response.json.return_value = {
            "code": 0,
            "data": {"item": {
                "id_str": dynamic_id, "type": "DYNAMIC_TYPE_DRAW",
                "modules": [
                    {"module_type": "MODULE_TYPE_CONTENT", "module_content": {
                        "paragraphs": [{"text": {"nodes": [{"word": {"words": "图文动态内容"}}]}}]
                    }}
                ]
            }}
        }

        def get_side_effect(url, **kwargs):
            if "opus/detail" in url:
                return opus_response
            return detail_response

        client = MagicMock()
        client.get.side_effect = get_side_effect
        client.sign_wbi.return_value = {"mid": "789", "wts": "123", "w_rid": "abc"}

        event = make_event(
            "dynamic_draw", dynamic_id,
            f"https://www.bilibili.com/opus/{dynamic_id}",
        )
        source._enrich_events([event], client)

        assert event.video_title == "图文动态内容"
        assert event.images == [
            "http://e.g/img1.jpg",
            "http://e.g/img2.jpg",
        ]

    def test_forward_dynamic_orig_text(self, source):
        """转发动态：本层 desc.text 为空时从 orig.modules 提取。"""
        dynamic_id = "1201036034656174088"
        client = make_mock_client({
            "code": 0,
            "data": {
                "item": {
                    "id_str": dynamic_id,
                    "type": "DYNAMIC_TYPE_FORWARD",
                    "modules": {
                        "module_dynamic": {
                            "desc": {"text": ""},
                            "major": {"type": "MAJOR_TYPE_NONE"},
                        }
                    },
                    "orig": {
                        "modules": {
                            "module_dynamic": {
                                "desc": {"text": "原始动态的文字内容"},
                            }
                        }
                    },
                }
            },
        })

        event = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        assert event.video_title == "原始动态的文字内容"

    def test_forward_dynamic_orig_images(self, source):
        """转发动态：本层无图片时从 orig 提取。"""
        dynamic_id = "1201036034656174089"
        client = make_mock_client({
            "code": 0,
            "data": {
                "item": {
                    "id_str": dynamic_id,
                    "type": "DYNAMIC_TYPE_FORWARD",
                    "modules": {
                        "module_dynamic": {
                            "desc": {"text": ""},
                            "major": {"type": "MAJOR_TYPE_NONE"},
                        }
                    },
                    "orig": {
                        "modules": {
                            "module_dynamic": {
                                "major": {
                                    "type": "MAJOR_TYPE_DRAW",
                                    "draw": {
                                        "items": [{"src": "http://example.com/orig.jpg"}],
                                    },
                                }
                            }
                        }
                    },
                }
            },
        })

        event = make_event("dynamic_draw", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        assert event.images == ["http://example.com/orig.jpg"]

    def test_empty_uri_skips(self, source):
        """没有 URI 的事件被跳过，不调 API。"""
        client = MagicMock()
        event = make_event("dynamic", "oid1", uri="")
        source._enrich_events([event], client)
        client.get.assert_not_called()
        assert event.video_title == ""

    def test_detail_api_failure(self, source):
        """detail API 返回非零 code → 不提取文字，video_title 保持空。"""
        dynamic_id = "1201036034656174090"
        client = make_mock_client({"code": -1, "message": "error"})

        event = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        assert event.video_title == ""  # enrichment 无法提取


# ===================================================================
# 文章 enrich
# ===================================================================

class TestArticleEnrich:
    """business_type == article 时的 enrichment。"""

    def test_article_id_used_for_api_call(self, source):
        """文章 enrich 用 event.oid 作为 article_id 调用 /x/article/view。"""
        article_id = "48872105"
        client = make_mock_client({
            "code": 0,
            "data": {
                "title": "专栏标题",
                "summary": "专栏摘要文字很长的内容",
                "content": "<p>正文</p>",
                "image_urls": ["http://example.com/cover.jpg"],
            },
        })

        event = make_event("article", article_id, video_title="")
        source._enrich_events([event], client)

        assert event.video_title == "专栏标题"
        assert event.video_desc == "专栏摘要文字很长的内容"

        call_args = client.get.call_args
        assert call_args is not None
        assert "x/article/view" in call_args[0][0]
        assert call_args[1]["params"]["id"] == article_id

    def test_article_fallback_when_api_fails(self, source):
        """文章 API 返回非零 code：event.video_title 保持为空（原始 payload 无标题）。"""
        article_id = "48872105"
        client = make_mock_client({"code": -1, "message": "error"})

        event = make_event("article", article_id, video_title="")
        source._enrich_events([event], client)

        assert event.video_title == ""  # enrichment 失败，保持原样

    def test_article_skip_when_title_already_set(self, source):
        """event.video_title 已有值时不调文章 API。"""
        article_id = "48872105"
        client = MagicMock()

        event = make_event("article", article_id, video_title="已有标题")
        source._enrich_events([event], client)

        client.get.assert_not_called()
        assert event.video_title == "已有标题"


# ===================================================================
# _dynamic_title_cache
# ===================================================================

class TestDynamicTitleCache:
    """mention 写入缓存 → reply 读取缓存的跨请求共享机制。"""

    def test_mention_writes_to_cache(self, source):
        """mention 事件写入 _dynamic_title_cache。"""
        dynamic_id = "1201036034656174091"
        client = MagicMock()

        event = make_event(
            "dynamic", dynamic_id,
            f"https://t.bilibili.com/{dynamic_id}",
            source_type="mention",
            video_title="mention 设置的标题",
        )
        source._enrich_events([event], client)

        client.get.assert_not_called()  # mention 不走 API
        assert _dynamic_title_cache.get(dynamic_id) == "mention 设置的标题"

    def test_reply_reads_from_cache(self, source):
        """reply 事件读取缓存中的标题，不调 API。"""
        dynamic_id = "1201036034656174092"
        _dynamic_title_cache[dynamic_id] = "缓存的标题"

        client = MagicMock()
        event = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        client.get.assert_not_called()  # 有缓存不走 API
        assert event.video_title == "缓存的标题"

    def test_reply_ignores_forward_title_from_mention(self, source):
        """mention 不缓存 '转发动态' / '分享动态' 这类无效标题。"""
        dynamic_id = "1201036034656174093"

        for bad_title in ("转发动态", "分享动态"):
            _dynamic_title_cache.clear()
            client = MagicMock()
            event = make_event(
                "dynamic", dynamic_id,
                f"https://t.bilibili.com/{dynamic_id}",
                source_type="mention",
                video_title=bad_title,
            )
            source._enrich_events([event], client)
            assert dynamic_id not in _dynamic_title_cache, (
                f"'{bad_title}' 不应写入缓存"
            )

    def test_enrich_sets_cache_for_reply(self, source):
        """reply 事件从 detail API 提取到文字时也写入缓存（后续读取可用）。"""
        dynamic_id = "1201036034656174094"
        client = make_mock_client({
            "code": 0,
            "data": {
                "item": {
                    "id_str": dynamic_id,
                    "type": "DYNAMIC_TYPE_WORD",
                    "modules": {
                        "module_dynamic": {
                            "desc": {"text": "API 提取的标题"},
                            "major": {"type": "MAJOR_TYPE_NONE"},
                        }
                    },
                }
            },
        })

        event = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event], client)

        assert _dynamic_title_cache.get(dynamic_id) == "API 提取的标题"

        # 第二个事件读取缓存，不调 API
        client2 = MagicMock()
        event2 = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event2], client2)
        client2.get.assert_not_called()
        assert event2.video_title == "API 提取的标题"

    def test_cache_is_global_dict(self):
        """_dynamic_title_cache 是模块级 dict，可在实例间共享。"""
        assert isinstance(_dynamic_title_cache, dict)


# ===================================================================
# _enrich_users
# ===================================================================

class TestEnrichUsers:
    """_enrich_users 调用验证。"""

    def test_called_exactly_once(self):
        """_enrich_events 结束时调用一次 _enrich_users。"""
        config = MagicMock()
        config.sources.msgfeed.page_size = 10
        source = MsgFeedReplySource(config)

        with patch.object(source, "_enrich_users") as mock_enrich:
            client = make_mock_client({
                "code": 0,
                "data": {
                    "item": {
                        "id_str": "1201036034656174098",
                        "type": "DYNAMIC_TYPE_WORD",
                        "modules": {
                            "module_dynamic": {
                                "desc": {"text": "test"},
                                "major": {"type": "MAJOR_TYPE_NONE"},
                            }
                        },
                    }
                },
            })
            event = make_event("dynamic", "1201036034656174098", "https://t.bilibili.com/1201036034656174098")
            source._enrich_events([event], client)
            mock_enrich.assert_called_once_with([event], client)

    def test_called_even_with_empty_events(self):
        """没有需要 enrich 的事件时仍然调用 _enrich_users。"""
        config = MagicMock()
        config.sources.msgfeed.page_size = 10
        source = MsgFeedReplySource(config)

        with patch.object(source, "_enrich_users") as mock_enrich:
            client = MagicMock()
            source._enrich_events([], client)
            mock_enrich.assert_called_once_with([], client)


# ===================================================================
# 集成场景：多个事件的 enrich 流水线
# ===================================================================

class TestEnrichPipeline:
    """多个事件混合时的处理顺序和缓存共享。"""

    def test_mixed_events_processed_independently(self, source):
        """不同类型的事件各自正确 enrich。"""
        dyn_id_1 = "1201036034656174095"
        dyn_id_2 = "1201036034656174096"
        responses_by_id = {
            dyn_id_1: {
                "code": 0,
                "data": {
                    "item": {
                        "id_str": dyn_id_1,
                        "type": "DYNAMIC_TYPE_WORD",
                        "modules": {
                            "module_dynamic": {
                                "desc": {"text": "动态一"},
                                "major": {"type": "MAJOR_TYPE_NONE"},
                            }
                        },
                    }
                },
            },
            dyn_id_2: {
                "code": 0,
                "data": {
                    "item": {
                        "id_str": dyn_id_2,
                        "type": "DYNAMIC_TYPE_DRAW",
                        "modules": {
                            "module_dynamic": {
                                "desc": {"text": "动态二"},
                                "major": {
                                    "type": "MAJOR_TYPE_DRAW",
                                    "draw": {
                                        "items": [{"src": "http://example.com/pic.jpg"}],
                                    },
                                },
                            }
                        },
                    }
                },
            },
        }

        # 根据动态 ID 返回对应的 mock 响应
        def get_side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            did = params.get("id", "")
            data = responses_by_id.get(did)
            if data is not None:
                resp = MagicMock()
                resp.json.return_value = data
                return resp
            resp = MagicMock()
            resp.json.return_value = {"code": -1}
            return resp

        client = MagicMock()
        client.get.side_effect = get_side_effect
        client.sign_wbi.return_value = {"mid": "789", "wts": "123", "w_rid": "abc"}

        event1 = make_event("dynamic", dyn_id_1, f"https://t.bilibili.com/{dyn_id_1}")
        event2 = make_event("dynamic_draw", dyn_id_2, f"https://t.bilibili.com/{dyn_id_2}")
        source._enrich_events([event1, event2], client)

        assert event1.video_title == "动态一"
        assert event1.images == []

        assert event2.video_title == "动态二"
        assert event2.images == ["http://example.com/pic.jpg"]

    def test_cache_shared_across_multiple_reply_events(self, source):
        """多个 reply 事件共享 _dynamic_title_cache，只调一次 API。"""
        dynamic_id = "1201036034656174097"

        # 第一个事件 → API 调用 → 写入缓存
        client1 = make_mock_client({
            "code": 0,
            "data": {
                "item": {
                    "id_str": dynamic_id,
                    "type": "DYNAMIC_TYPE_WORD",
                    "modules": {
                        "module_dynamic": {
                            "desc": {"text": "共享标题"},
                            "major": {"type": "MAJOR_TYPE_NONE"},
                        }
                    },
                }
            },
        })
        event1 = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event1], client1)
        assert _dynamic_title_cache.get(dynamic_id) == "共享标题"

        # 第二、三个事件 → 走缓存，不调 API
        client2 = MagicMock()
        event2 = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        event3 = make_event("dynamic", dynamic_id, f"https://t.bilibili.com/{dynamic_id}")
        source._enrich_events([event2, event3], client2)
        client2.get.assert_not_called()
        assert event2.video_title == "共享标题"
        assert event3.video_title == "共享标题"
