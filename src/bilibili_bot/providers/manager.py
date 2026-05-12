"""AI Provider 管理 — 基于 PydanticAI Agent 的会话级对话管理。

每个会话维护独立 Agent 实例，Agent 通过 message_history 自动管理上下文。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from pydantic_ai import Agent



if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

logger = structlog.get_logger()

SESSION_TTL = 3600
MAX_SESSIONS = 500
HISTORY_MAX = 50


@dataclass
class ReplyResult:
    success: bool
    text: str = ""
    provider: str = ""
    error: str = ""
    retriable: bool = False
    raw: Any = None
    tool_calls: list[str] = field(default_factory=list)


class ProviderManager:
    def __init__(self, config):
        self._config = config
        self._sessions: dict[str, _AgentSession] = {}

    def chat(
        self,
        session_key: str,
        system_prompt: str,
        user_message: str,
        use_tools: bool = True,
    ) -> ReplyResult:
        session = self._get_or_create_session(session_key, system_prompt)
        session.touch()

        try:
            result = session.agent.run_sync(
                user_prompt=user_message,
                message_history=session.history,
            )
            session.history = result.all_messages()
            self._trim_history(session)
            return _result_to_reply(result)
        except Exception as e:
            logger.warning("agent_chat_failed", error=str(e), session=session_key)
            return ReplyResult(False, error=str(e), retriable=True)

    def _get_or_create_session(
        self, key: str, system_prompt: str
    ) -> _AgentSession:
        self._prune()
        if key in self._sessions:
            return self._sessions[key]

        agent = _create_agent(system_prompt, self._config)
        session = _AgentSession(agent=agent, created_at=time.time())
        if len(self._sessions) >= MAX_SESSIONS:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            del self._sessions[oldest]
        self._sessions[key] = session
        return session

    def _prune(self) -> None:
        now = time.time()
        for k in list(self._sessions):
            if now - self._sessions[k].last_used > SESSION_TTL:
                del self._sessions[k]

    def _trim_history(self, session: _AgentSession) -> None:
        if len(session.history) <= HISTORY_MAX:
            return
        session.history = [session.history[0]] + session.history[-30:]


class _AgentSession:
    def __init__(self, agent: Agent, created_at: float):
        self.agent = agent
        self.created_at = created_at
        self.last_used = created_at
        self.history: list[ModelMessage] = []

    def touch(self) -> None:
        self.last_used = time.time()


def _result_to_reply(result) -> ReplyResult:
    tool_calls: list[str] = []
    try:
        for msg in result.all_messages():
            for part in msg.parts:
                name = getattr(part, "tool_name", "") or ""
                if name and name not in tool_calls:
                    tool_calls.append(name)
    except Exception:
        pass

    return ReplyResult(
        success=True,
        text=str(result.output),
        provider="deepseek",
        tool_calls=tool_calls,
    )

# ── Agent 工厂 ──

import os
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from bilibili_bot.tools import TOOLS

def _create_agent(system_prompt: str, config) -> Agent:
    provider_cfg = config.ai.providers.get(config.ai.primary_provider)
    api_key = os.environ.get(provider_cfg.api_key_env or "", "")
    p = OpenAIProvider(base_url=(provider_cfg.base_url or "").rstrip("/"), api_key=api_key)
    model = OpenAIChatModel(provider_cfg.model or "", provider=p)
    return Agent(model, system_prompt=system_prompt, tools=TOOLS)
