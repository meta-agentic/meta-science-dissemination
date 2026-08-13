"""Primary-source binding: from a news headline to the paper it is about.

The Science news feed gives a headline, a one-line dek and the DOI *of the
news article* — never the DOI of the underlying study. So the link to the
primary source cannot be read off; it has to be inferred and then scored, and
the score has to be honest enough that a weak match is visibly weak.

Two open, key-free catalogues are queried: OpenAlex (which also returns an
abstract, the text every quantity claim is later checked against) and Crossref
as a fallback. Candidates are ranked on title overlap, date proximity and
venue plausibility. Anything below the weak threshold is reported as UNBOUND
rather than guessed at, because a confidently wrong paper is far worse for a
science post than an admitted gap.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings
from .store import Item
from .textutil import normalize, similarity, tokens

OPENALEX = "https://api.openalex.org/works"
CROSSREF = "https://api.crossref.org/works"

BOUND = "bound"
WEAK = "weak"
UNBOUND = "unbound"


@dataclass
class Candidate:
    """One possible primary source for a news item."""

    doi: str | None
    title: str
    abstract: str = ""
    venue: str = ""
    published: str = ""
    type: str = ""
    is_preprint: bool = False
    catalogue: str = "openalex"
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "doi": self.doi, "title": self.title, "venue": self.venue,
            "published": self.published, "type": self.type,
            "is_preprint": self.is_preprint, "catalogue": self.catalogue,
            "score": round(self.score, 4), "components": self.components,
            "abstract": self.abstract,
        }


def _get_json(url: str, *, user_agent: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        # A catalogue being unreachable must degrade to "no candidates",
        # not abort the run — the other catalogue may still answer.
        return {}


def _abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Rebuild the prose."""
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        positions.extend((spot, word) for spot in spots)
    positions.sort()
    return " ".join(word for _, word in positions)


def _query_terms(item: Item, *, limit: int = 12) -> str:
    """The discriminative words of a headline, longest first.

    Longer tokens carry more signal ("hippocampus" beats "brain"), and both
    catalogues behave better with a compact query than a full sentence.
    """
    words = sorted(tokens(item.text), key=len, reverse=True)
    return " ".join(words[:limit])


def _date_window(item: Item, settings: Settings) -> tuple[str, str]:
    lookback = int(settings.pipeline.get("binding", "lookback_days"))
    lookahead = int(settings.pipeline.get("binding", "lookahead_days"))
    anchor = datetime.now(timezone.utc)
    if item.published_at:
        try:
            anchor = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    return (
        (anchor - timedelta(days=lookback)).date().isoformat(),
        (anchor + timedelta(days=lookahead)).date().isoformat(),
    )


def _search_openalex(item: Item, settings: Settings, agent: str, timeout: int) -> list[Candidate]:
    start, end = _date_window(item, settings)
    per_page = int(settings.pipeline.get("binding", "max_candidates"))
    query = urllib.parse.urlencode({
        "search": _query_terms(item),
        "filter": f"from_publication_date:{start},to_publication_date:{end}",
        "per-page": min(per_page, 50),
        "select": "doi,title,abstract_inverted_index,publication_date,type,primary_location",
    })
    payload = _get_json(f"{OPENALEX}?{query}", user_agent=agent, timeout=timeout)

    out: list[Candidate] = []
    for work in payload.get("results", []) or []:
        location = work.get("primary_location") or {}
        venue = ((location.get("source") or {}).get("display_name")) or ""
        work_type = str(work.get("type") or "")
        out.append(Candidate(
            doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
            title=str(work.get("title") or ""),
            abstract=_abstract_from_inverted_index(work.get("abstract_inverted_index")),
            venue=venue,
            published=str(work.get("publication_date") or ""),
            type=work_type,
            is_preprint=work_type == "preprint" or "arxiv" in venue.lower()
            or "biorxiv" in venue.lower() or "medrxiv" in venue.lower(),
            catalogue="openalex",
        ))
    return out


def _search_crossref(item: Item, settings: Settings, agent: str, timeout: int) -> list[Candidate]:
    start, _ = _date_window(item, settings)
    rows = min(int(settings.pipeline.get("binding", "max_candidates")), 20)
    query = urllib.parse.urlencode({
        "query.bibliographic": _query_terms(item),
        "filter": f"from-pub-date:{start}",
        "rows": rows,
        "select": "DOI,title,abstract,container-title,issued,type",
    })
    payload = _get_json(f"{CROSSREF}?{query}", user_agent=agent, timeout=timeout)

    out: list[Candidate] = []
    for work in (payload.get("message") or {}).get("items", []) or []:
        titles = work.get("title") or []
        containers = work.get("container-title") or []
        parts = ((work.get("issued") or {}).get("date-parts") or [[]])[0]
        published = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts)) if parts else ""
        out.append(Candidate(
            doi=work.get("DOI"),
            title=str(titles[0]) if titles else "",
            abstract=str(work.get("abstract") or ""),
            venue=str(containers[0]) if containers else "",
            published=published,
            type=str(work.get("type") or ""),
            is_preprint=str(work.get("type") or "") == "posted-content",
            catalogue="crossref",
        ))
    return out


def _score(candidate: Candidate, item: Item, settings: Settings) -> Candidate:
    """Rank a candidate against the news item.

    Title overlap dominates. Date proximity and venue plausibility are
    tie-breakers only — they must never carry a semantically unrelated paper
    over the threshold on their own.
    """
    title_score = similarity(item.text, candidate.title)
    abstract_score = similarity(item.text, candidate.abstract) if candidate.abstract else 0.0

    proximity = 0.0
    if candidate.published and item.published_at:
        try:
            paper = datetime.fromisoformat(candidate.published).replace(tzinfo=timezone.utc)
            news = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            gap = abs((news - paper).days)
            proximity = max(0.0, 1.0 - gap / 30.0)
        except ValueError:
            proximity = 0.0

    boosts = [normalize(v) for v in settings.pipeline.get("binding", "venue_boost", [])]
    venue = normalize(candidate.venue)
    venue_score = 1.0 if any(b and b in venue for b in boosts) else 0.0

    components = {
        "title": round(title_score, 4),
        "abstract": round(abstract_score, 4),
        "proximity": round(proximity, 4),
        "venue": venue_score,
    }
    candidate.components = components
    candidate.score = round(
        0.55 * title_score + 0.25 * abstract_score + 0.12 * proximity + 0.08 * venue_score,
        4,
    )
    return candidate


def bind_item(item: Item, settings: Settings) -> dict[str, Any]:
    """Identify the primary source behind a news item.

    Returns an evidence record: the chosen candidate, its score and the
    runners-up, so a disputed binding can be re-examined without re-running
    the network calls.
    """
    agent = str(settings.pipeline.get("fetch", "user_agent"))
    timeout = int(settings.pipeline.get("fetch", "timeout_seconds"))
    bound_at = float(settings.pipeline.get("binding", "bound_threshold"))
    weak_at = float(settings.pipeline.get("binding", "weak_threshold"))

    candidates = _search_openalex(item, settings, agent, timeout)
    if not candidates:
        candidates = _search_crossref(item, settings, agent, timeout)

    scored = sorted(
        (_score(c, item, settings) for c in candidates if c.title),
        key=lambda c: c.score,
        reverse=True,
    )
    if not scored:
        return {
            "status": UNBOUND, "reason": "no candidate returned by any catalogue",
            "best": None, "runners_up": [], "abstract": "",
        }

    best = scored[0]
    if best.score >= bound_at:
        status = BOUND
    elif best.score >= weak_at:
        status = WEAK
    else:
        status = UNBOUND

    return {
        "status": status,
        "reason": f"best score {best.score} against bound={bound_at} weak={weak_at}",
        "best": best.as_dict() if status != UNBOUND else None,
        "runners_up": [c.as_dict() for c in scored[1:4]],
        # The abstract is the text against which every number is later
        # checked. An unbound item deliberately carries none, so its
        # quantity claims cannot be verified and will not be published.
        "abstract": best.abstract if status != UNBOUND else "",
    }
