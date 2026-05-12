from __future__ import annotations

import time

import structlog

from bilibili_bot.atomic_state import AtomicStateStore

logger = structlog.get_logger()

_WINDOW_SECONDS = 24 * 60 * 60
_SKIP_THRESHOLD = 3


class AutoSkipTracker:
    def __init__(self, store: AtomicStateStore) -> None:
        self._store = store
        state = store.load_state()
        auto_skip_data = state.get("auto_skip", {})
        self._records: list[dict] = auto_skip_data.get("records", [])
        self._prune()

    def record_fatal(self, event_key: str, author_mid: str, source_type: str) -> None:
        self._records.append({
            "author": author_mid,
            "source": source_type,
            "ts": time.time(),
        })
        self._prune()
        self._save()
        logger.info(
            "auto_skip_recorded",
            event_key=event_key,
            author_mid=author_mid,
            source_type=source_type,
            total_records=len(self._records),
        )

    def should_skip(self, author_mid: str, source_type: str) -> bool:
        self._prune()
        cutoff = time.time() - _WINDOW_SECONDS
        count = sum(
            1 for r in self._records
            if r["author"] == author_mid
            and r["source"] == source_type
            and r["ts"] >= cutoff
        )
        return count >= _SKIP_THRESHOLD

    def _prune(self) -> None:
        cutoff = time.time() - _WINDOW_SECONDS
        self._records = [r for r in self._records if r["ts"] >= cutoff]

    def _save(self) -> None:
        state = self._store.load_state()
        state["auto_skip"] = {"records": self._records}
        self._store.save_state(state)
