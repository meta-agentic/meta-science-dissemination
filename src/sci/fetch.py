"""Feed ingestion.

Only publisher-provided RSS is read. The pipeline never authenticates to
science.org and never requests article bodies: full text is paywalled, and
scraping it with subscriber credentials would breach the AAAS terms of use and
put the account at risk. Titles, deks, DOIs and dates are enough to decide
what deserves a human read.
"""

from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Settings, Source
from .store import Item, Store, utcnow
from .textutil import strip_html


class FetchError(RuntimeError):
    """A feed could not be retrieved or parsed."""


@dataclass
class FetchReport:
    source_id: str
    ok: bool
    total: int = 0
    new: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_id, "ok": self.ok,
            "total": self.total, "new": self.new, "error": self.error,
        }


def _http_get(url: str, *, user_agent: str, timeout: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise FetchError(f"HTTP {response.status} for {url}")
            return response.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"network error for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError(f"timeout for {url}") from exc


def _entry_id(source: Source, entry: Any) -> str:
    """Stable identity for an entry.

    Prefers the DOI, then the canonical link, and only then a content hash,
    so that a publisher re-issuing an item under a new GUID does not produce
    a duplicate draft.
    """
    doi = (entry.get("prism_doi") or "").strip()
    if doi:
        seed = f"doi:{doi.lower()}"
    elif entry.get("link"):
        seed = f"url:{entry['link'].split('?')[0].lower()}"
    else:
        seed = f"txt:{source.id}:{entry.get('title', '')}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{source.id}:{digest}"


def _published(entry: Any) -> str | None:
    """Best available publication timestamp, as ISO-8601 UTC."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            pass
    for key in ("prism_coverdate", "published", "updated"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


def parse_feed(payload: bytes, source: Source) -> list[Item]:
    """Turn raw feed bytes into normalized items."""
    try:
        import feedparser
    except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
        raise FetchError("feedparser is required; run: uv sync") from exc

    parsed = feedparser.parse(payload)
    if parsed.bozo and not parsed.entries:
        raise FetchError(f"unparseable feed for {source.id}: {parsed.bozo_exception}")

    items: list[Item] = []
    for entry in parsed.entries:
        title = strip_html(entry.get("title", "")).strip()
        link = (entry.get("link") or entry.get("prism_url") or "").strip()
        if not title or not link:
            # A feed entry without a headline or a destination is unusable;
            # skipping is correct and worth counting, not worth crashing over.
            continue
        items.append(
            Item(
                id=_entry_id(source, entry),
                source_id=source.id,
                title=title,
                summary=strip_html(entry.get("summary", "")).strip(),
                link=link,
                doi=(entry.get("prism_doi") or "").strip() or None,
                author=(entry.get("author") or "").strip() or None,
                published_at=_published(entry),
                fetched_at=utcnow(),
                raw={"source_name": source.name, "lang": source.lang},
            )
        )
    return items


def fetch_source(source: Source, store: Store, settings: Settings) -> FetchReport:
    """Fetch and persist one feed. Failures are reported, never raised upward."""
    user_agent = str(settings.pipeline.get("fetch", "user_agent"))
    timeout = int(settings.pipeline.get("fetch", "timeout_seconds"))
    try:
        payload = _http_get(source.url, user_agent=user_agent, timeout=timeout)
        items = parse_feed(payload, source)
    except FetchError as exc:
        return FetchReport(source_id=source.id, ok=False, error=str(exc))

    new = sum(1 for item in items if store.upsert_item(item))
    return FetchReport(source_id=source.id, ok=True, total=len(items), new=new)


def fetch_all(store: Store, settings: Settings, *, primary_only: bool = False) -> list[FetchReport]:
    """Fetch every configured feed, pausing between hosts to stay polite."""
    delay = float(settings.pipeline.get("fetch", "delay_seconds", 1.0))
    targets = settings.sources.primary if primary_only else settings.sources.all

    reports: list[FetchReport] = []
    for index, source in enumerate(targets):
        if index:
            time.sleep(delay)
        reports.append(fetch_source(source, store, settings))
    return reports
