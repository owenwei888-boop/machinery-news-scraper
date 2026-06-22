# src/machinery_news_scraper/ai_processor.py
"""AI enrichment via Google Gemini — generates summary / category / importance / tags.

Designed to fail gracefully: if AI fails for an item, the pipeline continues
and pushes the item WITHOUT ai_* fields. The WP plugin handles missing ai_*
fields cleanly (treats them as raw, importance=0).

Cost: free tier of gemini-1.5-flash is 15 RPM + 1500 requests/day, which
covers ~100 articles/day at 4-5 cron runs. For higher volume, rotate keys
or upgrade.
"""
from __future__ import annotations
import json
import logging
import re
import time
from dataclasses import replace
from typing import Any, Optional

from .models import Item

log = logging.getLogger(__name__)


CATEGORIES = (
    "machines",
    "components",
    "materials",
    "robotics",
    "industry",
    "trade-shows",
)


PROMPT_TEMPLATE = """You are an editor at a B2B packaging-machinery industry news site
read by procurement managers and operations engineers at FMCG / pharma /
industrial companies worldwide.

Given this industry news item:
- Title: {title}
- Excerpt: {excerpt}
- Source: {source_id}

Categorize using exactly one of these 6 slugs (the WP site's primary taxonomy):
- machines       → VFFS / HFFS / form-fill-seal / labeling / pouch equipment
- components     → sensors / servo motors / PLCs / valves / sealing bars (the parts)
- materials      → films / laminates / sustainable polymers / recyclability regs
- robotics       → pick-place / palletizing / collaborative robots in packaging lines
- industry       → M&A / regulation / trade news / market analysis
- trade-shows    → Interpack / Pack Expo / Hannover Messe / Chinaplas announcements

Produce JSON ONLY — no markdown, no commentary, no code fences:

{{
  "summary": "100-200 word plain-English summary explaining what happened and why it matters to machinery buyers",
  "category": "one of: machines | components | materials | robotics | industry | trade-shows",
  "importance": <integer 1-5, where 5 = frontpage major launch, 1 = minor mention>,
  "tags": ["3-5 lowercase kebab-case tags"],
  "buyer_angle": "1-2 sentences: what this means for someone evaluating packaging machinery purchases"
}}
"""


class AIProcessor:
    """Wraps Google Gemini client for batch processing of Items.

    Construction:
        ai = AIProcessor(api_key="AIza...")
        item = ai.process(item)  # returns NEW Item with ai_* fields set

    On any error (network, parse, quota), process() returns the input item
    unchanged (no AI fields) and logs a warning.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
        rpm_limit: int = 15,
    ):
        # Import inside __init__ so the module can be loaded without the SDK
        # installed (e.g., during tests that don't use AI).
        try:
            from google import genai
        except ImportError as e:
            raise ImportError(
                "google-genai SDK not installed. Run: pip install google-genai"
            ) from e
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._rpm_limit = rpm_limit
        self._last_call_at = 0.0

    def process(self, item: Item) -> Item:
        """Enrich an Item with AI fields. Returns the (possibly new) Item.

        On failure: returns the original item unchanged, logs warning.
        """
        try:
            self._respect_rate_limit()
            prompt = PROMPT_TEMPLATE.format(
                title=item.title,
                excerpt=item.excerpt[:500] if item.excerpt else "(no excerpt)",
                source_id=item.source_id,
            )
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            parsed = self._parse_response(response.text)
            return replace(
                item,
                ai_summary=parsed.get("summary"),
                ai_category=parsed.get("category"),
                ai_importance=parsed.get("importance"),
                ai_tags=tuple(parsed.get("tags", []) or ()),
                ai_buyer_angle=parsed.get("buyer_angle"),
            )
        except Exception as e:
            log.warning("AI processing failed for %s: %s", item.url, e)
            return item  # unchanged — pipeline pushes raw

    def _respect_rate_limit(self) -> None:
        """Enforce min interval between calls (60s / rpm_limit)."""
        min_interval = 60.0 / self._rpm_limit if self._rpm_limit else 0
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call_at = time.monotonic()

    @staticmethod
    def _parse_response(text: str) -> dict[str, Any]:
        """Parse Gemini's response as JSON, stripping common wrappers."""
        if not text:
            return {}
        # Strip ```json ... ``` code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()
        # Sometimes Gemini wraps in { "result": {...} } — handle both
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "result" in parsed and isinstance(parsed["result"], dict):
            return parsed["result"]
        return parsed if isinstance(parsed, dict) else {}


def process_items(items: list[Item], ai: Optional["AIProcessor"]) -> list[Item]:
    """Process a list of items. If ai is None, returns items unchanged."""
    if ai is None:
        return items
    out: list[Item] = []
    for i, item in enumerate(items, 1):
        log.info("AI processing [%d/%d]: %s", i, len(items), item.title[:60])
        out.append(ai.process(item))
    return out
