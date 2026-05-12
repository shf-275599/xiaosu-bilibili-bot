"""AI Provider 管理 — PydanticAI Agent 会话级对话管理。

v4: 裁剪LLM摘要 + 摘要持久化 + 可配TTL/上限
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import Usage

from bilibili_bot.tools import TOOLS

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from bilibili_bot.atomic_state import AtomicStateStore

logger = structlog.get_logger()

MAX_SESSIONS = 500
SUMMARY_MAX_TOKENS = 500


@dataclass
class BotDeps:
    config: Any = None


@dataclass
class ReplyResult:
    success: bool
    text: str = ""
    provider: str = ""
    error: str = ""
    retriable: bool = False
    tool_calls: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


class ProviderManager:
    def __init__(self, config, atomic_store: Any = None):
        self._config = config
        self._store = atomic_store
        self._sessions: dict[str, _AgentSession] = {}
        self._deps = BotDeps(config=config)

    @property
    def _ttl(self) -> int:
        return getattr(self._config.ai, "session_ttl_seconds", 3600)

    @property
    def _history_max(self) -> int:
        return getattr(self._config.ai, "history_max_messages", 50)

    def chat(self, session_key: str, system_prompt: str,
             user_message: str, stream: bool = False) -> ReplyResult:
        session = self._get_or_create_session(session_key, system_prompt)
        session.touch()

        if session.summary and not session.history:
            user_message = f"[之前的对话摘要] {session.summary}\n\n{user_message}"
            session.summary = ""

        try:
            if stream:
                return self._chat_stream(session, user_message)

            result = session.agent.run_sync(
                user_prompt=user_message,
                message_history=session.history,
                deps=self._deps,
                model_settings=ModelSettings(
                    temperature=self._config.reply.temperature,
                    max_tokens=self._config.reply.max_tokens,
                ),
            )
            session.history = result.all_messages()
            self._trim_history(session, session_key)
            return _result_to_reply(result)
        except Exception as e:
            logger.warning("agent_chat_failed", error=str(e), session=session_key)
            return ReplyResult(False, error=str(e), retriable=True)

    def _chat_stream(self, session: _AgentSession, user_message: str) -> ReplyResult:
        import sys
        full_text = ""
        try:
            with session.agent.run_stream(
                user_prompt=user_message,
                message_history=session.history,
                deps=self._deps,
                model_settings=ModelSettings(
                    temperature=self._config.reply.temperature,
                    max_tokens=self._config.reply.max_tokens,
                ),
            ) as stream:
                for text in stream.stream_text(delta=True):
                    full_text += text
                    sys.stdout.write(text)
                    sys.stdout.flush()
                sys.stdout.write("\n")
            return ReplyResult(True, text=full_text.strip(), provider="deepseek")
        except Exception as e:
            return ReplyResult(False, error=str(e), retriable=True)

    def _get_or_create_session(self, key: str, system_prompt: str) -> _AgentSession:
        self._prune()
        if key in self._sessions:
            return self._sessions[key]

        cfg = self._config.ai.providers[self._config.ai.primary_provider]
        api_key = os.environ.get(cfg.api_key_env or "", "")
        p = OpenAIProvider(base_url=(cfg.base_url or "").rstrip("/"), api_key=api_key)
        model = OpenAIChatModel(cfg.model or "", provider=p)
        agent = Agent(model, system_prompt=system_prompt, tools=TOOLS, deps_type=BotDeps)
        session = _AgentSession(agent=agent, created_at=time.time())

        # 从持久化存储恢复摘要
        saved = self._load_summary(key)
        if saved:
            session.summary = saved

        if len(self._sessions) >= MAX_SESSIONS:
            oldest = min(self._sessions, key=lambda k: self._sessions[k].last_used)
            self._prune_one(oldest)
            del self._sessions[oldest]
        self._sessions[key] = session
        return session

    def _prune(self) -> None:
        now = time.time()
        for k in list(self._sessions):
            if now - self._sessions[k].last_used > self._ttl:
                self._prune_one(k)
                del self._sessions[k]

    def _prune_one(self, key: str) -> None:
        """删除session前生成摘要并持久化。"""
        session = self._sessions.get(key)
        if not session or len(session.history) < 4:
            return
        summary = self._summarize(session.history)
        if summary:
            self._save_summary(key, summary)

    def _trim_history(self, session: _AgentSession, key: str) -> None:
        """裁剪前生成摘要（裁剪后摘要+最近消息保留）。"""
        if len(session.history) <= self._history_max:
            return

        keep = max(20, self._history_max * 3 // 5)
        old_msgs = session.history[1:-keep]
        if len(old_msgs) <= 2:
            return

        summary = self._summarize(old_msgs)
        if not summary:
            session.history = [session.history[0]] + session.history[-keep:]
            return

        self._save_summary(key, summary)
        hint = f"[对话历史摘要] {summary}"
        from pydantic_ai.messages import ModelRequest, SystemPromptPart
        summary_msg = ModelRequest(parts=[SystemPromptPart(content=hint)])
        session.history = [session.history[0], summary_msg] + session.history[-keep:]

    def _summarize(self, messages: list) -> str:
        """调用 LLM 生成详细摘要。"""
        lines = []
        for msg in messages:
            for part in msg.parts:
                content = getattr(part, 'content', '') or ''
                if content and len(content) > 5:
                    role = "bot" if hasattr(msg, 'parts') and any(
                        'ThinkingPart' in type(p).__name__ or 'TextPart' in type(p).__name__
                        for p in msg.parts) else "user"
                    lines.append(f"{role}: {content[:200]}")
                    break
        text = "\n".join(lines[-30:])
        if not text.strip():
            return ""

        result = _chat_simple(
            self._config,
            "详细总结以下对话，列出每个被讨论过的话题和关键信息，尽可能还原上下文，方便后续继续对话：",
            text,
        )
        if result.success and result.text:
            return result.text[:500]
        return ""

    def _save_summary(self, key: str, summary: str) -> None:
        """持久化摘要到 AtomicStateStore。"""
        if not self._store:
            return
        try:
            self._store.atomic_getset(
                "session_summaries", key, value=summary,
            )
        except Exception:
            pass

    def _load_summary(self, key: str) -> str:
        """从持久化存储读取摘要。"""
        if not self._store:
            return ""
        try:
            state = self._store.load_state()
            summaries = state.get("session_summaries", {})
            return summaries.pop(key, "") if isinstance(summaries, dict) else ""
        except Exception:
            return ""


class _AgentSession:
    def __init__(self, agent: Agent, created_at: float):
        self.agent = agent
        self.created_at = created_at
        self.last_used = created_at
        self.history: list[ModelMessage] = []
        self.summary: str = ""

    def touch(self) -> None:
        self.last_used = time.time()


def _result_to_reply(result) -> ReplyResult:
    tool_calls: list[str] = []
    token_usage: dict[str, int] = {}
    try:
        for msg in result.all_messages():
            for part in msg.parts:
                name = getattr(part, "tool_name", "") or ""
                if name and name not in tool_calls:
                    tool_calls.append(name)
    except Exception:
        pass
    try:
        u: Usage = result.usage()
        token_usage = {"request": u.request_tokens or 0,
                       "response": u.response_tokens or 0,
                       "total": u.total_tokens or 0}
    except Exception:
        pass
    return ReplyResult(success=True, text=str(result.output),
                       provider="deepseek", tool_calls=tool_calls, usage=token_usage)


def _chat_simple(config, system_prompt: str, user_message: str) -> ReplyResult:
    """纯 PydanticAI 单轮调用（无 tools，用于摘要等轻量任务）。"""
    cfg = config.ai.providers[config.ai.primary_provider]
    api_key = os.environ.get(cfg.api_key_env or "", "")
    p = OpenAIProvider(base_url=(cfg.base_url or "").rstrip("/"), api_key=api_key)
    model = OpenAIChatModel(cfg.model or "", provider=p)
    agent = Agent(model, system_prompt=system_prompt)
    try:
        result = agent.run_sync(
            user_message,
            model_settings=ModelSettings(temperature=config.reply.temperature, max_tokens=SUMMARY_MAX_TOKENS),
        )
        return ReplyResult(True, text=str(result.output), provider="deepseek")
    except Exception as e:
        return ReplyResult(False, error=str(e))
