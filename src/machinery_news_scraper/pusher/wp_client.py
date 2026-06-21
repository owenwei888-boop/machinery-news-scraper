# src/machinery_news_scraper/pusher/wp_client.py
"""WordPress REST API client for pushing scraped items."""
from __future__ import annotations
from enum import Enum
import logging

import httpx

from ..models import Item

log = logging.getLogger(__name__)


class PushStatus(str, Enum):
    CREATED = "created"           # 201 — new post
    DUPLICATE = "duplicate"       # 409 or 400 with code=duplicate (wp-rss-curator dedup)
    SERVER_ERROR = "server_error"  # 5xx — retry
    NETWORK_ERROR = "network"     # timeout / connection — retry
    BAD_REQUEST = "bad_request"   # 4xx other — don't retry, log loud


class PushResult:
    __slots__ = ("status", "wp_id", "error")

    def __init__(self, status: PushStatus, wp_id: int | None = None, error: str | None = None):
        self.status = status
        self.wp_id = wp_id
        self.error = error

    def __repr__(self) -> str:
        return f"PushResult(status={self.status}, wp_id={self.wp_id}, error={self.error!r})"


class WPClient:
    def __init__(self, base_url: str, username: str, app_password: str, timeout: float = 30):
        self._base = base_url.rstrip("/")
        self._auth = (username, app_password)
        self._timeout = timeout

    def push(self, item: Item) -> PushResult:
        payload = self._build_payload(item)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._base}/wp-json/wp/v2/posts",
                    json=payload,
                    auth=self._auth,
                )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.warning("network error pushing item %s: %s", item.url, e)
            return PushResult(PushStatus.NETWORK_ERROR, error=str(e))

        if resp.status_code == 201:
            data = resp.json()
            return PushResult(PushStatus.CREATED, wp_id=data.get("id"))
        if resp.status_code == 409:
            return PushResult(PushStatus.DUPLICATE)
        if 500 <= resp.status_code < 600:
            log.warning("server error %s pushing item %s: %s", resp.status_code, item.url, resp.text[:200])
            return PushResult(PushStatus.SERVER_ERROR, error=f"HTTP {resp.status_code}")
        # 4xx (excluding 409): try to detect wp-rss-curator duplicate code
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("code") == "duplicate":
                return PushResult(PushStatus.DUPLICATE)
        except Exception:
            pass
        log.error("bad request %s pushing item %s: %s", resp.status_code, item.url, resp.text[:200])
        return PushResult(PushStatus.BAD_REQUEST, error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    @staticmethod
    def _build_payload(item: Item) -> dict:
        return {
            "title": item.title,
            "excerpt": item.excerpt,
            "content": item.content_html or item.excerpt,
            "status": "draft",  # AI layer will publish after processing
            "slug": "",  # let WP auto-generate
            "date_gmt": item.published_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "meta": {
                "_machinery_source_url": item.url,
                "_machinery_source_id": item.source_id,
                "_machinery_source_type": item.source_type,
                "_machinery_external_id": item.external_id,
            },
        }
