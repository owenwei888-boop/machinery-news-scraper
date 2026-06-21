# src/machinery_news_scraper/models.py
"""Core dataclasses shared across the scraper."""
from __future__ import annotations
from dataclasses import dataclass
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
    """A normalized news item — the canonical shape that flows through the pipeline."""
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
