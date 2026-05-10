from __future__ import annotations

import os
from typing import Any

import requests
import structlog
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from bilibili_bot.providers.base import BaseProvider, ReplyResult
from bilibili_bot.tools import TOOLS

logger = structlog.get_logger()


def _messages_to_agent_input(messages: list[dict[str, str]]) -> tuple[str, list[ModelRequest | ModelResponse] | None]:
    """将 openai_compat 消息格式转为 PydanticAI 的 (user_prompt, message_history)。

    messages[0] = system prompt（由 Agent 单独设置）
    messages[-1] = 当前用户输入
    messages[1:-1] = 对话历史
    """
    if not messages:
        return "", None

    user_prompt = messages[-1].get("content", "")
    history: list[ModelRequest | ModelResponse] = []
    for msg in messages[1:-1]:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=content)]))
    return user_prompt, history if history else None


def _agent_result_to_reply(result, provider_name: str) -> ReplyResult:
    """将 PydanticAI AgentRunResult 转为 ReplyResult。"""
    return ReplyResult(success=True, text=str(result.output), provider=provider_name)


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
        tools: list[dict[str, Any]] | None = None,
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

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self.global_config.reply.temperature,
            "max_tokens": self.global_config.reply.max_tokens,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

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

            if message.get("tool_calls") and tools:
                return ReplyResult(
                    False,
                    provider=self.name,
                    error="TOOL_CALLS",
                    retriable=False,
                    raw={
                        "tool_calls": message["tool_calls"],
                        "full_message": message,  # 保留 reasoning_content 等字段
                    },
                )

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

    def generate_with_tools(self, messages: list[dict[str, str]]) -> ReplyResult:
        """使用 PydanticAI Agent 进行带工具调用的回复生成。"""
        if not messages:
            return ReplyResult(False, provider=self.name, error="空消息")

        system_prompt = messages[0].get("content", "") if messages else ""
        agent = _create_pydantic_agent(system_prompt, self.global_config, self.name)

        user_prompt, message_history = _messages_to_agent_input(messages)

        try:
            result = agent.run_sync(
                user_prompt=user_prompt,
                message_history=message_history,
                model_settings=ModelSettings(
                    temperature=self.global_config.reply.temperature,
                    max_tokens=self.global_config.reply.max_tokens,
                    timeout=self.global_config.ai.timeout_seconds,
                ),
                usage_limits=UsageLimits(
                    request_limit=self.global_config.ai.tool_max_iterations + 1,
                ),
            )
            return _agent_result_to_reply(result, self.name)
        except Exception as e:
            return ReplyResult(False, provider=self.name, error=f"Agent 执行失败: {e}", retriable=True)
