"""PipelineContext 服务接口类型定义。

替代 v2 中 PipelineContext 的 Any 类型字段。
使用 typing.Protocol 实现结构化类型，支持 mock 替换。
"""

from __future__ import annotations

from typing import Any, Protocol

from bilibili_bot.atomic_state import DedupStatus


class DedupProtocol(Protocol):
    """去重服务接口。"""

    def is_duplicate(self, key: str) -> DedupStatus: ...

    def mark_seen(
        self, event_key: str, reason: str, event: dict[str, Any] | None = None
    ) -> None: ...

    def mark_failed(
        self,
        event_key: str,
        reason: str,
        provider: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> None: ...

    def mark_replied(
        self,
        event_key: str,
        event: dict[str, Any],
        reply_text: str,
        provider: str,
        tool_calls: list[str] | None = None,
    ) -> None: ...


class RateProtocol(Protocol):
    """频控服务接口。"""

    def can_send(self, user_id: str = "", oid: str = "") -> tuple[bool, str]: ...

    def can_run_source(self, name: str) -> tuple[bool, str]: ...

    def wait_for_request_slot(self) -> float: ...

    def wait_before_send(self) -> float: ...

    def record_success(self, user_id: str = "", oid: str = "") -> None: ...

    def record_failure(self, retriable: bool) -> float: ...

    def record_source_success(self, name: str) -> None: ...

    def record_source_failure(self, name: str) -> float: ...
