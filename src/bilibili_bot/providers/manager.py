from __future__ import annotations

import structlog

from bilibili_bot.providers.base import BaseProvider, ReplyResult
from bilibili_bot.providers.openai_compat import OpenAICompatibleProvider

logger = structlog.get_logger()


class ProviderManager:
    def __init__(self, config):
        self.config = config
        providers = config.ai.providers
        self.primary_name = config.ai.primary_provider

        self.primary = self._build_provider(self.primary_name, providers[self.primary_name])

    def _build_provider(self, name: str, provider_config) -> BaseProvider:
        provider_type = provider_config.type
        if provider_type == "openai_compatible":
            return OpenAICompatibleProvider(name, provider_config.model_dump(), self.config)
        raise ValueError(f"不支持的 provider type: {provider_type}")

    def generate_reply(self, messages: list[dict[str, str]]) -> ReplyResult:
        return self.primary.generate(messages)
