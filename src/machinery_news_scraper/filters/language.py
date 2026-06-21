# src/machinery_news_scraper/filters/language.py
"""English-only language filter using langdetect."""
from __future__ import annotations
from langdetect import DetectorFactory, detect

from ..models import Item

# Make detection deterministic across runs
DetectorFactory.seed = 0

_MIN_CHARS = 20


def detect_language(text: str) -> str:
    """Returns language code like 'en', 'de', 'zh-cn'. Falls back to 'en' on short input."""
    if len(text.strip()) < _MIN_CHARS:
        return "en"
    return detect(text)


def _combined_text(item: Item) -> str:
    parts = [item.title, item.excerpt]
    return " ".join(p for p in parts if p)


def filter_english(items: list[Item]) -> list[Item]:
    """Drop items whose combined title+excerpt is detected as non-English.
    Returns a new list. Items kept get their `language` set to detected code."""
    kept: list[Item] = []
    for item in items:
        lang = detect_language(_combined_text(item))
        if lang == "en":
            kept.append(Item(**{**item.__dict__, "language": "en"}))
    return kept
