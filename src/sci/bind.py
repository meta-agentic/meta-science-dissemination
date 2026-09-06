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
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

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


@dataclass(frozen=True)
class Rejection:
    """A candidate discarded before scoring, and the rule that discarded it.

    Retained because "we found the paper but it was a preprint" and "we found
    nothing at all" are different editorial situations, and a ledger that
    cannot tell them apart cannot explain itself later.
    """

    doi: str | None
    title: str
    rule: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"doi": self.doi, "title": self.title[:200],
                "rule": self.rule, "reason": self.reason}


@dataclass(frozen=True)
class Bound:
    """A positively identified primary source, carrying its abstract.

    The abstract is non-empty by construction. That is the whole point of the
    type: binding exists to obtain independent text to verify claims against,
    so a binding without that text is not a weaker binding, it is not one.
    """

    status: ClassVar[str] = BOUND

    candidate: Candidate
    abstract: str
    score: float
    runners_up: tuple[Candidate, ...] = ()
    rejected: tuple[Rejection, ...] = ()

    def __post_init__(self) -> None:
        if not (self.abstract or "").strip():
            raise ValueError(
                f"{type(self).__name__} requires a non-empty abstract; "
                "a binding with no verification substrate must be Unbound"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "best": self.candidate.as_dict(),
            "abstract": self.abstract,
            "score": round(self.score, 4),
            "runners_up": [c.as_dict() for c in self.runners_up],
            "rejected": [r.as_dict() for r in self.rejected],
        }


@dataclass(frozen=True)
class Weak(Bound):
    """Identified, but below the confidence threshold.

    Still carries an abstract, so claims can still be verified against real
    primary text and INV-1 holds. The lower score travels with the draft.
    """

    status: ClassVar[str] = WEAK


@dataclass(frozen=True)
class Unbound:
    """No primary source could be identified.

    Deliberately has **no** `abstract` attribute. Verification code that reaches
    for `binding.abstract` on one of these raises AttributeError during
    development, rather than silently receiving an empty string and comparing a
    claim against nothing — which is exactly how the spike published four
    self-verified items.
    """

    status: ClassVar[str] = UNBOUND

    reason: str
    rejected: tuple[Rejection, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "best": None,
            "runners_up": [],
            "rejected": [r.as_dict() for r in self.rejected],
        }


Binding = Bound | Weak | Unbound


def is_valid_candidate(candidate: Candidate, item: Item,
                       settings: Settings) -> tuple[bool, str, str]:
    """Whether a candidate could be the study this news item is about.

    Checked **before** scoring, and a failure discards the candidate rather
    than down-weighting it: a news article scoring 0.55 against itself is
    arithmetically correct and editorially worthless, and no tie-breaker should
    be able to rescue it.

    Returns (ok, rule, reason).
    """
    binding_cfg = settings.pipeline.section("binding")

    # V1 — identity. An item cannot be its own primary source.
    own = (item.doi or "").strip().lower()
    cand = (candidate.doi or "").strip().lower()
    if own and cand and own == cand:
        return False, "V1", "candidate is the news item itself"

    # V2 — editorial venue. Publishers index their own journalism as works;
    # this is the container the spike matched against, four times out of four.
    venue = normalize(candidate.venue)
    for editorial in binding_cfg.get("editorial_venues", []):
        if venue and normalize(editorial) in venue:
            return False, "V2", f"venue '{candidate.venue}' publishes journalism, not studies"

    # V3 — DOI shape. News DOIs carry an opaque suffix where research carries
    # a structured one.
    for pattern in binding_cfg.get("news_doi_patterns", []):
        if cand and re.match(pattern, cand):
            return False, "V3", f"DOI '{candidate.doi}' matches a news-content pattern"

    # V4 — work type. Editorials, letters and errata are not the study.
    allowed = {str(x).lower() for x in binding_cfg.get("research_types", [])}
    kind = (candidate.type or "").strip().lower()
    if allowed and kind and kind not in allowed:
        return False, "V4", f"work type '{candidate.type}' is not research"

    # V5 — the invariant. Without an abstract there is nothing to verify
    # against, however well the title matches.
    if not (candidate.abstract or "").strip():
        return False, "V5", "no abstract: cannot serve as a verification substrate"

    return True, "", "valid"


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


def bind_item(item: Item, settings: Settings) -> Binding:
    """Identify the primary source behind a news item, or say honestly that we
    could not.

    Validity is checked before scoring (see `is_valid_candidate`), so an
    invalid candidate cannot be rescued by date proximity or venue prestige.
    Every rejection is retained on the result: the ledger must be able to
    distinguish "found a preprint" from "found nothing".
    """
    agent = str(settings.pipeline.get("fetch", "user_agent"))
    timeout = int(settings.pipeline.get("fetch", "timeout_seconds"))
    bound_at = float(settings.pipeline.get("binding", "bound_threshold"))
    weak_at = float(settings.pipeline.get("binding", "weak_threshold"))

    candidates = _search_openalex(item, settings, agent, timeout)
    if not candidates:
        candidates = _search_crossref(item, settings, agent, timeout)

    if not candidates:
        return Unbound(reason="no candidate returned by any catalogue")

    valid: list[Candidate] = []
    rejected: list[Rejection] = []
    for candidate in candidates:
        if not candidate.title:
            continue
        ok, rule, reason = is_valid_candidate(candidate, item, settings)
        if ok:
            valid.append(candidate)
        else:
            rejected.append(Rejection(doi=candidate.doi, title=candidate.title,
                                      rule=rule, reason=reason))

    rejections = tuple(rejected)
    if not valid:
        fired = ", ".join(sorted({r.rule for r in rejections})) or "none"
        return Unbound(
            reason=f"all {len(rejections)} candidate(s) failed validity (rules: {fired})",
            rejected=rejections,
        )

    scored = sorted((_score(c, item, settings) for c in valid),
                    key=lambda c: c.score, reverse=True)
    best, runners_up = scored[0], tuple(scored[1:4])

    if best.score >= bound_at:
        return Bound(candidate=best, abstract=best.abstract, score=best.score,
                     runners_up=runners_up, rejected=rejections)
    if best.score >= weak_at:
        return Weak(candidate=best, abstract=best.abstract, score=best.score,
                    runners_up=runners_up, rejected=rejections)

    return Unbound(
        reason=(f"best valid candidate scored {best.score}, below the weak "
                f"threshold {weak_at}"),
        rejected=rejections,
    )
