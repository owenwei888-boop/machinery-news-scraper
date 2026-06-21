# src/machinery_news_scraper/config.py
"""Load and validate source config from YAML."""
from __future__ import annotations
from pathlib import Path
import yaml
from .models import Source

class ConfigError(Exception):
    """Raised when source config is invalid."""


def load_sources(path: Path) -> list[Source]:
    if not path.exists():
        raise ConfigError(f"Source config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "sources" not in raw:
        raise ConfigError(f"Source config missing 'sources' key: {path}")

    sources: list[Source] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw["sources"]):
        try:
            src = Source(
                id=entry["id"],
                name=entry["name"],
                url=entry["url"],
                source_type=entry["source_type"],
            )
        except KeyError as e:
            raise ConfigError(f"Source #{i} missing field {e.args[0]}") from e
        if src.id in seen_ids:
            raise ConfigError(f"duplicate source id: {src.id}")
        seen_ids.add(src.id)
        sources.append(src)
    return sources
