#!/usr/bin/env python
"""Backfill AI enrichment for the Git/md pipeline (machinery-cf-site).

The scraper's short-circuit-on-quota behavior leaves items published with
empty aiSummary and never retries them. This script walks the Astro site's
src/content/news/*.md, finds items whose aiSummary is empty, re-enriches them
via AIProcessor (with the multi-key rotation pool), and rewrites the files in
place — byte-compatible with pusher/git_publisher.py's rendering. The `featured`
flag is deliberately left untouched (flipping thousands of items at once would
reshuffle the site).

Runs where Google APIs are reachable (GitHub Actions or a local machine with
a proxy). Commit & push are done by the surrounding workflow, same as the
scraper itself.

Usage:
    python backfill_md.py --site-dir ./machinery-cf-site --limit 300
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from machinery_news_scraper.ai_processor import AIProcessor  # noqa: E402
from machinery_news_scraper.models import Item  # noqa: E402
from machinery_news_scraper.pusher.git_publisher import (  # noqa: E402
    FALLBACK_CATEGORY,
    VALID_CATEGORIES,
    _yaml_scalar,
)

# Frontmatter line scanner — same "regex, no YAML dep" approach as git_publisher.
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    m = _FM_RE.search(text)
    if not m:
        return None
    out: dict[str, str] = {}
    for k, v in _KV_RE.findall(m.group(1)):
        if k not in out:  # first occurrence wins
            out[k] = v.strip()
    return out


def _unquote(v: str | None, default: str = "") -> str:
    if v is None:
        return default
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def needs_enrichment(fm: dict[str, str]) -> bool:
    return _unquote(fm.get("aiSummary")) == ""


def md_to_item(fm: dict[str, str], path: Path) -> Item:
    published = _unquote(fm.get("publishedAt")) or "1970-01-01T00:00:00+00:00"
    try:
        pub_dt = datetime.fromisoformat(published)
    except ValueError:
        pub_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return Item(
        source_id=_unquote(fm.get("sourceId")) or "backfill",
        source_type=_unquote(fm.get("sourceType"), "rss") or "rss",  # type: ignore[arg-type]
        external_id=path.stem,
        title=_unquote(fm.get("title"), "Untitled"),
        url=_unquote(fm.get("sourceUrl")),
        excerpt="",  # not persisted in md — AI prompt falls back to title-only
        content_html=None,
        author=None,
        published_at=pub_dt,
        fetched_at=datetime.now(timezone.utc),
        language="en",
    )


def rewrite_md(path: Path, item: Item) -> None:
    """Regenerate the file: original non-AI fields + fresh AI fields.

    Mirrors git_publisher._render exactly (key order, quoting, body shape)
    except `featured`, which keeps its previous value.
    """
    old = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(old) or {}

    category = item.ai_category or FALLBACK_CATEGORY
    if category not in VALID_CATEGORIES:
        category = FALLBACK_CATEGORY

    new_fm = {
        "title": item.title,
        "publishedAt": _unquote(fm.get("publishedAt")),
        "sourceType": _unquote(fm.get("sourceType"), "rss"),
        "sourceId": _unquote(fm.get("sourceId")),
        "sourceUrl": item.url,
        "aiSummary": item.ai_summary or "",
        "aiImportance": int(item.ai_importance) if item.ai_importance is not None else 3,
        "aiCategory": category,
        "aiTags": list(item.ai_tags or ()),
        "aiBuyerAngle": item.ai_buyer_angle or "",
        # featured must stay a YAML boolean — passing the string through
        # _yaml_scalar would quote it ("false") and fail the site's Zod
        # schema, killing the whole Cloudflare build.
        "featured": _unquote(fm.get("featured"), "false").lower() == "true",
    }
    lines = ["---"]
    for key, val in new_fm.items():
        lines.append(f"{key}: {_yaml_scalar(val)}")
    lines.append("---")

    body = (item.ai_summary or "").strip() or "（暂无摘要）"
    if item.ai_buyer_angle:
        body += "\n\n## 采购视角\n\n" + item.ai_buyer_angle
    if item.url:
        body += f"\n\n[查看原文]({item.url})"

    path.write_text("\n".join(lines) + f"\n\n{body}\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site-dir", default="./machinery-cf-site",
                    help="Clone of machinery-cf-site (default: ./machinery-cf-site)")
    ap.add_argument("--limit", type=int, default=300,
                    help="Max items to enrich this run (default 300; ~25 min at rpm 12)")
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--rpm", type=int, default=12)
    ap.add_argument("--newest-first", action="store_true", default=True,
                    help="Enrich newest items first (site home window covers recent)")
    args = ap.parse_args()

    news_dir = Path(args.site_dir).resolve() / "src" / "content" / "news"
    if not news_dir.exists():
        print(f"ERROR: news dir not found: {news_dir}", file=sys.stderr)
        return 1

    import os
    keys = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
    single = os.environ.get("GEMINI_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)
    if not keys:
        print("ERROR: GEMINI_API_KEYS / GEMINI_API_KEY not set", file=sys.stderr)
        return 1

    # Collect candidates: md files with empty aiSummary, newest first.
    candidates: list[tuple[Path, dict[str, str]]] = []
    for md in news_dir.glob("*.md"):
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if fm and needs_enrichment(fm):
            candidates.append((md, fm))
    candidates.sort(
        key=lambda c: _unquote(c[1].get("publishedAt")), reverse=args.newest_first
    )
    print(f"Backfill: {len(candidates)} items lack summaries; enriching up to {args.limit}")

    ai = AIProcessor(api_key=",".join(keys), model=args.model, rpm_limit=args.rpm)

    done = skipped = 0
    for md, fm in candidates[: args.limit]:
        if ai._quota_exhausted:  # noqa: SLF001 — diagnostic surface for the loop
            print(f"All keys exhausted after {done} items — stopping early.")
            break
        item = ai.process(md_to_item(fm, md))
        if not item.ai_summary:
            skipped += 1
            continue
        rewrite_md(md, item)
        done += 1
        if done % 25 == 0:
            print(f"  {done} enriched...")

    print(f"DONE: {done} enriched, {skipped} failed this run, "
          f"{max(0, len(candidates) - done - skipped)} still empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
