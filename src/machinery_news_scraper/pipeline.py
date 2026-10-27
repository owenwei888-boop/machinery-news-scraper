# src/machinery_news_scraper/pipeline.py
"""Orchestrates: replay queue → scrape sources → filter EN → AI enrich → push with retry → queue failures."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
from typing import Callable, Optional, TYPE_CHECKING

from .models import Item, Source
from .filters.language import filter_english
from .pusher.wp_client import PushResult, PushStatus
from .pusher.retry import push_with_retry
from .pusher.queue import FailedQueue
from .ai_processor import process_items

if TYPE_CHECKING:
    from .ai_processor import AIProcessor

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
    ai_processed: int = 0
    ai_failed: int = 0

    def __repr__(self) -> str:
        return (
            f"PipelineStats(scraped={self.scraped}, en={self.kept_english}, "
            f"dropped_non_en={self.dropped_non_english}, pushed={self.pushed}, "
            f"dups={self.duplicated}, failed={self.failed}, "
            f"queued={self.queued}, replayed={self.replayed_from_queue}, "
            f"ai_ok={self.ai_processed}, ai_fail={self.ai_failed})"
        )


def run_pipeline(
    sources: list[Source],
    scraper_fn: Callable[[Source], list[Item]],
    push_fn: Callable[[Item], PushResult],
    queue_path: Path | str,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    queue_replay_limit: int = 100,
    ai_processor: "Optional[AIProcessor]" = None,
    existing_urls: Optional[set[str]] = None,
    max_new_items: int = 0,
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

    # 3.5. Pre-dedup: drop items already on WP (saves AI calls + push attempts)
    if existing_urls is not None:
        before = len(english_items)
        english_items = [i for i in english_items if i.url not in existing_urls]
        skipped = before - len(english_items)
        stats.duplicated += skipped
        if skipped:
            log.info("pre-dedup skipped %d already-on-WP items (saved %d AI calls)",
                     skipped, skipped if ai_processor else 0)

    # 3.55. Per-run cap: with a healthy key, AI at rpm 10 costs ~6s/item — an
    # uncapped 1000+ item backlog blows any Actions timeout mid-run and the
    # whole run (including already-enriched items) is lost, every run (seen
    # 2026-10-25..27). Keep only the newest N; the rest reappear in the next
    # scheduled scrape — feeds are the source of truth, nothing is lost.
    if max_new_items and len(english_items) > max_new_items:
        english_items.sort(key=lambda i: i.published_at or datetime.min, reverse=True)
        deferred = len(english_items) - max_new_items
        english_items = english_items[:max_new_items]
        log.info("run cap: processing newest %d this run, %d deferred to next runs",
                 max_new_items, deferred)

    # 3.6. AI enrichment (optional, only on items not already on WP)
    if ai_processor is not None and english_items:
        log.info("AI-enriching %d items...", len(english_items))
        english_items = process_items(english_items, ai_processor)
        stats.ai_processed = sum(1 for i in english_items if i.ai_summary is not None)
        stats.ai_failed = len(english_items) - stats.ai_processed

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
