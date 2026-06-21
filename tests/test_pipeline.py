# tests/test_pipeline.py
from machinery_news_scraper.pusher.wp_client import PushResult, PushStatus
from machinery_news_scraper.pipeline import run_pipeline, PipelineStats


def test_pipeline_success_path(sample_source, sample_item, sample_rss_bytes, tmp_path):
    """End-to-end: scrape → filter EN → push (mocked) → report stats."""
    def fake_scraper(source):
        return [sample_item]

    pushes = []
    def fake_push(item):
        pushes.append(item)
        return PushResult(PushStatus.CREATED, wp_id=len(pushes))

    stats = run_pipeline(
        sources=[sample_source],
        scraper_fn=fake_scraper,
        push_fn=fake_push,
        queue_path=tmp_path / "q.db",
    )
    assert stats.scraped == 1
    assert stats.kept_english == 1
    assert stats.pushed == 1
    assert stats.failed == 0
    assert stats.queued == 0
    assert len(pushes) == 1


def test_pipeline_drops_non_english(sample_source, sample_item, tmp_path):
    from machinery_news_scraper.models import Item
    from datetime import datetime, timezone

    german_item = Item(**{
        **sample_item.__dict__,
        "title": "Neue Verpackungsmaschine",
        "excerpt": "Die neue Maschine ist schneller und effizienter als bisherige Modelle.",
    })

    def fake_scraper(source):
        return [sample_item, german_item]

    def fake_push(item):
        return PushResult(PushStatus.CREATED, wp_id=1)

    stats = run_pipeline(
        sources=[sample_source],
        scraper_fn=fake_scraper,
        push_fn=fake_push,
        queue_path=tmp_path / "q.db",
    )
    assert stats.scraped == 2
    assert stats.kept_english == 1
    assert stats.pushed == 1
    assert stats.dropped_non_english == 1


def test_pipeline_queues_on_failure(sample_source, sample_item, tmp_path):
    def fake_scraper(source):
        return [sample_item]

    def fake_push(item):
        return PushResult(PushStatus.NETWORK_ERROR, error="timeout")

    stats = run_pipeline(
        sources=[sample_source],
        scraper_fn=fake_scraper,
        push_fn=fake_push,
        queue_path=tmp_path / "q.db",
        max_attempts=2,
        base_delay=0.01,
    )
    assert stats.scraped == 1
    assert stats.kept_english == 1
    assert stats.pushed == 0
    assert stats.failed == 1
    assert stats.queued == 1


def test_pipeline_replays_queue_first(sample_source, sample_item, tmp_path):
    """On startup, before scraping fresh items, retry anything in the queue."""
    from machinery_news_scraper.pusher.queue import FailedQueue
    q = FailedQueue(tmp_path / "q.db")
    q.enqueue(sample_item, reason="previous timeout")

    pushes = []
    def fake_push(item):
        pushes.append(item)
        return PushResult(PushStatus.CREATED, wp_id=1)
    def fake_scraper(source):
        return []  # nothing new this cycle

    stats = run_pipeline(
        sources=[sample_source],
        scraper_fn=fake_scraper,
        push_fn=fake_push,
        queue_path=tmp_path / "q.db",
    )
    assert stats.replayed_from_queue == 1
    assert stats.pushed == 1
    assert len(pushes) == 1
    # Queue should be drained after successful replay
    assert q.peek(limit=10) == []
