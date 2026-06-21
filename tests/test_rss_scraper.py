# tests/test_rss_scraper.py
from datetime import datetime, timezone
from pathlib import Path
import pytest
from machinery_news_scraper.models import Source
from machinery_news_scraper.scrapers.rss_scraper import scrape_rss, RSSParseError

FIXTURES = Path(__file__).parent / "fixtures"


def _make_source(url: str = "https://example.com/feed.xml") -> Source:
    return Source(id="test", name="Test", url=url, source_type="rss")


def test_scrape_rss_parses_items(monkeypatch):
    xml = (FIXTURES / "packaging_europe_sample.xml").read_bytes()
    monkeypatch.setattr(
        "machinery_news_scraper.scrapers.rss_scraper._fetch_bytes",
        lambda url: xml,
    )
    items = scrape_rss(_make_source())
    assert len(items) == 2
    assert items[0].title == "Bosch unveils servo-driven VFFS machine at Interpack"
    assert items[0].source_id == "test"
    assert items[0].source_type == "rss"
    assert items[0].url == "https://www.packagingeurope.com/news/bosch-vffs-interpack"
    assert items[0].external_id == items[0].url  # guid falls back to URL
    assert items[0].published_at == datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)
    assert items[0].fetched_at > datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
    assert "servo-driven" in items[0].excerpt
    assert items[0].author == "Jane Doe"  # parsed from "email (Name)"


def test_scrape_rss_handles_missing_fields(monkeypatch):
    xml = (FIXTURES / "packaging_europe_sample.xml").read_bytes()
    monkeypatch.setattr(
        "machinery_news_scraper.scrapers.rss_scraper._fetch_bytes",
        lambda url: xml,
    )
    items = scrape_rss(_make_source())
    # Second item has no author
    assert items[1].author is None


def test_scrape_rss_raises_on_unparseable(monkeypatch):
    monkeypatch.setattr(
        "machinery_news_scraper.scrapers.rss_scraper._fetch_bytes",
        lambda url: b"not xml at all <<<",
    )
    with pytest.raises(RSSParseError):
        scrape_rss(_make_source())


def test_scrape_rss_skips_items_without_link(monkeypatch):
    malformed_xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>No link here</title></item>
  <item>
    <title>Has link</title>
    <link>https://example.com/ok</link>
    <pubDate>Mon, 20 Jun 2026 09:00:00 +0000</pubDate>
    <guid>https://example.com/ok</guid>
  </item>
</channel></rss>"""
    monkeypatch.setattr(
        "machinery_news_scraper.scrapers.rss_scraper._fetch_bytes",
        lambda url: malformed_xml,
    )
    items = scrape_rss(_make_source())
    assert len(items) == 1
    assert items[0].title == "Has link"
