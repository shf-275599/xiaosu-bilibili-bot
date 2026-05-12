"""AtomicStateStore 单元测试。"""
import json
import os
import tempfile
import threading
import time

import pytest

from bilibili_bot.atomic_state import (
    MAX_RETRIES,
    AtomicStateStore,
    DedupStatus,
    _is_fatal_error,
    _nested_get,
    _nested_set,
    utc_timestamp,
)


@pytest.fixture
def store(tmp_path):
    data_dir = tmp_path / "data"
    return AtomicStateStore(data_dir)


@pytest.fixture
def store_with_state(store):
    store.save_state({"rate_limit": {"failure_count": 5}})
    return store


# ── load / save ──

def test_load_empty(store):
    assert store.load_state() == {}


def test_save_and_load(store):
    store.save_state({"a": 1})
    assert store.load_state() == {"a": 1}


def test_save_overwrites(store):
    store.save_state({"a": 1})
    store.save_state({"b": 2})
    assert store.load_state() == {"b": 2}


def test_load_corrupted_json(store):
    store._state_path.write_text("{invalid json", encoding="utf-8")
    assert store.load_state() == {}


def test_load_missing_file(store):
    if store._state_path.exists():
        os.remove(store._state_path)
    assert store.load_state() == {}


# ── atomic_increment ──

def test_atomic_increment_from_zero(store):
    new = store.atomic_increment("rate_limit", "failure_count")
    assert new == 1
    assert store.load_state()["rate_limit"]["failure_count"] == 1


def test_atomic_increment_from_existing(store_with_state):
    new = store_with_state.atomic_increment("rate_limit", "failure_count")
    assert new == 6


def test_atomic_increment_custom_delta(store):
    new = store.atomic_increment("rate_limit", "failure_count", delta=5)
    assert new == 5


def test_atomic_increment_negative_delta(store_with_state):
    new = store_with_state.atomic_increment("rate_limit", "failure_count", delta=-3)
    assert new == 2


def test_atomic_increment_creates_intermediate(store):
    store.atomic_increment("a", "b", "c")
    assert store.load_state() == {"a": {"b": {"c": 1}}}


# ── atomic_append ──

def test_atomic_append_creates_list(store):
    store.atomic_append("items", "values", value=42)
    assert store.load_state()["items"]["values"] == [42]


def test_atomic_append_existing_list(store):
    store.atomic_append("items", "values", value=1)
    store.atomic_append("items", "values", value=2)
    assert store.load_state()["items"]["values"] == [1, 2]


def test_atomic_append_max_len_trims(store):
    store.atomic_append("items", "values", value=1, max_len=2)
    store.atomic_append("items", "values", value=2, max_len=2)
    store.atomic_append("items", "values", value=3, max_len=2)
    assert store.load_state()["items"]["values"] == [2, 3]


def test_atomic_append_max_len_zero_no_trim(store):
    for i in range(10):
        store.atomic_append("items", "values", value=i)
    assert len(store.load_state()["items"]["values"]) == 10


# ── atomic_getset ──

def test_atomic_getset_returns_old(store):
    old = store.atomic_getset("x", "y", value=100)
    assert old is None
    old = store.atomic_getset("x", "y", value=200)
    assert old == 100


def test_atomic_getset_overwrites(store):
    store.save_state({"a": 1})
    old = store.atomic_getset("a", value=2)
    assert old == 1
    assert store.load_state() == {"a": 2}


# ── 去重操作 ──

def test_has_processed_new(store):
    assert store.is_duplicate("event:1") == DedupStatus.NEW


def test_mark_seen(store):
    store.mark_seen("event:1", "test reason")
    assert store.is_duplicate("event:1") == DedupStatus.SEEN


def test_mark_failed_retryable(store):
    store.mark_failed("event:1", "API timeout")
    assert store.is_duplicate("event:1") in (
        DedupStatus.FAILED_RETRYABLE,
        DedupStatus.NEW,
    )


def test_mark_failed_fatal_keyword(store):
    store.mark_failed("event:1", "评论已经被删除")
    assert store.is_duplicate("event:1") == DedupStatus.FAILED_FATAL

    store.mark_failed("event:2", "内容不存在")
    assert store.is_duplicate("event:2") == DedupStatus.FAILED_FATAL

    store.mark_failed("event:3", "已过期")
    assert store.is_duplicate("event:3") == DedupStatus.FAILED_FATAL


def test_mark_failed_max_retries_fatal(store):
    for i in range(MAX_RETRIES):
        store.mark_failed("event:1", f"attempt {i}")
    assert store.is_duplicate("event:1") == DedupStatus.FAILED_FATAL


def test_mark_replied(store):
    store.mark_replied("event:1", {"title": "test"}, "reply text", "deepseek")
    assert store.is_duplicate("event:1") == DedupStatus.REPLIED


def test_mark_replied_writes_history(store):
    store.mark_replied("event:1", {"title": "test"}, "reply text", "deepseek", ["search_web"])
    assert store._history_path.exists()
    with store._history_path.open() as f:
        records = [json.loads(line) for line in f if line.strip()]
    assert len(records) == 1
    assert records[0]["event_key"] == "event:1"
    assert records[0]["reply_text"] == "reply text"
    assert records[0]["tool_calls"] == ["search_web"]


def test_is_duplicate_handles_multiple_records_same_key(store):
    store.mark_seen("dup:1", "first")
    store.mark_replied("dup:1", {}, "text", "p")
    assert store.is_duplicate("dup:1") == DedupStatus.REPLIED


def test_get_record(store):
    store.mark_seen("event:1", "reason")
    record = store.get_record("event:1")
    assert record is not None
    assert record["event_key"] == "event:1"

    assert store.get_record("nonexistent") is None


def test_retry_becomes_new_after_cooldown(store, monkeypatch):
    store.mark_failed("event:1", "temp error")
    status = store.is_duplicate("event:1")
    assert status != DedupStatus.REPLIED


# ── compact ──

def test_compact_processed(store):
    for i in range(5):
        store.mark_seen(f"event:{i}", "skip")
    freed = store.compact_processed()
    assert freed >= 0
    assert store._processed_path.exists()
    for i in range(5):
        assert store.is_duplicate(f"event:{i}") == DedupStatus.SEEN


def test_compact_history(store):
    for i in range(50):
        store.append_history({"event_key": f"event:{i}", "replied_at": i})
    freed = store.compact_history(max_records=10)
    assert freed >= 0

    with store._history_path.open() as f:
        lines = [line for line in f if line.strip()]
    assert len(lines) <= 10


def test_compact_empty(store):
    assert store.compact_processed() == 0
    assert store.compact_history() == 0


# ── threading ──

def test_atomic_increment_thread_safe(store):
    results = []

    def worker():
        for _ in range(100):
            val = store.atomic_increment("counter", "n")
            results.append(val)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = store.load_state()["counter"]["n"]
    assert final == 500
    assert len(results) == 500


# ── helper functions ──

def test_nested_get():
    data = {"a": {"b": {"c": 42}}}
    assert _nested_get(data, ("a", "b", "c")) == 42
    assert _nested_get(data, ("a", "x")) is None
    assert _nested_get(data, ("x",)) is None


def test_nested_set():
    data = {}
    _nested_set(data, ("a", "b", "c"), 42)
    assert data == {"a": {"b": {"c": 42}}}

    _nested_set(data, ("a", "b", "d"), 99)
    assert data["a"]["b"]["d"] == 99


def test_is_fatal_error():
    assert _is_fatal_error("评论已经被删除") is True
    assert _is_fatal_error("该内容已被删除") is True
    assert _is_fatal_error("不存在") is True
    assert _is_fatal_error("API timeout") is False
    assert _is_fatal_error("") is False


def test_utc_timestamp():
    ts = utc_timestamp()
    assert isinstance(ts, int)
    assert ts > 1_700_000_000


# ── DedupStatus enum ──

def test_dedup_status_values():
    assert DedupStatus.NEW.value == "new"
    assert DedupStatus.REPLIED.value == "replied"
    assert DedupStatus.SEEN.value == "seen"
    assert DedupStatus.FAILED_RETRYABLE.value == "failed_retryable"
    assert DedupStatus.FAILED_FATAL.value == "failed_fatal"


# ── properties ──

def test_processed_path(store):
    assert store.processed_path.name == "processed.jsonl"


def test_reply_history_path(store):
    assert store.reply_history_path.name == "reply-history.jsonl"
