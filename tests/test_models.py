# tests/test_models.py
from datetime import datetime, timezone
from machinery_news_scraper.models import Source, Item


def test_source_creation():
    s = Source(
        id="packaging-europe",
        name="Packaging Europe",
        url="https://www.packagingeurope.com/feed/",
        source_type="rss",
    )
    assert s.id == "packaging-europe"
    assert s.source_type == "rss"


def test_item_creation_with_all_fields():
    i = Item(
        source_id="packaging-europe",
        source_type="rss",
        external_id="https://www.packagingeurope.com/news/article-1",
        title="Bosch unveils new VFFS machine",
        url="https://www.packagingeurope.com/news/article-1",
        excerpt="Bosch has launched a servo-driven VFFS machine.",
        content_html=None,
        author="Jane Doe",
        published_at=datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc),
        language="en",
    )
    assert i.title == "Bosch unveils new VFFS machine"
    assert i.published_at.year == 2026


def test_item_validates_required_fields():
    import pytest
    with pytest.raises(TypeError):
        Item(source_id="x")  # type: ignore[call-arg]
