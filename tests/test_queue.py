# tests/test_queue.py
from datetime import datetime, timezone
import pytest
from machinery_news_scraper.models import Item
from machinery_news_scraper.pusher.queue import FailedQueue


def _make_item(suffix: str = "1") -> Item:
    return Item(
        source_id="x", source_type="rss", external_id=f"ext-{suffix}",
        title=f"title-{suffix}", url=f"https://example.com/{suffix}",
        excerpt="e", content_html=None, author=None,
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        language="en",
    )


def test_enqueue_and_dequeue(tmp_path):
    q = FailedQueue(tmp_path / "q.db")
    item = _make_item()
    row_id = q.enqueue(item, reason="503 service unavailable")
    pending = q.peek(limit=10)
    assert len(pending) == 1
    row = pending[0]
    assert row.id == row_id
    assert row.item.url == item.url
    assert row.reason == "503 service unavailable"
    assert row.attempts == 1


def test_remove_after_successful_push(tmp_path):
    q = FailedQueue(tmp_path / "q.db")
    item = _make_item()
    qid = q.enqueue(item, reason="x")
    q.mark_pushed(qid)
    assert q.peek(limit=10) == []


def test_attempt_count_increments_on_reenqueue(tmp_path):
    q = FailedQueue(tmp_path / "q.db")
    item = _make_item()
    qid1 = q.enqueue(item, reason="timeout")
    q.mark_pushed(qid1)
    qid2 = q.enqueue(item, reason="503")  # second enqueue
    pending = q.peek(limit=10)
    assert len(pending) == 1
    assert pending[0].id == qid2
    assert pending[0].attempts == 2  # second attempt


def test_dedup_on_external_id(tmp_path):
    """Same external_id enqueued twice (without dequeue) shouldn't duplicate."""
    q = FailedQueue(tmp_path / "q.db")
    item = _make_item()
    q.enqueue(item, reason="x")
    q.enqueue(item, reason="y")
    assert len(q.peek(limit=10)) == 1


def test_peek_respects_limit(tmp_path):
    q = FailedQueue(tmp_path / "q.db")
    for i in range(5):
        q.enqueue(_make_item(str(i)), reason="x")
    pending = q.peek(limit=3)
    assert len(pending) == 3
