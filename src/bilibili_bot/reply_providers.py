#!/usr/bin/env python3
"""回复 Provider 抽象层。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class ReplyResult:
    success: bool
    text: str = ""
    provider: str = ""
    error: str = ""
    retriable: bool = False
    raw: Any = None


class OpenAICompatibleProvider:
    def __init__(self, name: str, provider_config: dict, global_config: dict):
        self.name = name
        self.provider_config = provider_config
        self.global_config = global_config

    def generate(self, messages: list[dict[str, str]]) -> ReplyResult:
        api_key = os.environ.get(self.provider_config["api_key_env"], "")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.provider_config["model"],
            "messages": messages,
            "temperature": self.global_config["reply"].get("temperature", 0.7),
            "max_tokens": self.global_config["reply"].get("max_tokens", 200),
        }
        try:
            response = requests.post(
                f"{self.provider_config['base_url'].rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.global_config["ai"].get("timeout_seconds", 25),
            )
        except requests.RequestException as exc:
            return ReplyResult(False, provider=self.name, error=str(exc), retriable=True)

        retriable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
        if response.status_code != 200:
            return ReplyResult(False, provider=self.name, error=f"HTTP {response.status_code}: {response.text[:300]}", retriable=retriable)

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            return ReplyResult(False, provider=self.name, error=f"响应解析失败: {exc}", retriable=False, raw=response.text)

        if not text:
            return ReplyResult(False, provider=self.name, error="Provider 返回空回复", retriable=False, raw=data)

        return ReplyResult(True, text=text, provider=self.name, raw=data)


class OpenCodeFallbackProvider:
    def __init__(self, name: str, provider_config: dict, global_config: dict):
        self.name = name
        self.provider_config = provider_config
        self.global_config = global_config

    def generate(self, messages: list[dict[str, str]]) -> ReplyResult:
        user_prompt = "\n\n".join(part["content"] for part in messages)
        user_prompt = user_prompt.replace("\x00", "")
        if len(user_prompt) > 10000:
            user_prompt = user_prompt[:10000]
        command = [
            self.provider_config.get("command", "opencode"),
            "run",
            "--format",
            "json",
            "--dir",
            self.provider_config.get("dir", "."),
            user_prompt,
        ]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=self.global_config["ai"].get("timeout_seconds", 25) + 30)
        except (subprocess.SubprocessError, OSError) as exc:
            return ReplyResult(False, provider=self.name, error=str(exc), retriable=True)

        if proc.returncode != 0:
            return ReplyResult(False, provider=self.name, error=proc.stderr.strip() or proc.stdout.strip(), retriable=True)

        final_text = ""
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "text":
                final_text = payload.get("part", {}).get("text", final_text)

        if not final_text.strip():
            return ReplyResult(False, provider=self.name, error="OpenCode fallback 未返回文本", retriable=False, raw=proc.stdout)

        return ReplyResult(True, text=final_text.strip(), provider=self.name, raw=proc.stdout)


class ReplyProviderManager:
    def __init__(self, config: dict):
        self.config = config
        providers = config["ai"]["providers"]
        self.primary_name = config["ai"]["primary_provider"]
        self.fallback_name = config["ai"]["fallback_provider"]
        self.primary = self._build_provider(self.primary_name, providers[self.primary_name])
        self.fallback = self._build_provider(self.fallback_name, providers[self.fallback_name])

    def _build_provider(self, name: str, provider_config: dict):
        provider_type = provider_config.get("type")
        if provider_type == "openai_compatible":
            return OpenAICompatibleProvider(name, provider_config, self.config)
        if provider_type == "opencode_local":
            return OpenCodeFallbackProvider(name, provider_config, self.config)
        raise ValueError(f"不支持的 provider type: {provider_type}")

    def generate_reply(self, messages: list[dict[str, str]]) -> ReplyResult:
        primary_result = self.primary.generate(messages)
        if primary_result.success:
            return primary_result
        fallback_result = self.fallback.generate(messages)
        if fallback_result.success:
            return fallback_result
        fallback_result.error = (
            f"主 Provider 失败: {primary_result.error} | fallback 失败: {fallback_result.error}"
        )
        return fallback_result
