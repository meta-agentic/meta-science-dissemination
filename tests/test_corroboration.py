"""Tests for the widened corroborator registry and per-feed health.

Two failures this file exists to prevent: the "two independent outlets" route
to publication being decorative because only two independent sources are
registered (ADR-003), and a dead feed reading as "nobody carried the story",
which silently depresses every corroboration score.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pytest

from sci.config import ROOT, ConfigError, Settings, load_pipeline, load_sources
from sci.corroborate import corroborate_item
from sci.store import Item, Store

SOURCES_YAML = ROOT / "config" / "sources.yaml"
ECHO_IDS = {"phys_org", "sciencedaily", "medicalxpress"}
NEW_IDS = {"guardian_science", "bbc_science", "nyt_science", "stat_news",
           "new_scientist", "quanta"}

HEADLINE = "Ancient river deltas reveal how early Mars held liquid water for millennia"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Corroboration reads the store only; any live call is a bug."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("network access attempted in an offline test")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        sources=load_sources(SOURCES_YAML),
        pipeline=load_pipeline(ROOT / "config" / "pipeline.yaml"),
        root=tmp_path,
    )


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "sci.db") as db:
        yield db


def primary(title: str = HEADLINE) -> Item:
    return Item(id="science_news:1", source_id="science_news", title=title,
                link="https://example.org/science/1",
                published_at="2026-10-01T12:00:00+00:00")


def coverage(source_id: str, title: str = HEADLINE) -> Item:
    return Item(id=f"{source_id}:1", source_id=source_id, title=title,
                link=f"https://example.org/{source_id}/1",
                published_at="2026-10-02T08:00:00+00:00")


def record_fetch(store: Store, settings: Settings, *, failed: set[str]) -> None:
    """Write a fetch run exactly as pipeline.run_fetch shapes its detail."""
    sources = [
        {"source": s.id, "ok": s.id not in failed, "total": 0 if s.id in failed else 10,
         "new": 0, "error": "HTTP 404" if s.id in failed else None}
        for s in settings.sources.all
    ]
    run_id = store.start_run("fetch")
    store.end_run(run_id, ok=True, detail={
        "sources": sources, "new_total": 0, "failed": sorted(failed),
    })


# -- registry ---------------------------------------------------------------

def test_registry_lists_eight_independent_corroborators():
    sources = load_sources(SOURCES_YAML)
    independent = [s for s in sources.corroborators if s.independence >= 0.5]
    assert len(independent) == 8
    assert NEW_IDS <= {s.id for s in independent}


def test_press_release_republishers_stay_at_zero():
    sources = load_sources(SOURCES_YAML)
    echo = {s.id: s.independence for s in sources.corroborators if s.id in ECHO_IDS}
    assert echo == {sid: 0.0 for sid in ECHO_IDS}


def test_each_new_source_records_its_probe_date():
    """The url field is the probed url; the comment above it says when it worked."""
    text = SOURCES_YAML.read_text(encoding="utf-8")
    for sid in NEW_IDS:
        block = re.search(rf"- id: {sid}\n(.*?)(?:\n\s*\n|\Z)", text, re.S)
        assert block, f"{sid} missing from sources.yaml"
        assert re.search(r"# verified \d{4}-\d{2}-\d{2}: HTTP 200, \d+ items", block.group(1)), sid
        assert re.search(r"url: https://\S+", block.group(1)), sid


def test_widened_registry_still_rejects_a_duplicate_id(tmp_path: Path):
    duplicated = tmp_path / "sources.yaml"
    text = SOURCES_YAML.read_text(encoding="utf-8")
    extra = ("\n  - id: quanta\n    name: Quanta again\n"
             "    url: https://example.org/feed\n    independence: 1.0\n")
    duplicated.write_text(
        text.replace("\ncorroborators:\n", "\ncorroborators:\n" + extra, 1),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate id"):
        load_sources(duplicated)


# -- corroboration ----------------------------------------------------------

def test_three_echo_matches_are_not_corroboration(store: Store, settings: Settings):
    for sid in ECHO_IDS:
        store.upsert_item(coverage(sid))

    record = corroborate_item(primary(), store, settings)

    assert record["independent_count"] == 0
    assert record["echo_count"] == 3
    assert record["press_release_only"] is True


def test_two_new_newsrooms_reach_the_independent_route(store: Store, settings: Settings):
    store.upsert_item(coverage("guardian_science"))
    store.upsert_item(coverage("bbc_science"))

    record = corroborate_item(primary(), store, settings)

    assert record["independent_count"] == 2
    assert record["press_release_only"] is False


def test_failed_feed_is_unfetched_not_unmatched(store: Store, settings: Settings):
    record_fetch(store, settings, failed={"nyt_science"})

    record = corroborate_item(primary(), store, settings)

    assert record["independent_count"] == 0
    assert record["unfetched_sources"] == ["nyt_science"]
    assert record["feed_health"]["nyt_science"] == "failed"
    # Fetched and matched nothing: a real absence, and recorded as one.
    assert record["feed_health"]["guardian_science"] == "ok"
    assert "guardian_science" not in record["unfetched_sources"]


def test_latest_completed_fetch_wins(store: Store, settings: Settings):
    record_fetch(store, settings, failed={"nyt_science"})
    record_fetch(store, settings, failed={"quanta"})
    store.start_run("fetch")  # still running: no detail, must not mask the last one

    record = corroborate_item(primary(), store, settings)

    assert record["unfetched_sources"] == ["quanta"]
    assert record["feed_health"]["nyt_science"] == "ok"


def test_health_is_unknown_without_any_fetch_run(store: Store, settings: Settings):
    record = corroborate_item(primary(), store, settings)

    assert set(record["feed_health"].values()) == {"unknown"}
    assert record["unfetched_sources"] == []
