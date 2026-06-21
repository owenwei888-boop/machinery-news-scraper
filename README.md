# Machinery News Scraper

Python scraper for [the-packing-machine.com](https://the-packing-machine.com). Pulls machinery-industry RSS feeds, filters to English-only, pushes to a WordPress site running the `wp-rss-curator` plugin.

## Install

```powershell
cd machinery-news-scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

**Note (Windows + non-ASCII path):** If your project path contains non-ASCII characters (e.g. Chinese) and you're on Python 3.12.0, the editable-install `.pth` file may fail to decode under GBK locale. Symptom: any `python` invocation in the venv raises `UnicodeDecodeError` from `site.addpackage`. Two fixes:
1. Upgrade to Python 3.12.1+ or 3.13+ (cleanest — the bug is patched).
2. Delete `.venv\Lib\site-packages\_editable_impl_machinery_news_scraper.pth` and rely on `PYTHONPATH=src` for CLI invocations (pytest still works via the `pythonpath` config in `pyproject.toml`).

To run the CLI with workaround #2:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m machinery_news_scraper run --source all
```

## Configure

1. Copy `.env.example` to `.env`. Fill in `WP_URL`, `WP_USER`, `WP_APP_PASSWORD`.
   - WP Application Passwords: WP Admin → Users → your user → Application Passwords → Add New.
   - The WP user must have `edit_posts` capability.
2. Edit `sources/rss_feeds.yaml` to add/remove RSS sources.

## Run

```powershell
# Make the package importable (see install note above if needed)
$env:PYTHONPATH = "src"

# Run all sources
.\.venv\Scripts\python.exe -m machinery_news_scraper run --source all

# Or one source only
.\.venv\Scripts\python.exe -m machinery_news_scraper run --source packaging-europe
```

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Schedule (Windows Task Scheduler)

```powershell
# Run every 6 hours
schtasks /create /tn "MachineryNewsScraper" /tr "powershell.exe -Command 'cd C:\path\to\machinery-news-scraper; `$env:PYTHONPATH=`'src`'; .\.venv\Scripts\python.exe -m machinery_news_scraper run --source all'" /sc HOURLY /mo 6
```

## Schedule (Linux cron)

```
0 */6 * * * cd /opt/machinery-news-scraper && PYTHONPATH=src .venv/bin/python -m machinery_news_scraper run --source all >> /var/log/machinery-scraper.log 2>&1
```

## Architecture

See [docs/superpowers/specs/2026-06-20-machinery-news-aggregator-design.md](../docs/superpowers/specs/2026-06-20-machinery-news-aggregator-design.md).

## Phase 1 scope

This is Phase 1 of 4. Future phases:
- Phase 2: WP mu-plugin for AI summaries / tags / category / importance scoring
- Phase 3: Add LinkedIn + trade-show sources
- Phase 4: Polish, SEO, launch
