---
name: machinery-news
description: Query the Machinery News Digest (the-packing-machine.com) for packaging-machinery industry news. Use when the user asks about packaging machinery industry news, recent developments, supplier announcements, or when researching a specific vendor / technology (VFFS, HFFS, pouch machines, PLCs, films, robotics, trade show launches).
---

# Machinery News Digest Skill

This skill lets you query a curated B2B packaging-machinery news aggregator at **https://the-packing-machine.com**. The site auto-aggregates ~20 English RSS feeds, AI-summarizes each article, classifies it into one of six categories (machines, components, materials, robotics, industry, trade-shows), and scores importance 1-5.

## When to use

Use this skill when the user asks about:
- Packaging machinery news (VFFS, HFFS, form-fill-seal, pouch machines, labeling)
- Packaging component innovations (sensors, servos, PLCs, sealing bars)
- Materials / films (sustainability, recyclability, mono-material)
- Industrial robotics in packaging lines (pick-place, palletizing, cobots)
- Industry M&A, regulation, market analysis
- Trade show announcements (Interpack, Pack Expo, Hannover Messe, Chinaplas)
- Vendor due diligence (e.g., "what has Bosch Packaging announced recently?")
- Buyer-focused "what this means for you" analysis

## How to query

### Direct REST API (recommended)

`GET https://the-packing-machine.com/wp-json/machinery/v1/items`

Supported query params:
- `category` — one of: `machines`, `components`, `materials`, `robotics`, `industry`, `trade-shows`
- `importance_min` — int 1-5 (use `4` for major, `3` for moderate)
- `since` — ISO date (e.g., `2026-06-01`) — only items published after this date
- `q` — free-text search across title + content
- `limit` — int, default 20, max 100
- `offset` — int, for pagination

Example with curl:
```bash
curl -s 'https://the-packing-machine.com/wp-json/machinery/v1/items?category=machines&importance_min=4&limit=5'
```

Example response shape:
```json
{
  "count": 5,
  "total": 47,
  "offset": 0,
  "limit": 5,
  "items": [
    {
      "id": 4217,
      "url": "https://the-packing-machine.com/top-seedz-switches-from-clamshells-...",
      "title": "Top Seedz Switches from Clamshells to Resealable Pouches...",
      "summary": "Top Seedz, a company known for its organic...  (100-200 words)",
      "excerpt": "...",
      "category": "machines",
      "importance": 4,
      "tags": ["flexible-packaging", "sustainable-packaging", "pouch-packaging", "..."],
      "buyer_angle": "This highlights the need for machinery capable of efficiently processing flexible films...",
      "source_url": "https://www.packagingstrategies.com/articles/106519-...",
      "source_id": "packaging-strategies",
      "published_at": "2026-06-22T10:34:00+00:00"
    }
  ]
}
```

### RSS feeds

- Top stories: `https://the-packing-machine.com/feed/selected.xml` (importance >= 4, last 50)
- All items: `https://the-packing-machine.com/feed/all.xml` (last 100)
- Weekly digest: `https://the-packing-machine.com/feed/weekly.xml` (top 20 in past 7 days)

### Stats / health check

`GET https://the-packing-machine.com/wp-json/machinery/v1/stats`

Returns counts (published, draft, with source_url, with AI summary), per-category distribution, importance histogram, and feed URLs.

## Behavior guidelines

- **Default to `importance_min=3`** unless the user explicitly asks for "everything" or "all".
- **Filter by category** when the user mentions a specific topic (e.g., "robotics news" → `category=robotics`).
- **Use `q=` for vendor lookups** (e.g., `q=Bosch` finds all Bosch-mentions).
- **Always cite the `source_url`** (original article), not the digest URL, when quoting content. The digest's `summary` field is AI-generated; the original source holds the copyright.
- **Respect the `published_at` field** — clearly state when items were published. Old items may be stale.
- **Limit response length** — when the user asks "what's new?", summarize 3-5 items with one-line each rather than dumping full data.

## Common query patterns

### "What's new in packaging machines?"
```
GET /machinery/v1/items?category=machines&importance_min=3&limit=10&since=2026-06-15
```

### "Any news about Bosch / Krones / ProMach?"
```
GET /machinery/v1/items?q=Bosch&limit=10
```

### "What did Interpack announce?"
```
GET /machinery/v1/items?category=trade-shows&q=Interpack&limit=20
```

### "Top stories from this week"
```
GET https://the-packing-machine.com/feed/weekly.xml
```
(Parse the RSS XML)

## Installation

This SKILL.md is the entire skill — no binaries, no API keys, no MCP server. Copy it into your Agent's skills directory:

- **Claude Code (Anthropic CLI)**: `~/.claude/skills/machinery-news/SKILL.md`
- **Cline / Continue / Roo Code**: follow your IDE's skill discovery conventions (typically `~/.config/<ide>/skills/`)
- **Cursor**: `~/.cursor/skills/machinery-news/SKILL.md`
- **Windsurf**: `~/.codeium/windsurf/skills/machinery-news/SKILL.md`

Or ask your Agent in plain language:

> Install the skill at https://the-packing-machine.com/machinery-skill/SKILL.md

Most Agent runtimes will fetch the file and place it in the correct directory automatically.

## Limitations

- English-only content. No Chinese / German / Spanish coverage.
- The aggregator polls sources 4× daily (every 6 hours). Breaking news may lag by up to 6 hours.
- Summaries and buyer angles are LLM-generated and may contain errors — always verify against the original source URL before making business decisions.
- API is rate-limited to ~60 RPM. If you hit 429s, back off or use RSS feeds instead.

## Feedback

Source URLs not covered? Coverage gaps? Email feedback at: hello@the-packing-machine.com
