# tests/test_ai_fallback.py
"""FallbackAIProcessor: free-provider-first with automatic failover.

Covers the three behaviors the scraper pipeline depends on:
1. primary healthy  → every item enriched by primary, fallback never touched
2. primary dies mid-run → the item that tripped exhaustion is re-run on the
   fallback (not shipped raw), and every later item goes to the fallback
3. usage / _quota_exhausted aggregate across both providers

Uses stub processors (no network, no vendor SDKs) — FallbackAIProcessor only
needs the AIProcessor surface: process() / usage / _quota_exhausted /
_provider.
"""
from dataclasses import replace

import pytest

from machinery_news_scraper.ai_processor import FallbackAIProcessor


class _StubProc:
    """Minimal AIProcessor stand-in. `die_on_call` N = the Nth process()
    call kills the provider (sets _quota_exhausted, returns item raw)."""

    def __init__(self, provider: str, die_on_call: int | None = None):
        self._provider = provider
        self.usage = {"in": 0, "out": 0}
        self._quota_exhausted = False
        self._die_on_call = die_on_call
        self.calls = 0

    def process(self, item):
        self.calls += 1
        if self._die_on_call is not None and self.calls >= self._die_on_call:
            self._quota_exhausted = True
            return item  # raw, like AIProcessor on exhaustion
        self.usage["in"] += 10
        self.usage["out"] += 5
        return replace(item, ai_summary=f"{self._provider} summary")


def _items(n):
    from machinery_news_scraper.models import Item
    from datetime import datetime, timezone
    return [
        Item(
            source_id="test", source_type="rss",
            external_id=f"https://example.com/{i}", title=f"item {i}",
            url=f"https://example.com/{i}", excerpt="x", content_html=None,
            author=None,
            published_at=datetime(2026, 11, 26, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 11, 26, tzinfo=timezone.utc),
            language="en",
        )
        for i in range(n)
    ]


def test_primary_healthy_fallback_never_used(sample_item):
    p, f = _StubProc("gemini"), _StubProc("deepseek")
    fb = FallbackAIProcessor(p, f)
    out = fb.process(sample_item)
    assert out.ai_summary == "gemini summary"
    assert f.calls == 0
    assert fb._quota_exhausted is False


def test_midrun_death_reprocesses_tripwire_item(sample_item):
    # Primary dies on its 2nd call: item2 comes back raw → must be re-run
    # on the fallback, not shipped unenriched.
    p = _StubProc("gemini", die_on_call=2)
    f = _StubProc("deepseek")
    fb = FallbackAIProcessor(p, f)
    items = _items(3)
    outs = [fb.process(i) for i in items]
    assert [o.ai_summary for o in outs] == [
        "gemini summary", "deepseek summary", "deepseek summary",
    ]
    assert p.calls == 2
    assert f.calls == 2  # tripwire item + item3


def test_exhausted_before_item_goes_straight_to_fallback(sample_item):
    p = _StubProc("gemini", die_on_call=1)
    f = _StubProc("deepseek")
    fb = FallbackAIProcessor(p, f)
    out = fb.process(sample_item)
    assert out.ai_summary == "deepseek summary"
    assert f.calls == 1


def test_usage_and_quota_aggregate(sample_item):
    p = _StubProc("gemini")   # 1 enriched call: 10 in / 5 out
    f = _StubProc("deepseek")  # unused
    fb = FallbackAIProcessor(p, f)
    fb.process(sample_item)
    assert fb.usage == {"in": 10, "out": 5}
    assert fb._quota_exhausted is False
    # Both dead → wrapper reports exhausted (pipeline may rely on this).
    p._quota_exhausted = True
    f._quota_exhausted = True
    assert fb._quota_exhausted is True
