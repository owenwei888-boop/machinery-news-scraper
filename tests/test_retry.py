# tests/test_retry.py
from datetime import datetime, timezone
import pytest
from machinery_news_scraper.models import Item
from machinery_news_scraper.pusher.retry import push_with_retry
from machinery_news_scraper.pusher.wp_client import PushResult, PushStatus


def _make_item() -> Item:
    return Item(
        source_id="x", source_type="rss", external_id="x",
        title="t", url="u", excerpt="e", content_html=None, author=None,
        published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        language="en",
    )


def test_no_retry_on_success():
    calls = []
    def fake_push(item):
        calls.append(item)
        return PushResult(PushStatus.CREATED, wp_id=1)
    result = push_with_retry(_make_item(), fake_push, max_attempts=3, base_delay=0.01)
    assert result.status == PushStatus.CREATED
    assert len(calls) == 1


def test_no_retry_on_duplicate():
    """Duplicates are not retried — they're success cases."""
    calls = []
    def fake_push(item):
        calls.append(item)
        return PushResult(PushStatus.DUPLICATE)
    result = push_with_retry(_make_item(), fake_push, max_attempts=3, base_delay=0.01)
    assert result.status == PushStatus.DUPLICATE
    assert len(calls) == 1


def test_retries_on_server_error_then_succeeds():
    calls = []
    def fake_push(item):
        calls.append(item)
        if len(calls) < 3:
            return PushResult(PushStatus.SERVER_ERROR, error="503")
        return PushResult(PushStatus.CREATED, wp_id=1)
    result = push_with_retry(_make_item(), fake_push, max_attempts=3, base_delay=0.01)
    assert result.status == PushStatus.CREATED
    assert len(calls) == 3


def test_stops_after_max_attempts():
    calls = []
    def fake_push(item):
        calls.append(item)
        return PushResult(PushStatus.NETWORK_ERROR, error="timeout")
    result = push_with_retry(_make_item(), fake_push, max_attempts=3, base_delay=0.01)
    assert result.status == PushStatus.NETWORK_ERROR
    assert len(calls) == 3


def test_no_retry_on_bad_request():
    """4xx that aren't duplicate are permanent failures — don't waste attempts."""
    calls = []
    def fake_push(item):
        calls.append(item)
        return PushResult(PushStatus.BAD_REQUEST, error="400 invalid title")
    result = push_with_retry(_make_item(), fake_push, max_attempts=3, base_delay=0.01)
    assert result.status == PushStatus.BAD_REQUEST
    assert len(calls) == 1
