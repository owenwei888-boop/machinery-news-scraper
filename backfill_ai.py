"""One-off script: backfill AI processing on existing draft posts.

Fetches all drafts from WP that lack _machinery_ai_summary, runs Gemini on each,
updates the post via REST API with AI meta + optionally promotes importance>=N to publish.

Usage:
    python backfill_ai.py [--auto-publish-threshold 3] [--limit 50] [--dry-run]

Rate-limited to 15 RPM by default (Gemini free tier). 208 posts ~= 14 minutes.
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")


def fetch_drafts_needing_ai(base: str, auth: tuple[str, str]) -> list[dict]:
    """Page through WP REST API, return posts that have source_url but no ai_summary."""
    out: list[dict] = []
    page = 1
    while True:
        r = httpx.get(
            f"{base}/wp-json/wp/v2/posts",
            params={"per_page": 100, "page": page, "status": "draft,pending,publish",
                    "context": "edit"},
            auth=auth, timeout=30,
        )
        r.raise_for_status()
        posts = r.json()
        for p in posts:
            meta = p.get("meta", {}) if isinstance(p.get("meta"), dict) else {}
            if meta.get("_machinery_source_url") and not meta.get("_machinery_ai_summary"):
                out.append({
                    "id": p["id"],
                    "title": p["title"]["rendered"] if isinstance(p.get("title"), dict) else "",
                    "excerpt": p["excerpt"]["rendered"] if isinstance(p.get("excerpt"), dict) else "",
                    "content": p["content"]["rendered"] if isinstance(p.get("content"), dict) else "",
                    "source_url": meta.get("_machinery_source_url", ""),
                    "source_id": meta.get("_machinery_source_id", ""),
                    "status": p.get("status"),
                })
        if len(posts) < 100:
            break
        page += 1
        if page > 20:  # safety cap (2000 posts)
            break
    return out


def update_post_ai(
    base: str, auth: tuple[str, str], post_id: int, ai_data: dict,
    auto_publish_threshold: int,
) -> tuple[str, int]:
    """Update a post with AI meta. Returns (new_status, importance)."""
    importance = ai_data.get("importance") or 0
    new_status = "draft"
    if auto_publish_threshold > 0 and importance >= auto_publish_threshold:
        new_status = "publish"

    payload = {
        "meta": {
            "_machinery_ai_summary": ai_data.get("summary", ""),
            "_machinery_ai_category": ai_data.get("category", ""),
            "_machinery_ai_importance": str(importance),
            "_machinery_ai_tags": ",".join(ai_data.get("tags", []) or []),
            "_machinery_ai_buyer_angle": ai_data.get("buyer_angle", ""),
        },
        "status": new_status,
        # Rebuild excerpt to use AI summary if available
        "excerpt": ai_data.get("summary", "") or None,
    }
    # Remove None values (WP rejects them for some fields)
    payload = {k: v for k, v in payload.items() if v is not None}

    r = httpx.post(f"{base}/wp-json/wp/v2/posts/{post_id}", json=payload, auth=auth, timeout=30)
    r.raise_for_status()
    return new_status, importance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--auto-publish-threshold", type=int, default=3,
                        help="Promote to publish if importance >= N (0 = never publish). Default 3.")
    parser.add_argument("--limit", type=int, default=0, help="Max posts to process. 0 = no limit.")
    parser.add_argument("--dry-run", action="store_true", help="Skip the WP update step.")
    parser.add_argument("--rpm", type=int, default=15, help="Gemini RPM limit. Default 15 (free tier).")
    parser.add_argument("--model", default="gemini-2.5-flash")
    args = parser.parse_args()

    # Load env
    load_dotenv(args.env_file)
    base = os.environ["WP_URL"].rstrip("/")
    auth = (os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("ERROR: GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    # Add src to path so we can import the package
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from machinery_news_scraper.ai_processor import AIProcessor, PROMPT_TEMPLATE
    from machinery_news_scraper.models import Item
    from dataclasses import replace
    from datetime import datetime, timezone

    log.info("Fetching drafts needing AI from %s ...", base)
    drafts = fetch_drafts_needing_ai(base, auth)
    log.info("Found %d posts needing AI", len(drafts))

    if args.limit > 0:
        drafts = drafts[:args.limit]
        log.info("Limited to %d posts", len(drafts))

    if args.dry_run:
        for d in drafts[:10]:
            log.info("  WOULD process: #%d %s", d["id"], d["title"][:60])
        return 0

    ai = AIProcessor(api_key=gemini_key, model=args.model, rpm_limit=args.rpm)

    stats = {"processed": 0, "published": 0, "drafted": 0, "failed": 0}
    for i, d in enumerate(drafts, 1):
        log.info("[%d/%d] #%d %s", i, len(drafts), d["id"], d["title"][:60])

        # Build a minimal Item for the AI processor
        item = Item(
            source_id=d["source_id"] or "backfill",
            source_type="rss",
            external_id=d["source_url"],
            title=d["title"],
            url=d["source_url"],
            excerpt=d["excerpt"][:500] if d["excerpt"] else "",
            content_html=None,
            author=None,
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            language="en",
        )

        try:
            enriched = ai.process(item)
            if enriched.ai_summary is None:
                log.warning("  AI failed, skipping")
                stats["failed"] += 1
                continue

            new_status, importance = update_post_ai(
                base, auth, d["id"],
                ai_data={
                    "summary": enriched.ai_summary,
                    "category": enriched.ai_category,
                    "importance": enriched.ai_importance or 0,
                    "tags": list(enriched.ai_tags),
                    "buyer_angle": enriched.ai_buyer_angle,
                },
                auto_publish_threshold=args.auto_publish_threshold,
            )
            stats["processed"] += 1
            if new_status == "publish":
                stats["published"] += 1
            else:
                stats["drafted"] += 1
            log.info("  OK: cat=%s imp=%d status=%s",
                     enriched.ai_category, importance, new_status)
        except Exception as e:
            log.error("  FAIL: %s", e)
            stats["failed"] += 1

    log.info("=== Backfill done ===")
    for k, v in stats.items():
        log.info("  %s: %d", k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
