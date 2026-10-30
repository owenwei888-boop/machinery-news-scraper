# src/machinery_news_scraper/pusher/git_publisher.py
"""Cloudflare / Jamstack publisher — a drop-in replacement for WPClient.

Same interface as WPClient, so pipeline.py and cli.py need no logic changes:
    GitPublisher.push(item)                  -> PushResult
    GitPublisher.fetch_existing_source_urls()-> set[str]

Instead of POSTing to /wp-json/wp/v2/posts, it writes one Markdown file per
Item into the Astro site's content collection
(machinery-cf-site/src/content/news/*.md), matching src/content/config.ts.
The actual `git commit && git push` is done once by the GitHub Actions
workflow at the end of the run — one run = one commit = one Cloudflare rebuild.

Field mapping (Item attribute -> frontmatter key), verified against models.py:
    item.url            -> sourceUrl      (also drives dedup)
    item.source_id      -> sourceId
    item.source_type    -> sourceType
    item.title          -> title
    item.published_at   -> publishedAt    (datetime -> ISO 8601)
    item.ai_summary     -> aiSummary
    item.ai_importance  -> aiImportance   (1-5)
    item.ai_category    -> aiCategory     (6-enum; out-of-range -> "industry")
    item.ai_tags        -> aiTags         (tuple -> list)
    item.ai_buyer_angle -> aiBuyerAngle
    importance >= auto_publish_threshold -> featured: true

Enable from cli.py by setting env MACHINERY_PUBLISHER=git (see scraper-bridge/README.md).
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from .wp_client import PushResult, PushStatus
from ..models import Item

log = logging.getLogger(__name__)

# Must stay byte-for-byte identical with machinery-cf-site/src/content/config.ts.
VALID_CATEGORIES = {
    "machines", "components", "materials",
    "robotics", "industry", "trade-shows",
}
FALLBACK_CATEGORY = "industry"

DEFAULT_SITE_DIR = "./machinery-cf-site"

_FRONTMATTER_RE = re.compile(
    r"^sourceUrl:\s*\"?([^\"\n]+)\"?\s*$", re.MULTILINE
)


def _slugify(text: str, max_len: int = 70) -> str:
    text = re.sub(r"[^\w\s-]", "", (text or "").lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "item"


def _yaml_scalar(v) -> str:
    """Render a single Python value as a safe YAML scalar."""
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yaml_scalar(x) for x in v) + "]" if v else "[]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


class GitPublisher:
    """Writes Items to Markdown. Drop-in for WPClient.push()."""

    def __init__(
        self,
        site_dir: str | Path = DEFAULT_SITE_DIR,
        auto_publish_threshold: int = 0,
        category_map: dict | None = None,
    ):
        self.site_dir = Path(site_dir).resolve()
        self.news_dir = self.site_dir / "src" / "content" / "news"
        self.threshold = auto_publish_threshold
        self._seen: set[str] | None = None

    def fetch_existing_source_urls(self) -> set[str]:
        """Scan news/*.md (and archive/news/*.md) for `sourceUrl:` lines.

        Uses a regex line-scan instead of a full YAML parse: O(lines), no
        PyYAML dependency, and fast even with thousands of files.

        Archive MUST be scanned too: pruned items keep surfacing in feeds,
        and without their URLs in this set each run re-enriches (re-buys)
        them — measured ~240 redundant AI calls per run on 2026-10-30,
        ~80% of the DeepSeek bill.
        """
        urls: set[str] = set()
        scanned = 0
        dirs = [self.news_dir, self.site_dir / "archive" / "news"]
        if not self.news_dir.exists():
            log.info("news dir does not exist yet (first run): %s", self.news_dir)
        for d in dirs:
            if not d.exists():
                continue
            for md in d.glob("*.md"):
                try:
                    m = _FRONTMATTER_RE.search(md.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
                if m:
                    urls.add(m.group(1).strip())
                    scanned += 1
        self._seen = urls
        log.info("git publisher: %d existing items on disk (%d urls)",
                 scanned, len(urls))
        return urls

    def push(self, item: Item) -> PushResult:
        url = getattr(item, "url", "") or ""
        if self._seen is None:
            self.fetch_existing_source_urls()
        if url and url in (self._seen or ()):
            return PushResult(PushStatus.DUPLICATE)

        try:
            slug = self._resolve_slug(item, url)
            fm_block, body = self._render(item)
            self.news_dir.mkdir(parents=True, exist_ok=True)
            (self.news_dir / f"{slug}.md").write_text(
                f"{fm_block}\n\n{body}\n", encoding="utf-8"
            )
        except Exception as e:
            log.error("git publisher write failed for %s: %s", url, e)
            return PushResult(PushStatus.BAD_REQUEST, error=str(e))

        if url:
            self._seen.add(url)
        return PushResult(PushStatus.CREATED)

    def _resolve_slug(self, item: Item, url: str) -> str:
        base = _slugify(getattr(item, "title", "") or getattr(item, "source_id", "") or "item")
        path = self.news_dir / f"{base}.md"
        if path.exists():
            suffix = hashlib.sha1((url or base).encode("utf-8")).hexdigest()[:6]
            base = f"{base}-{suffix}"
        return base

    def _render(self, item: Item) -> tuple[str, str]:
        category = getattr(item, "ai_category", None) or FALLBACK_CATEGORY
        if category not in VALID_CATEGORIES:
            category = FALLBACK_CATEGORY

        importance = getattr(item, "ai_importance", None)
        importance_val = int(importance) if importance is not None else 3

        pub = getattr(item, "published_at", None)
        published_at = pub.isoformat() if hasattr(pub, "isoformat") else str(pub)

        ai_tags = list(getattr(item, "ai_tags", ()) or ())
        ai_summary = getattr(item, "ai_summary", None) or ""
        buyer = getattr(item, "ai_buyer_angle", None) or ""
        featured = bool(self.threshold > 0 and importance is not None
                        and importance_val >= self.threshold)

        fm = {
            "title": getattr(item, "title", "Untitled"),
            "publishedAt": published_at,
            "sourceType": getattr(item, "source_type", "unknown"),
            "sourceId": getattr(item, "source_id", "") or "",
            "sourceUrl": getattr(item, "url", "") or "",
            "aiSummary": ai_summary,
            "aiImportance": importance_val,
            "aiCategory": category,
            "aiTags": ai_tags,
            "aiBuyerAngle": buyer,
            "featured": featured,
        }
        lines = ["---"]
        for key, val in fm.items():
            lines.append(f"{key}: {_yaml_scalar(val)}")
        lines.append("---")

        body = ai_summary.strip() or "（暂无摘要）"
        if buyer:
            body += "\n\n## 采购视角\n\n" + buyer
        source_url = fm["sourceUrl"]
        if source_url:
            body += f'\n\n[查看原文]({source_url})'
        return "\n".join(lines), body
