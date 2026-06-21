# src/machinery_news_scraper/pipeline.py
"""Orchestrates: replay queue → scrape sources → filter EN → push with retry → queue failures."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import logging
from typing import Callable

from .models import Item, Source
from .filters.language import filter_english
from .pusher.wp_client import PushResult, PushStatus
from .pusher.retry import push_with_retry
from .pusher.queue import FailedQueue

log = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    scraped: int = 0
    kept_english: int = 0
    dropped_non_english: int = 0
    pushed: int = 0
    duplicated: int = 0
    failed: int = 0
    queued: int = 0
    replayed_from_queue: int = 0

    def __repr__(self) -> str:
        return (
            f"PipelineStats(scraped={self.scraped}, en={self.kept_english}, "
            f"dropped_non_en={self.dropped_non_english}, pushed={self.pushed}, "
            f"dups={self.duplicated}, failed={self.failed}, "
            f"queued={self.queued}, replayed={self.replayed_from_queue})"
        )


def run_pipeline(
    sources: list[Source],
    scraper_fn: Callable[[Source], list[Item]],
    push_fn: Callable[[Item], PushResult],
    queue_path: Path | str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    queue_replay_limit: int = 100,
) -> PipelineStats:
    stats = PipelineStats()
    queue = FailedQueue(queue_path)

    # 1. Replay queue first (give stuck items another shot before adding new ones)
    pending = queue.peek(limit=queue_replay_limit)
    for queued in pending:
        result = push_with_retry(queued.item, push_fn,
                                  max_attempts=max_attempts, base_delay=base_delay)
        if result.status == PushStatus.CREATED:
            stats.replayed_from_queue += 1
            stats.pushed += 1
            queue.mark_pushed(queued.id)
        # If still failing, it stays in queue (last_queued_at unchanged)

    # 2. Scrape fresh
    all_items: list[Item] = []
    for source in sources:
        try:
            all_items.extend(scraper_fn(source))
        except Exception as e:
            log.error("scraper error for %s: %s", source.id, e)
    stats.scraped = len(all_items)

    # 3. Filter EN
    english_items = filter_english(all_items)
    stats.kept_english = len(english_items)
    stats.dropped_non_english = stats.scraped - stats.kept_english

    # 4. Push each
    for item in english_items:
        result = push_with_retry(item, push_fn, max_attempts=max_attempts, base_delay=base_delay)
        if result.status == PushStatus.CREATED:
            stats.pushed += 1
        elif result.status == PushStatus.DUPLICATE:
            stats.duplicated += 1
        else:
            stats.failed += 1
            queue.enqueue(item, reason=result.error or result.status.value)
            stats.queued += 1

    log.info("pipeline done: %s", stats)
    return stats
