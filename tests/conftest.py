# tests/conftest.py
from datetime import datetime, timezone
from pathlib import Path
import pytest
from machinery_news_scraper.models import Item, Source

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_source() -> Source:
    return Source(id="test", name="Test", url="https://example.com/feed", source_type="rss")


@pytest.fixture
def sample_item() -> Item:
    return Item(
        source_id="test", source_type="rss",
        external_id="https://example.com/a",
        title="Bosch unveils VFFS machine at Interpack",
        url="https://example.com/a",
        excerpt="Bosch has launched a new servo-driven VFFS machine for packaging.",
        content_html=None, author="Jane",
        published_at=datetime(2026, 6, 20, 9, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, 10, tzinfo=timezone.utc),
        language="en",
    )


@pytest.fixture
def sample_rss_bytes() -> bytes:
    return (FIXTURES / "packaging_europe_sample.xml").read_bytes()
