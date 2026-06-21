# src/machinery_news_scraper/pusher/retry.py
"""Retry wrapper for wp_client.push with exponential backoff on transient errors."""
from __future__ import annotations
import logging
import time
from typing import Callable

from ..models import Item
from .wp_client import PushResult, PushStatus

log = logging.getLogger(__name__)

_RETRYABLE = {PushStatus.SERVER_ERROR, PushStatus.NETWORK_ERROR}


def push_with_retry(
    item: Item,
    push_fn: Callable[[Item], PushResult],
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> PushResult:
    """Try push_fn up to max_attempts times. Only retries on SERVER_ERROR / NETWORK_ERROR.
    Returns the final PushResult (could be success, duplicate, or last failure)."""
    result: PushResult | None = None
    for attempt in range(1, max_attempts + 1):
        result = push_fn(item)
        if result.status not in _RETRYABLE:
            return result
        if attempt < max_attempts:
            delay = base_delay * (4 ** (attempt - 1))  # 1s, 4s, 16s
            log.info(
                "push attempt %d/%d failed (%s), retrying in %.1fs",
                attempt, max_attempts, result.status, delay,
            )
            time.sleep(delay)
    log.warning(
        "push exhausted %d attempts for %s (last status: %s)",
        max_attempts, item.url, result.status if result else "none",
    )
    return result  # type: ignore[return-value]
