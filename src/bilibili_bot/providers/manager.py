from __future__ import annotations

import hashlib

import structlog
from pydantic_ai import Agent

from bilibili_bot.providers.base import BaseProvider, ReplyResult
from bilibili_bot.providers.openai_compat import (
    OpenAICompatibleProvider,
    _agent_result_to_reply,
    _create_pydantic_agent,
    _messages_to_agent_input,
)

logger = structlog.get_logger()


class ProviderManager:
    def __init__(self, config):
        self._config = config
        providers = config.ai.providers
        self.primary_name = config.ai.primary_provider
        self.primary = self._build_provider(
            self.primary_name, providers[self.primary_name]
        )
        self._agent_cache: dict[str, Agent] = {}

    def _build_provider(self, name: str, provider_config) -> BaseProvider:
        provider_type = provider_config.type
        if provider_type == "openai_compatible":
            return OpenAICompatibleProvider(
                name, provider_config.model_dump(), self._config
            )
        raise ValueError(f"不支持的 provider type: {provider_type}")

    def generate_reply(self, messages: list[dict[str, str]]) -> ReplyResult:
        if (
            self._config.ai.tools_enabled
            and len(messages) >= 2
            and isinstance(self.primary, OpenAICompatibleProvider)
        ):
            return self._generate_with_tools(messages)
        return self.primary.generate(messages)

    def _generate_with_tools(self, messages: list[dict[str, str]]) -> ReplyResult:
        system_prompt = messages[0].get("content", "") if messages else ""
        agent = self._get_or_create_agent(system_prompt)
        user_prompt, message_history = _messages_to_agent_input(messages)

        try:
            result = agent.run_sync(
                user_prompt=user_prompt,
                message_history=message_history,
            )
            return _agent_result_to_reply(result, self.primary_name)
        except Exception as e:
            logger.warning("tool_generation_failed", error=str(e))
            return self.primary.generate(messages)

    def _get_or_create_agent(self, system_prompt: str) -> Agent:
        key = hashlib.md5(system_prompt.encode()).hexdigest()
        if key not in self._agent_cache:
            self._agent_cache[key] = _create_pydantic_agent(
                system_prompt, self._config, self.primary_name
            )
        return self._agent_cache[key]
