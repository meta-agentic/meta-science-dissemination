"""Deterministic text helpers shared by binding, corroboration and verification.

Nothing in here calls a model. Every similarity score the pipeline reports is
reproducible from these functions alone, which is what makes the evidence
ledger auditable after the fact.
"""

from __future__ import annotations

import html
import re
import unicodedata

# Words that carry no discriminative signal when matching a news headline to a
# paper title. Deliberately conservative: over-stripping destroys short titles.
_STOPWORDS = frozenset("""
a an the of in on at to for from with without by and or but as is are was were
be been being this that these those it its into over under new study finds
found show shows showed suggest suggests may might could can how why what when
first more most than then they them their we our you your no not can't
research researchers scientists reveals reveal report reports according
""".split())

_NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%|percent|per cent)?")
_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")


def strip_html(text: str) -> str:
    """Remove tags and resolve entities. Feed summaries arrive as HTML."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub(" ", text)).strip()


def normalize(text: str) -> str:
    """Casefold, strip accents, collapse whitespace."""
    if not text:
        return ""
    text = strip_html(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def tokens(text: str, *, keep_stopwords: bool = False) -> set[str]:
    """Content tokens of a string, as a set."""
    words = _WORD_RE.findall(normalize(text))
    if keep_stopwords:
        return set(words)
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    """Symmetric overlap. 0.0 when either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(small: set[str], large: set[str]) -> float:
    """Fraction of `small` present in `large`.

    Better than Jaccard when comparing a short headline against a long
    abstract, where Jaccard is punished by the length difference alone.
    """
    if not small:
        return 0.0
    return len(small & large) / len(small)


def similarity(a: str, b: str) -> float:
    """Blended headline similarity in [0, 1].

    Averages Jaccard with the best containment direction so that a short,
    punchy headline can still match a long, formal paper title.
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    best_containment = max(containment(ta, tb), containment(tb, ta))
    return round((jaccard(ta, tb) + best_containment) / 2, 4)


def extract_numbers(text: str) -> set[str]:
    """Every numeric literal in a text, normalized for comparison.

    Percentages are recorded twice — bare and suffixed — because a paper may
    write "42%" where the news copy writes "42 percent".
    """
    out: set[str] = set()
    for raw, suffix in _NUM_RE.findall(normalize(text)):
        value = raw.replace(",", "")
        value = value.rstrip("0").rstrip(".") if "." in value else value
        if not value:
            continue
        out.add(value)
        if suffix:
            out.add(f"{value}%")
    return out


def number_supported(number: str, *sources: str) -> bool:
    """True when a numeric literal appears in at least one source text.

    This is the whole of quantity verification. A model proposes the claim;
    this function, and only this function, decides whether the number is real.
    """
    target = number.strip().rstrip("%")
    if not target:
        return False
    pool: set[str] = set()
    for source in sources:
        pool |= extract_numbers(source)
    return target in {n.rstrip("%") for n in pool}


def slugify(text: str, *, max_length: int = 60) -> str:
    """Filesystem-safe slug for draft filenames."""
    base = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    if len(base) <= max_length:
        return base or "senza-titolo"
    return base[:max_length].rsplit("-", 1)[0] or "senza-titolo"
