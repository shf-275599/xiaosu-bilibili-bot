"""PydanticAI Agent 集成测试。"""
from unittest.mock import MagicMock


class TestMessageConversion:
    def test_extracts_user_prompt_from_last_message(self):
        from bilibili_bot.providers.openai_compat import _messages_to_agent_input

        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
        ]
        user_prompt, history = _messages_to_agent_input(messages)
        assert user_prompt == "你好"
        assert history is None

    def test_extracts_intermediate_as_history(self):
        from bilibili_bot.providers.openai_compat import _messages_to_agent_input

        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "问题1"},
            {"role": "assistant", "content": "回答1"},
            {"role": "user", "content": "问题2"},
        ]
        user_prompt, history = _messages_to_agent_input(messages)
        assert user_prompt == "问题2"
        assert history is not None
        assert len(history) == 2

    def test_empty_messages(self):
        from bilibili_bot.providers.openai_compat import _messages_to_agent_input

        user_prompt, history = _messages_to_agent_input([])
        assert user_prompt == ""
        assert history is None


class TestAgentResultConversion:
    def test_creates_success_reply(self):
        from bilibili_bot.providers.openai_compat import _agent_result_to_reply

        result = MagicMock()
        result.output = "你好世界"

        reply = _agent_result_to_reply(result, "test-provider")
        assert reply.success is True
        assert reply.text == "你好世界"
        assert reply.provider == "test-provider"
