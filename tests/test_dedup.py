import pytest
from bilibili_bot.atomic_state import AtomicStateStore
from bilibili_bot.pipeline.dedup import DedupService, DedupStatus
from bilibili_bot.events import CommentEvent


@pytest.fixture
def dedup(tmp_path):
    store = AtomicStateStore(tmp_path)
    return DedupService(store)


def test_new_event(dedup):
    event = CommentEvent(
        source_type="msgfeed",
        event_key="video:123:456",
        created_at=1000,
    )

    assert dedup.is_duplicate("video:123:456") == DedupStatus.NEW


def test_mark_replied(dedup):
    event = CommentEvent(
        source_type="msgfeed",
        event_key="video:123:456",
        created_at=1000,
    )

    dedup.mark_replied(event, "test reply", "test_provider")
    assert dedup.is_duplicate("video:123:456") == DedupStatus.REPLIED


def test_mark_seen(dedup):
    event = CommentEvent(
        source_type="msgfeed",
        event_key="video:123:456",
        created_at=1000,
    )

    dedup.mark_seen(event, "test reason")
    assert dedup.is_duplicate("video:123:456") == DedupStatus.SEEN
