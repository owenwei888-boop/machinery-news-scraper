# src/machinery_news_scraper/ai_processor.py
"""AI enrichment via Google Gemini — generates summary / category / importance / tags.

Designed to fail gracefully: if AI fails for an item, the pipeline continues
and pushes the item WITHOUT ai_* fields. The site handles missing ai_*
fields cleanly (treats them as raw, importance=3).

Cost/quota: free-tier keys have tight per-day caps (observed as low as 20/day
on gemini-2.5-flash-lite in 2026). Pass a comma-separated key list to rotate
across quota pools; the processor only gives up when every key is dead.
Default model moved to gemini-3.5-flash-lite — 2.5-flash-lite is sunset for
newer keys.
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

Categorize using exactly one of these 6 slugs (the site's primary taxonomy).
Pick the MOST SPECIFIC category that fits — "industry" is a last resort, NOT a
default. If the item mentions any machine, part, material, robot, or trade
show, it does NOT belong in "industry":
- machines       → VFFS / HFFS / form-fill-seal / filling / capping / labeling /
                   coding / cartoning / palletizing / wrapping / pouch equipment,
                   new machine launches, machine tech upgrades, OEM product lines
- components     → sensors / vision systems / servo motors / drives / PLCs /
                   HMIs / valves / sealing bars / grippers / line software —
                   the parts and subsystems that go INTO machines
- materials      → films / laminates / mono-material / sustainable polymers /
                   recyclability rules / paper vs plastic / pouch stock / adhesives
- robotics       → pick-and-place / palletizing / depalletizing / collaborative
                   robots / AMRs / humanoid robots on packaging or production lines
- industry       → ONLY if none of the above fit: M&A / financing / regulation /
                   tariffs / market forecasts / general corporate moves
- trade-shows    → Interpack / Pack Expo / Hannover Messe / Chinaplas / iba /
                   exhibitor announcements / event coverage / new show dates

Importance calibration (use the FULL range; a batch of 10 items should have
roughly 1-2 items at 4-5, ~5 at 3, and 3-4 at 1-2 — never all 3s):
  5 = landmark launch, major M&A (>$500M), regulation with compliance deadline
  4 = significant new product line, notable acquisition, major plant investment
  3 = routine new product / order / partnership / ordinary company news
  2 = minor mention, local-market note, thin sourcing, vague roadmap talk
  1 = noise: job ads, giveaways, promos with no technical substance

Produce JSON ONLY — no markdown, no commentary, no code fences:

{{
  "summary": "100-200 word plain-English summary of what happened and why it matters to machinery buyers. Ground every claim in the title/excerpt — if the excerpt is missing, summarize only what the title asserts and say what remains unknown.",
  "category": "one of: machines | components | materials | robotics | industry | trade-shows",
  "importance": <integer 1-5 per the calibration above>,
  "tags": ["3-5 lowercase kebab-case tags, including company and technology names, e.g. \\"krones\\", \\"vffs-machine\\", \\"mono-material\\""],
  "buyer_angle": "1-2 sentences: what this means for someone evaluating packaging machinery purchases"
}}
"""


class AIProcessor:
    """Wraps Google Gemini client for batch processing of Items.

    Construction:
        ai = AIProcessor(api_key="AIza...")            # single key (legacy)
        ai = AIProcessor(api_key="key1,key2,key3")     # comma-separated rotation

    Key rotation: on a per-key fatal error (429 quota / 404 model-gone) the
    processor switches to the next key. Only when EVERY key is dead does it
    short-circuit, so two free-tier keys ≈ two daily quotas.

    On any error (network, parse, quota), process() returns the input item
    unchanged (no AI fields) and logs a warning.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash-lite",
        rpm_limit: int = 15,
    ):
        keys = [k.strip() for k in (api_key or "").split(",") if k.strip()]
        if not keys:
            raise ValueError("AIProcessor needs at least one API key")
        # Import inside __init__ so the module can be loaded without the SDK
        # installed (e.g., during tests that don't use AI).
        try:
            from google import genai
        except ImportError as e:
            raise ImportError(
                "google-genai SDK not installed. Run: pip install google-genai"
            ) from e
        self._clients = [(k, genai.Client(api_key=k)) for k in keys]
        self._client_idx = 0
        self._model = model
        self._rpm_limit = rpm_limit
        self._last_call_at = 0.0
        self._quota_exhausted = False  # True only when ALL keys are dead

    def _rotate_client(self, reason: str) -> bool:
        """Drop the current key and move to the next live one.

        Returns True if a live key remains, False when all keys are dead
        (caller then sets _quota_exhausted).
        """
        dead_key = self._clients.pop(self._client_idx)
        log.warning(
            "Gemini key #%d unusable (%s) — %d key(s) left",
            self._client_idx, reason, len(self._clients),
        )
        if self._client_idx >= len(self._clients):
            self._client_idx = 0
        self._last_call_at = 0.0  # fresh key, fresh rate budget
        return bool(self._clients)

    def process(self, item: Item) -> Item:
        """Enrich an Item with AI fields. Returns the (possibly new) Item.

        On failure: returns the original item unchanged, logs warning.
        Short-circuits silently (no rate-limit wait) once every key's quota is
        exhausted — otherwise the pipeline would burn ~6s per item × thousands
        of items on the rate limiter, blowing the workflow timeout.
        """
        while not self._quota_exhausted:
            key, client = self._clients[self._client_idx]
            try:
                self._respect_rate_limit()
                prompt = PROMPT_TEMPLATE.format(
                    title=item.title,
                    excerpt=item.excerpt[:500] if item.excerpt else "(no excerpt)",
                    source_id=item.source_id,
                )
                response = client.models.generate_content(
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
                msg = str(e)
                # 429 RESOURCE_EXHAUSTED = this key's daily quota is gone;
                # 404 = model sunset for this key's project (newer keys lose
                # old models); 403 PERMISSION_DENIED "free tier is not
                # available for this project" = project-level ban (seen
                # 2026-10-25 — every call from that key fails). All three are
                # per-KEY fatal → rotate, don't grind item-by-item (~6s each).
                is_quota = (
                    "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
                )
                is_model_gone = " 404" in msg or "NOT_FOUND" in msg or (
                    "no longer available" in msg.lower()
                )
                is_banned = "403" in msg or "PERMISSION_DENIED" in msg or (
                    "free tier is not available" in msg.lower()
                )
                if is_quota or is_model_gone or is_banned:
                    reason = (
                        "quota" if is_quota
                        else "model gone" if is_model_gone
                        else "banned (403)"
                    )
                    if not self._rotate_client(reason):
                        log.warning(
                            "All Gemini keys exhausted — short-circuiting remaining items"
                        )
                        self._quota_exhausted = True
                        return item
                    continue  # retry same item on the next key
                log.warning("AI processing failed for %s: %s", item.url, e)
                return item  # unchanged — pipeline pushes raw
        return item

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
