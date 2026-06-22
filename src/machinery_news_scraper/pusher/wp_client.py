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
    def __init__(
        self,
        base_url: str,
        username: str,
        app_password: str,
        timeout: float = 30,
        auto_publish_threshold: int = 0,
        category_map: dict | None = None,
    ):
        """WPClient pushes Items to /wp-json/wp/v2/posts.

        auto_publish_threshold: posts with ai_importance >= threshold auto-publish,
        others stay draft. 0 = always draft (manual review).

        category_map: maps AI category slug (e.g. 'machines') to WP category ID.
        If provided AND item.ai_category is in the map, the post is assigned
        to that WP category on insert.
        """
        self._base = base_url.rstrip("/")
        self._auth = (username, app_password)
        self._timeout = timeout
        self._threshold = auto_publish_threshold
        self._category_map = category_map or {}

    def push(self, item: Item) -> PushResult:
        payload = self._build_payload(
            item,
            auto_publish_threshold=self._threshold,
            category_map=self._category_map,
        )
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
    def _build_payload(
        item: Item,
        auto_publish_threshold: int = 0,
        category_map: dict | None = None,
    ) -> dict:
        """Build WP REST API payload for a post.

        auto_publish_threshold: if item.ai_importance is set and >= threshold,
        status flips from 'draft' to 'publish'. Pass 0 to always draft (AI
        not in use, or user wants manual review).

        category_map: maps AI category slug to WP category ID. If present and
        item.ai_category is in the map, assigns the post to that category.
        """
        # Decide status
        status = "draft"
        if (
            item.ai_importance is not None
            and auto_publish_threshold > 0
            and item.ai_importance >= auto_publish_threshold
        ):
            status = "publish"

        # Always include source meta
        meta = {
            "_machinery_source_url": item.url,
            "_machinery_source_id": item.source_id,
            "_machinery_source_type": item.source_type,
            "_machinery_external_id": item.external_id,
        }

        # AI-enrichment meta (only when AI ran successfully)
        if item.ai_summary is not None:
            meta["_machinery_ai_summary"] = item.ai_summary
        if item.ai_category is not None:
            meta["_machinery_ai_category"] = item.ai_category
        if item.ai_importance is not None:
            meta["_machinery_ai_importance"] = str(item.ai_importance)
        if item.ai_tags:
            meta["_machinery_ai_tags"] = ",".join(item.ai_tags)
        if item.ai_buyer_angle is not None:
            meta["_machinery_ai_buyer_angle"] = item.ai_buyer_angle

        # If AI summary exists, prepend it to content for SEO + UX
        content = item.content_html or item.excerpt
        if item.ai_summary:
            content = (
                f"<!-- wp:paragraph -->\n"
                f"<p><strong>AI Summary:</strong> {item.ai_summary}</p>\n"
                f"<!-- /wp:paragraph -->\n\n"
                f"{content}"
            )

        payload = {
            "title": item.title,
            "excerpt": item.ai_summary or item.excerpt,  # prefer AI summary in excerpt
            "content": content,
            "status": status,
            "slug": "",  # let WP auto-generate
            "date_gmt": item.published_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "meta": meta,
        }

        # Assign WP category based on AI classification
        category_map = category_map or {}
        if item.ai_category and item.ai_category in category_map:
            payload["categories"] = [category_map[item.ai_category]]

        return payload

    def fetch_existing_source_urls(self) -> set[str]:
        """Page through WP REST API and return the set of all _machinery_source_url
        values currently on the site. Used by the pipeline to skip AI processing
        on items already imported (saves Gemini quota).

        Queries all posts with status publish/draft/pending. Paginates 100/page.
        """
        urls: set[str] = set()
        page = 1
        while True:
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    resp = client.get(
                        f"{self._base}/wp-json/wp/v2/posts",
                        params={
                            "per_page": 100,
                            "page": page,
                            "status": "publish,draft,pending",
                            "context": "edit",
                        },
                        auth=self._auth,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as e:
                log.warning("network error fetching existing URLs page %d: %s", page, e)
                break

            if resp.status_code != 200:
                log.warning("fetch_existing_source_urls page %d: HTTP %s", page, resp.status_code)
                break

            posts = resp.json()
            for p in posts:
                meta = p.get("meta", {})
                if isinstance(meta, dict):
                    url = meta.get("_machinery_source_url", "")
                    if url:
                        urls.add(url)

            if len(posts) < 100:
                break
            page += 1
            # Safety cap
            if page > 50:
                break
        log.info("fetched %d existing source URLs from WP (in %d pages)", len(urls), page)
        return urls
