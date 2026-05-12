from __future__ import annotations

import os

import requests
import structlog
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from bilibili_bot.providers.base import BaseProvider, ReplyResult
from bilibili_bot.tools import TOOLS

logger = structlog.get_logger()


def _agent_result_to_reply(result, provider_name: str) -> ReplyResult:
    tool_calls: list[str] = []
    try:
        messages = result.all_messages()
        for msg in messages:
            for part in msg.parts:
                part_name = type(part).__name__
                if "ToolCall" in part_name:
                    name = getattr(part, "tool_name", "")
                    if name:
                        tool_calls.append(name)
    except Exception:
        pass

    return ReplyResult(
        success=True,
        text=str(result.output),
        provider=provider_name,
        tool_calls=tool_calls,
    )


def _create_pydantic_agent(system_prompt: str, config, provider_name: str) -> Agent:
    """创建配置好的 PydanticAI Agent。"""
    provider_cfg = config.ai.providers.get(provider_name)
    if not provider_cfg:
        raise ValueError(f"未找到 AI Provider 配置: {provider_name}")
    if provider_cfg.type != "openai_compatible":
        raise ValueError(f"Provider {provider_name} 类型不是 openai_compatible")

    api_key = os.environ.get(provider_cfg.api_key_env or "", "")
    provider = OpenAIProvider(base_url=(provider_cfg.base_url or "").rstrip("/"), api_key=api_key)
    model = OpenAIChatModel(provider_cfg.model or "", provider=provider)
    return Agent(model, system_prompt=system_prompt, tools=TOOLS)


class OpenAICompatibleProvider(BaseProvider):
    def generate(self, messages: list[dict[str, str]]) -> ReplyResult:
        return self._call_api(messages)

    def _call_api(
        self,
        messages: list[dict[str, str]],
    ) -> ReplyResult:
        api_key_env = self.provider_config.get("api_key_env", "")
        api_key = os.environ.get(api_key_env, "")

        if not api_key:
            return ReplyResult(False, provider=self.name, error=f"缺少环境变量 {api_key_env}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        base_url = self.provider_config.get("base_url", "").rstrip("/")
        model = self.provider_config.get("model", "")

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.global_config.reply.temperature,
            "max_tokens": self.global_config.reply.max_tokens,
        }

        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.global_config.ai.timeout_seconds,
            )
        except requests.RequestException as exc:
            return ReplyResult(False, provider=self.name, error=str(exc), retriable=True)

        retriable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
        if response.status_code != 200:
            return ReplyResult(
                False,
                provider=self.name,
                error=f"HTTP {response.status_code}: {response.text[:300]}",
                retriable=retriable,
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message", {})

            text = message.get("content", "").strip()
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            return ReplyResult(
                False,
                provider=self.name,
                error=f"响应解析失败: {exc}",
                retriable=False,
                raw=response.text,
            )

        if not text:
            return ReplyResult(
                False,
                provider=self.name,
                error="Provider 返回空回复",
                retriable=False,
                raw=data,
            )

        return ReplyResult(True, text=text, provider=self.name, raw=data)
