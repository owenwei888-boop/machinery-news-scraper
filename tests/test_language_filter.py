# tests/test_language_filter.py
from datetime import datetime, timezone
from machinery_news_scraper.models import Item
from machinery_news_scraper.filters.language import filter_english, detect_language


def _make_item(title: str, excerpt: str) -> Item:
    return Item(
        source_id="x", source_type="rss", external_id="x", title=title,
        url="https://example.com/x", excerpt=excerpt, content_html=None,
        author=None, published_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, tzinfo=timezone.utc), language="en",
    )


def test_detect_english_returns_en():
    assert detect_language("Bosch launches a new servo-driven packaging machine for food manufacturers") == "en"


def test_detect_german_returns_de():
    assert detect_language("Die neue Verpackungsmaschine ist schneller und effizienter") == "de"


def test_detect_chinese_returns_zh():
    assert detect_language("博世包装推出新款伺服驱动的立式包装机，适用于食品行业") == "zh-cn"


def test_filter_english_keeps_english():
    items = [
        _make_item("Bosch launches new packaging machine", "Bosch has launched a new servo-driven packaging machine."),
        _make_item("Neue Maschine", "Die neue Maschine ist schneller."),
    ]
    kept = filter_english(items)
    assert len(kept) == 1
    assert kept[0].title == "Bosch launches new packaging machine"
    assert kept[0].language == "en"


def test_filter_english_drops_short_unsure_items():
    # Very short title-only items are unreliable for langdetect; we keep them
    # (defensive — better to publish a borderline item than drop real news).
    items = [_make_item("IPO", "Interpack 2026 dates announced.")]
    kept = filter_english(items)
    assert len(kept) == 1


def test_filter_english_uses_combined_text():
    # German title but English body — should be detected from longer body text.
    items = [_make_item(
        "Maschinenbau Update",
        "The new packaging line from Bosch delivers significantly higher throughput "
        "compared to the previous generation, with reduced energy consumption.",
    )]
    kept = filter_english(items)
    assert len(kept) == 1  # kept because body is English
    assert kept[0].language == "en"
