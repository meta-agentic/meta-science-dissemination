"""Cross-source corroboration.

Counting outlets is the easy part and the wrong part. Phys.org, ScienceDaily
and MedicalXpress overwhelmingly republish institutional press releases, so
three "hits" there can be one press office speaking three times. Each source
therefore carries an independence weight from sources.yaml, and matches are
split into `independent` confirmation and `echo`. Only the former counts
toward the publication gate; the latter is what raises the press-release-only
flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .store import Item, Store
from .textutil import similarity


def _parsed_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _within_window(item: Item, other: Item, window_days: int) -> bool:
    """Whether two items are close enough in time to be the same story.

    An item with no usable date is kept rather than dropped: excluding it
    would understate corroboration, and overstating it is the failure mode
    that actually matters here.
    """
    left, right = _parsed_date(item.published_at), _parsed_date(other.published_at)
    if left is None or right is None:
        return True
    return abs(right - left) <= timedelta(days=window_days)


def corroborate_item(item: Item, store: Store, settings: Settings) -> dict[str, Any]:
    """Find other outlets carrying the same story, weighted by independence."""
    window = int(settings.pipeline.get("corroboration", "window_days"))
    threshold = float(settings.pipeline.get("corroboration", "match_threshold"))

    independent: list[dict[str, Any]] = []
    echo: list[dict[str, Any]] = []

    for source in settings.sources.corroborators:
        best_match: tuple[float, Item] | None = None
        for other in store.items(source_id=source.id):
            if not _within_window(item, other, window):
                continue
            score = similarity(item.text, other.text)
            if score >= threshold and (best_match is None or score > best_match[0]):
                best_match = (score, other)

        if best_match is None:
            continue

        score, matched = best_match
        record = {
            "source": source.id,
            "name": source.name,
            "independence": source.independence,
            "title": matched.title,
            "link": matched.link,
            "similarity": round(score, 4),
            "published_at": matched.published_at,
        }
        (independent if source.is_independent else echo).append(record)

    independent.sort(key=lambda r: r["similarity"], reverse=True)
    echo.sort(key=lambda r: r["similarity"], reverse=True)

    return {
        "independent": independent,
        "echo": echo,
        "independent_count": len(independent),
        "echo_count": len(echo),
        # True when the only other coverage is press-release republication:
        # the story is travelling, but nobody has checked it.
        "press_release_only": not independent and bool(echo),
        "searched_sources": [s.id for s in settings.sources.corroborators],
    }
