"""原子状态存储 — 消除 v2 的全部读-改-写竞态。

设计原则：
- 所有写操作在内置锁（threading.Lock + fcntl.flock）保护下执行
- load_state + save_state 是单次原子 getset 操作
- mark_replied 是单次原子双写（history 先，processed 后）
- compact 操作在整个文件上持排他锁

兼容 v2 数据格式 — data/*.json 和 data/*.jsonl 的文件结构与 v2 完全相同。
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any


class DedupStatus(Enum):
    """事件去重状态。"""

    NEW = "new"
    REPLIED = "replied"
    SEEN = "seen"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FATAL = "failed_fatal"


# ── 去重策略常量 ──

MAX_RETRIES: int = 5
RETRY_COOLDOWN_SECONDS: int = 300
FATAL_COOLDOWN_SECONDS: int = 3600
FATAL_ERROR_KEYWORDS: tuple[str, ...] = (
    "已经被删除", "已被删除", "不存在", "已关闭", "已过期",
)


class AtomicStateStore:
    """原子状态存储。

    使用双重锁：
    - threading.Lock：进程内互斥
    - fcntl.flock：进程间互斥（POSIX，systemd 多进程场景）
    """

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

        self._state_path = self.root / "bot-state.json"
        self._processed_path = self.root / "processed.jsonl"
        self._history_path = self.root / "reply-history.jsonl"

        self._lock = threading.Lock()
        self._file_fd: int | None = None

        # 内存索引：event_key → record
        self._processed_index: dict[str, dict[str, Any]] = {}
        self._load_processed_index()

    # ── 初始化 ──

    def _load_processed_index(self) -> None:
        """从 processed.jsonl 加载内存索引。"""
        if not self._processed_path.exists():
            return
        with self._processed_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    key = record.get("event_key")
                    if key:
                        # 同名 key 保留最后一条（最新状态）
                        self._processed_index[key] = record
                except json.JSONDecodeError:
                    continue

    # ── 原子写操作：bot-state.json ──

    def load_state(self) -> dict[str, Any]:
        """读取完整状态（共享锁）。"""
        if not self._state_path.exists():
            return {}
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    def save_state(self, state: dict[str, Any]) -> None:
        """原子写入完整状态（排他锁）。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=self._state_path.parent, encoding="utf-8"
            ) as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=str)
                tmp_path = f.name
            os.replace(tmp_path, self._state_path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def atomic_getset(
        self, *path: str, value: Any
    ) -> Any:
        """原子读取旧值并写入新值。

        用法: old = store.atomic_getset("rate_limit", "failure_count", 0)
        在 threading.Lock 保护下完成 load→set→save。
        """
        with self._lock:
            state = self.load_state()
            old = _nested_get(state, path)
            _nested_set(state, path, value)
            self.save_state(state)
            return old

    def atomic_increment(self, *path: str, delta: int = 1) -> int:
        """原子自增嵌套字段。

        用法: new_count = store.atomic_increment("rate_limit", "failure_count")
        """
        with self._lock:
            state = self.load_state()
            current = _nested_get(state, path)
            if not isinstance(current, (int, float)):
                current = 0
            new = current + delta
            _nested_set(state, path, new)
            self.save_state(state)
            return new

    def atomic_append(
        self, *path: str, value: Any, max_len: int = 0
    ) -> None:
        """原子追加到嵌套列表。

        用法: store.atomic_append("rate_limit", "reply_timestamps", now)
        """
        with self._lock:
            state = self.load_state()
            lst = _nested_get(state, path)
            if not isinstance(lst, list):
                lst = []
            lst.append(value)
            if max_len > 0 and len(lst) > max_len:
                lst = lst[-max_len:]
            _nested_set(state, path, lst)
            self.save_state(state)

    # ── 去重操作：processed.jsonl ──

    def is_duplicate(self, event_key: str) -> DedupStatus:
        """检查事件状态。"""
        record = self._processed_index.get(event_key)
        if not record:
            return DedupStatus.NEW

        status = record.get("reply_status")
        if status == "replied":
            return DedupStatus.REPLIED
        if status == "seen":
            return DedupStatus.SEEN

        if status == "failed":
            reason = record.get("reason", "")
            if _is_fatal_error(reason):
                return DedupStatus.FAILED_FATAL

            retries = record.get("retries", 0)
            if retries >= MAX_RETRIES:
                last_retry = record.get("last_retry_at", 0)
                if time.time() - last_retry < FATAL_COOLDOWN_SECONDS:
                    return DedupStatus.FAILED_FATAL
                return DedupStatus.NEW

            last_retry = record.get("last_retry_at", 0)
            if time.time() - last_retry < RETRY_COOLDOWN_SECONDS:
                return DedupStatus.FAILED_RETRYABLE

            return DedupStatus.NEW

        return DedupStatus.NEW

    def mark_seen(self, event_key: str, reason: str, event: dict[str, Any] | None = None) -> None:
        """标记为已跳过。"""
        self._append_processed({
            "event_key": event_key,
            "seen_at": time.time(),
            "reply_status": "seen",
            "reason": reason,
            "event": event or {},
        })

    def mark_failed(
        self,
        event_key: str,
        reason: str,
        provider: str | None = None,
        event: dict[str, Any] | None = None,
    ) -> None:
        """标记为失败（原子递增 retry count）。"""
        existing = self._processed_index.get(event_key, {})
        retries = existing.get("retries", 0) + 1
        self._append_processed({
            "event_key": event_key,
            "seen_at": time.time(),
            "reply_status": "failed",
            "reason": reason,
            "provider_used": provider,
            "retries": retries,
            "last_retry_at": time.time(),
            "event": event or {},
        })

    def mark_replied(
        self,
        event_key: str,
        event: dict[str, Any],
        reply_text: str,
        provider: str,
        tool_calls: list[str] | None = None,
    ) -> None:
        """标记为成功回复 — 单次原子双写（history 先，processed 后）。"""
        ts = time.time()
        # 先写 history，再写 processed
        # 这样即使崩溃在两者之间，is_duplicate 不会误判为已回复
        self._append_history({
            "event_key": event_key,
            "replied_at": ts,
            "provider_used": provider,
            "reply_text": reply_text,
            "tool_calls": tool_calls or [],
            "event": event,
        })
        self._append_processed({
            "event_key": event_key,
            "seen_at": ts,
            "replied_at": ts,
            "reply_status": "replied",
            "provider_used": provider,
            "reply_text_hash": hash(reply_text),
            "event": event,
        })

    def get_record(self, event_key: str) -> dict[str, Any] | None:
        """获取事件处理记录。"""
        return self._processed_index.get(event_key)

    # ── 回复历史 ──

    def append_processed(self, record: dict[str, Any]) -> None:
        """追加去重记录（兼容 DedupService.mark_replied 调用）。"""
        self._append_processed(record)

    def append_history(self, record: dict[str, Any]) -> None:
        """追加回复历史。"""
        self._append_history(record)

    # ── 压缩维护 ──

    def compact_processed(self) -> int:
        """去重压缩 processed.jsonl（在排他锁下）。"""
        if not self._processed_path.exists():
            return 0

        with self._lock:
            before_size = self._processed_path.stat().st_size
            records = list(self._processed_index.values())

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", delete=False, dir=self._processed_path.parent, encoding="utf-8"
                ) as f:
                    for record in records:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    tmp_path = f.name
                os.replace(tmp_path, self._processed_path)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return 0

            after_size = self._processed_path.stat().st_size
            return max(0, before_size - after_size)

    def compact_history(self, max_records: int = 10000) -> int:
        """压缩回复历史（保留最近 N 条）。"""
        if not self._history_path.exists():
            return 0

        with self._lock:
            before_size = self._history_path.stat().st_size
            records: list[str] = []

            with self._history_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(line)

            if len(records) <= max_records:
                return 0

            records = records[-max_records:]

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", delete=False, dir=self._history_path.parent, encoding="utf-8"
                ) as f:
                    for line in records:
                        f.write(line + "\n")
                    tmp_path = f.name
                os.replace(tmp_path, self._history_path)
            except Exception:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                return 0

            after_size = self._history_path.stat().st_size
            return max(0, before_size - after_size)

    @property
    def processed_path(self) -> Path:
        """processed.jsonl 路径（兼容 v2 stats.py）。"""
        return self._processed_path

    @property
    def reply_history_path(self) -> Path:
        """reply-history.jsonl 路径（兼容 v2 stats.py）。"""
        return self._history_path

    # ── 内部方法 ──

    def _append_processed(self, record: dict[str, Any]) -> None:
        """追加到 processed.jsonl（带锁）。"""
        self._processed_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            with self._processed_path.open("a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
            key = record.get("event_key")
            if key:
                self._processed_index[key] = record

    def _append_history(self, record: dict[str, Any]) -> None:
        """追加到 reply-history.jsonl（带锁）。"""
        self._history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            with self._history_path.open("a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)


# ── 辅助函数 ──

def _nested_get(data: dict, path: tuple[str, ...]) -> Any:
    """按路径读取嵌套 dict 的值。"""
    current: Any = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
            if current is None and not isinstance(current, dict):
                return None
        else:
            return None
    return current


def _nested_set(data: dict, path: tuple[str, ...], value: Any) -> None:
    """按路径写入嵌套 dict（自动创建中间节点）。"""
    current = data
    for key in path[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[path[-1]] = value


def _is_fatal_error(reason: str) -> bool:
    """检测是否为致命错误（评论已删除等）。"""
    return any(kw in reason for kw in FATAL_ERROR_KEYWORDS)


def utc_timestamp() -> int:
    """UTC 时间戳。"""
    return int(time.time())
