from __future__ import annotations

import json
import os
from typing import Any, Callable

import requests
import structlog

from bilibili_bot.providers.base import BaseProvider, ReplyResult

logger = structlog.get_logger()


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

    def generate_with_tools(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], str],
        max_iterations: int = 3,
    ) -> ReplyResult:
        current_messages = list(messages)

        for _ in range(max_iterations):
            result = self._call_api(current_messages, tools=tools)

            if result.success:
                return result

            if result.error == "TOOL_CALLS" and result.raw:
                tool_calls = result.raw["tool_calls"]
                full_msg = result.raw.get("full_message", {})

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": full_msg.get("content"),
                    "tool_calls": tool_calls,
                }
                # DeepSeek thinking mode 要求回传 reasoning_content
                if full_msg.get("reasoning_content"):
                    assistant_msg["reasoning_content"] = full_msg["reasoning_content"]
                current_messages.append(assistant_msg)

                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    try:
                        fn_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        fn_args = {}
                    logger.info("tool_call_start", tool=fn_name, args=fn_args)
                    tool_result = tool_executor(fn_name, fn_args)
                    logger.info("tool_call_end", tool=fn_name, result_preview=tool_result[:200] if tool_result else "")
                    current_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result,
                    })
                continue

            error_msg = result.error
            return result

        return ReplyResult(
            False,
            provider=self.name,
            error=f"Tool calling 超过最大迭代次数 ({max_iterations})",
            retriable=False,
        )
