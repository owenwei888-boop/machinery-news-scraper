# src/machinery_news_scraper/models.py
"""Core dataclasses shared across the scraper."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

SourceType = Literal["rss", "linkedin", "tradeshow"]


@dataclass(frozen=True)
class Source:
    """A configured source of news items (RSS feed, LinkedIn page, etc.)."""
    id: str
    name: str
    url: str
    source_type: SourceType


@dataclass(frozen=True)
class Item:
    """A normalized news item — the canonical shape that flows through the pipeline.

    AI-enrichment fields (ai_*) are populated by ai_processor.py when an AI
    provider is configured. They default to None / empty when AI is not run
    (e.g., for tests, or when AI fails on a given item).
    """
    source_id: str
    source_type: SourceType
    external_id: str
    title: str
    url: str
    excerpt: str
    content_html: Optional[str]
    author: Optional[str]
    published_at: datetime
    fetched_at: datetime
    language: str

    # AI-enrichment (optional, populated by ai_processor)
    ai_summary: Optional[str] = None
    ai_category: Optional[str] = None
    ai_importance: Optional[int] = None
    ai_tags: tuple = ()
    ai_buyer_angle: Optional[str] = None
