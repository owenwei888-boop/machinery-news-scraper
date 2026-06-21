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
from .pusher.retry import push_with_retry
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
    return p


def main(argv: list[str] | None = None) -> int:
    # argparse calls sys.exit(2) on errors (unknown subcommand, missing required arg, -h).
    # Translate SystemExit.code into a return value so callers (tests, __main__) get an int.
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2

    if args.cmd == "run":
        return _cmd_run(args)
    return 2  # unreachable due to required=True


def _cmd_run(args) -> int:
    setup_logging(args.log_level)

    if not Path(args.env_file).exists():
        print(f"ERROR: env file not found: {args.env_file}", file=sys.stderr)
        return 1
    load_dotenv(args.env_file)

    wp_url = os.environ.get("WP_URL")
    wp_user = os.environ.get("WP_USER")
    wp_pass = os.environ.get("WP_APP_PASSWORD")
    if not (wp_url and wp_user and wp_pass):
        print("ERROR: WP_URL / WP_USER / WP_APP_PASSWORD must be set", file=sys.stderr)
        return 1

    sources = load_sources(Path(args.feeds_yaml))
    if args.source != "all":
        sources = [s for s in sources if s.id == args.source]
        if not sources:
            print(f"ERROR: source '{args.source}' not found in {args.feeds_yaml}", file=sys.stderr)
            return 1

    client = WPClient(base_url=wp_url, username=wp_user, app_password=wp_pass)
    push_fn = lambda item: client.push(item)  # noqa: E731

    stats = run_pipeline(
        sources=sources,
        scraper_fn=scrape_rss,
        push_fn=push_fn,
        queue_path=args.queue_db,
        max_attempts=args.max_attempts,
        base_delay=args.base_delay,
    )
    print(f"pipeline done: {stats}")
    return 0
