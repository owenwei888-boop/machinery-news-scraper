# src/machinery_news_scraper/cli.py
"""Command-line entry: `python -m machinery_news_scraper run --source all`."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_sources
from .logging_setup import setup as setup_logging
from .pipeline import run_pipeline
from .pusher.wp_client import WPClient
from .scrapers.rss_scraper import scrape_rss


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="machinery-news-scraper")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Scrape sources and push to WP")
    run.add_argument("--source", default="all",
                     help="Source id, or 'all' for every configured source")
    run.add_argument("--feeds-yaml", default="sources/rss_feeds.yaml",
                     help="Path to YAML source config")
    run.add_argument("--env-file", default=".env",
                     help="Path to .env file with WP_URL, WP_USER, WP_APP_PASSWORD")
    run.add_argument("--queue-db", default="queue.db",
                     help="Path to SQLite queue database")
    run.add_argument("--max-attempts", type=int, default=3,
                     help="Max push attempts on transient errors")
    run.add_argument("--base-delay", type=float, default=1.0,
                     help="Base delay in seconds for exponential backoff")
    run.add_argument("--log-level", default="INFO",
                     choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    # AI options — all optional. If --ai-provider is set, AI enrichment runs.
    run.add_argument("--ai-provider", default=None,
                     choices=["gemini"],
                     help="AI provider for summary/category/importance. Reads API key from env.")
    run.add_argument("--ai-model", default="gemini-3.5-flash-lite",
                     help="AI model (default: gemini-3.5-flash-lite; 2.5-flash-lite is sunset for new keys)")
    run.add_argument("--auto-publish-threshold", type=int, default=0,
                     help="Auto-publish items with ai_importance >= this (0=never, default). "
                          "Recommended: 3 (frontpage-worthy) or 4 (only major).")
    run.add_argument("--ai-rpm", type=int, default=15,
                     help="Max AI API calls per minute (rate limit, default 15 for Gemini free tier)")
    run.add_argument("--map-categories", action="store_true", default=False,
                     help="Auto-query WP categories and map AI category slugs to WP IDs "
                          "(machines, components, materials, robotics, industry, trade-shows)")
    run.add_argument("--no-predup-check", action="store_true", default=False,
                     help="Skip the pre-dedup WP URL fetch. Saves a few seconds but wastes Gemini quota.")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    if args.cmd == "run":
        return _cmd_run(args)
    return 2


def _cmd_run(args) -> int:
    setup_logging(args.log_level)

    publisher = os.environ.get("MACHINERY_PUBLISHER", "wp").lower()

    # .env is only required for the WP path (it holds WP creds). The Git path
    # reads GEMINI_API_KEY + MACHINERY_* straight from the environment.
    if publisher == "wp" and not Path(args.env_file).exists():
        print(f"ERROR: env file not found: {args.env_file}", file=sys.stderr)
        return 1
    if Path(args.env_file).exists():
        load_dotenv(args.env_file)

    sources = load_sources(Path(args.feeds_yaml))
    if args.source != "all":
        sources = [s for s in sources if s.id == args.source]
        if not sources:
            print(f"ERROR: source '{args.source}' not found in {args.feeds_yaml}", file=sys.stderr)
            return 1

    # Build AI processor if configured
    ai_processor = None
    if args.ai_provider == "gemini":
        # GEMINI_API_KEYS (comma-separated, rotation) wins; fall back to the
        # legacy single GEMINI_API_KEY. Duplicates dropped, order preserved.
        keys = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
        single = os.environ.get("GEMINI_API_KEY", "").strip()
        if single and single not in keys:
            keys.append(single)
        if not keys:
            print("ERROR: --ai-provider=gemini but GEMINI_API_KEYS/GEMINI_API_KEY not set in env", file=sys.stderr)
            return 1
        from .ai_processor import AIProcessor  # lazy import — google-genai is optional
        ai_processor = AIProcessor(
            api_key=",".join(keys),
            model=args.ai_model,
            rpm_limit=args.ai_rpm,
        )
        print(f"AI: enabled ({args.ai_model}, {len(keys)} key(s), auto-publish >= {args.auto_publish_threshold})")
    else:
        print("AI: disabled (use --ai-provider gemini to enable)")

    # --- publisher construction -------------------------------------------------
    if publisher == "git":
        from .pusher.git_publisher import GitPublisher
        site_dir = os.environ.get("MACHINERY_SITE_DIR", "./machinery-cf-site")
        client = GitPublisher(
            site_dir=site_dir,
            auto_publish_threshold=args.auto_publish_threshold,
        )
        print(f"Publisher: GIT  (site_dir={site_dir}, featured when importance >= {args.auto_publish_threshold})")
    else:
        wp_url = os.environ.get("WP_URL")
        wp_user = os.environ.get("WP_USER")
        wp_pass = os.environ.get("WP_APP_PASSWORD")
        if not (wp_url and wp_user and wp_pass):
            print("ERROR: WP_URL / WP_USER / WP_APP_PASSWORD must be set "
                  "(or switch publisher with MACHINERY_PUBLISHER=git)", file=sys.stderr)
            return 1

        category_map: dict = {}
        if args.map_categories:
            import httpx as _httpx
            try:
                r = _httpx.get(
                    f"{wp_url.rstrip('/')}/wp-json/wp/v2/categories?per_page=100",
                    auth=(wp_user, wp_pass),
                    timeout=120.0,
                )
                r.raise_for_status()
                for c in r.json():
                    category_map[c["slug"]] = c["id"]
                print(f"WP categories: {category_map}")
            except Exception as e:
                print(f"WARN: failed to fetch WP categories: {e}", file=sys.stderr)

        client = WPClient(
            base_url=wp_url,
            username=wp_user,
            app_password=wp_pass,
            auto_publish_threshold=args.auto_publish_threshold,
            category_map=category_map,
        )
        print("Publisher: WORDPRESS")

    push_fn = lambda item: client.push(item)  # noqa: E731

    # Pre-fetch existing source URLs so we can skip AI + push on duplicates.
    # For GitPublisher this scans news/*.md; for WPClient it pages WP REST.
    existing_urls: set[str] | None = None
    if not args.no_predup_check:
        try:
            existing_urls = client.fetch_existing_source_urls()
        except Exception as e:
            print(f"WARN: pre-dedup check failed ({e}); continuing without", file=sys.stderr)

    stats = run_pipeline(
        sources=sources,
        scraper_fn=scrape_rss,
        push_fn=push_fn,
        queue_path=args.queue_db,
        max_attempts=args.max_attempts,
        base_delay=args.base_delay,
        ai_processor=ai_processor,
        existing_urls=existing_urls,
    )
    print(f"pipeline done: {stats}")
    return 0
