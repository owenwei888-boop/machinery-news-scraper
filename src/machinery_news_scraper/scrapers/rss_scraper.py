# src/machinery_news_scraper/scrapers/rss_scraper.py
"""RSS/Atom scraper using feedparser."""
from __future__ import annotations
from datetime import datetime, timezone
import re
from typing import Callable

import feedparser
import httpx

from ..models import Item, Source

_AUTHOR_EMAIL_RE = re.compile(r"^.*?\(([^)]+)\)\s*$")

_USER_AGENT = (
    "Mozilla/5.0 (compatible; MachineryNewsScraper/0.1; "
    "+https://the-packing-machine.com/about)"
)


class RSSParseError(Exception):
    """Raised when RSS feed cannot be parsed."""


def _fetch_bytes(url: str) -> bytes:
    """HTTP fetch — isolated so tests can monkeypatch.
    Sends a descriptive User-Agent so industry-news sites don't 403 us as a bot."""
    with httpx.Client(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _parse_author(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _AUTHOR_EMAIL_RE.match(raw.strip())
    return m.group(1) if m else raw.strip()


def _strip_html(text: str) -> str:
    """Quick-and-dirty HTML→plain text. AI processing handles the rest downstream."""
    clean = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", clean).strip()


def _parse_date(entry) -> datetime:
    # feedparser returns struct_time in entry.published_parsed
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return datetime.now(timezone.utc)
    return datetime(*st[:6], tzinfo=timezone.utc)


def scrape_rss(
    source: Source,
    fetcher: Callable[[str], bytes] | None = None,
) -> list[Item]:
    # Resolve at call time so tests can monkeypatch the module-level _fetch_bytes.
    raw = (fetcher or _fetch_bytes)(source.url)
    parsed = feedparser.parse(raw)
    if parsed.bozo and not parsed.entries:
        raise RSSParseError(f"Failed to parse feed from {source.url}: {parsed.bozo_exception}")

    fetched_at = datetime.now(timezone.utc)
    items: list[Item] = []
    for entry in parsed.entries:
        url = entry.get("link")
        if not url:
            continue
        excerpt_raw = entry.get("summary") or entry.get("description") or ""
        items.append(Item(
            source_id=source.id,
            source_type="rss",
            external_id=entry.get("id") or url,
            title=entry.get("title", "").strip() or "(untitled)",
            url=url,
            excerpt=_strip_html(excerpt_raw)[:500],
            content_html=entry.get("content", [{}])[0].get("value") if entry.get("content") else None,
            author=_parse_author(entry.get("author")),
            published_at=_parse_date(entry),
            fetched_at=fetched_at,
            language="en",  # default; language filter will override or drop
        ))
    return items
