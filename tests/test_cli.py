# tests/test_cli.py
from pathlib import Path
import pytest
from machinery_news_scraper import cli
from machinery_news_scraper.pusher.wp_client import PushResult, PushStatus


def _fake_rss_bytes() -> bytes:
    return b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Bosch VFFS</title>
    <link>https://example.com/a</link>
    <description>Bosch has launched a new machine for packaging industry.</description>
    <pubDate>Mon, 20 Jun 2026 09:00:00 +0000</pubDate>
    <guid>https://example.com/a</guid>
  </item>
</channel></rss>"""


def test_cli_run_all_sources(tmp_path, monkeypatch, capsys):
    """`run --source all` loads sources from --feeds-yaml, scrapes, pushes, exits 0."""
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text("""
sources:
  - id: test
    name: Test
    url: https://example.com/feed
    source_type: rss
""")
    env = tmp_path / ".env"
    env.write_text("WP_URL=https://wp.example.com\nWP_USER=u\nWP_APP_PASSWORD=p\n")

    # Bypass network: RSS fetcher returns canned XML; WPClient.push returns success.
    monkeypatch.setattr(
        "machinery_news_scraper.scrapers.rss_scraper._fetch_bytes",
        lambda url: _fake_rss_bytes(),
    )
    monkeypatch.setattr(
        "machinery_news_scraper.cli.WPClient.push",
        lambda self, item: PushResult(PushStatus.CREATED, wp_id=1),
    )

    exit_code = cli.main([
        "run", "--source", "all",
        "--feeds-yaml", str(feeds),
        "--env-file", str(env),
        "--queue-db", str(tmp_path / "q.db"),
        "--max-attempts", "1",
        "--base-delay", "0.01",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "pipeline done" in out.lower()


def test_cli_invalid_subcommand(capsys):
    exit_code = cli.main(["frobnicate"])
    assert exit_code == 2  # argparse error
