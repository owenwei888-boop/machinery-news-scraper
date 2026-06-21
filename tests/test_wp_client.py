# tests/test_wp_client.py
from datetime import datetime, timezone
import pytest
import httpx
import respx
from machinery_news_scraper.models import Item
from machinery_news_scraper.pusher.wp_client import WPClient, PushResult, PushStatus


def _make_item() -> Item:
    return Item(
        source_id="test", source_type="rss",
        external_id="https://example.com/a",
        title="Bosch VFFS launch", url="https://example.com/a",
        excerpt="Bosch launched a new machine.",
        content_html=None, author="Jane",
        published_at=datetime(2026, 6, 20, 9, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 6, 20, 10, tzinfo=timezone.utc),
        language="en",
    )


def test_push_returns_success_on_201():
    client = WPClient(base_url="https://wp.example.com", username="u", app_password="p")
    with respx.mock(base_url="https://wp.example.com") as r:
        r.post("/wp-json/wp/v2/posts").respond(201, json={"id": 123, "link": "https://wp.example.com/?p=123"})
        result = client.push(_make_item())
    assert result.status == PushStatus.CREATED
    assert result.wp_id == 123


def test_push_returns_duplicate_on_409():
    client = WPClient("https://wp.example.com", "u", "p")
    with respx.mock(base_url="https://wp.example.com") as r:
        r.post("/wp-json/wp/v2/posts").respond(409, json={"code": "duplicate"})
        result = client.push(_make_item())
    assert result.status == PushStatus.DUPLICATE


def test_push_returns_server_error_on_500():
    client = WPClient("https://wp.example.com", "u", "p")
    with respx.mock(base_url="https://wp.example.com") as r:
        r.post("/wp-json/wp/v2/posts").respond(500)
        result = client.push(_make_item())
    assert result.status == PushStatus.SERVER_ERROR


def test_push_returns_network_error_on_timeout():
    client = WPClient("https://wp.example.com", "u", "p")
    with respx.mock(base_url="https://wp.example.com") as r:
        r.post("/wp-json/wp/v2/posts").mock(side_effect=httpx.TimeoutException("timeout"))
        result = client.push(_make_item())
    assert result.status == PushStatus.NETWORK_ERROR


def test_payload_includes_canonical_meta():
    """Pusher should set the post's canonical URL meta (wp-rss-curator reads this
    for attribution). The Item's URL goes into the post content + a meta field."""
    captured = {}

    def _capture(request):
        captured["body"] = request.read()
        return httpx.Response(201, json={"id": 1})

    client = WPClient("https://wp.example.com", "u", "p")
    with respx.mock(base_url="https://wp.example.com") as r:
        r.post("/wp-json/wp/v2/posts").mock(side_effect=_capture)
        client.push(_make_item())

    import json
    body = json.loads(captured["body"])
    assert body["title"] == "Bosch VFFS launch"
    assert "Bosch launched" in body["excerpt"]
    assert body["status"] == "draft"  # MVP: import as draft, AI layer publishes later
    assert body["meta"]["_machinery_source_url"] == "https://example.com/a"
    assert body["meta"]["_machinery_source_id"] == "test"
