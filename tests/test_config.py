# tests/test_config.py
from pathlib import Path
import pytest
from machinery_news_scraper.config import load_sources, ConfigError


def test_load_sources_returns_list(tmp_path: Path):
    yaml = tmp_path / "feeds.yaml"
    yaml.write_text("""
sources:
  - id: test-source
    name: Test Source
    url: https://example.com/feed.xml
    source_type: rss
""")
    sources = load_sources(yaml)
    assert len(sources) == 1
    assert sources[0].id == "test-source"
    assert sources[0].source_type == "rss"


def test_load_sources_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_sources(tmp_path / "nonexistent.yaml")


def test_load_sources_raises_on_missing_sources_key(tmp_path: Path):
    yaml = tmp_path / "feeds.yaml"
    yaml.write_text("foo: bar\n")
    with pytest.raises(ConfigError, match="missing 'sources'"):
        load_sources(yaml)


def test_load_sources_raises_on_duplicate_ids(tmp_path: Path):
    yaml = tmp_path / "feeds.yaml"
    yaml.write_text("""
sources:
  - id: dup
    name: First
    url: https://example.com/1
    source_type: rss
  - id: dup
    name: Second
    url: https://example.com/2
    source_type: rss
""")
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_sources(yaml)
