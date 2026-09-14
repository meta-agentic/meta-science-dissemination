"""Rule-based limitation and overreach detection.

These are the recurring failure modes of popular science writing, and every
one of them is decidable from text without asking a model to be honest about
its own output. The checks run over the paper abstract where one is bound,
and over the headline and dek always — because the gap between what the paper
says and what the headline says is itself the most informative signal here.

Every rule returns one of three outcomes (INV-2): it `fired`, it ran and came
back `clear`, or it was `not_assessable` because its input was missing. The
third is the one that matters. A rule that could not run is not evidence of
innocence, and collapsing it into "no flag" is what let the spike report a
hype score of 0 for an item nothing had been checked against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .bind import Binding, Unbound
from .config import Settings
from .store import Item
from .textutil import normalize

# Rule outcomes.
FIRED = "fired"
CLEAR = "clear"
NOT_ASSESSABLE = "not_assessable"

_NO_ABSTRACT = "No abstract was available, so this check could not run."

# Species terms that indicate a non-human model system.
_ANIMAL = re.compile(
    r"\b(mice|mouse|rats?|murine|zebrafish|drosophila|c\.? elegans|macaques?|"
    r"primates?|rodents?|canine|porcine|in vitro|cell lines?|organoids?)\b"
)
_HUMAN = re.compile(
    r"\b(humans?|patients?|participants?|volunteers?|men|women|children|"
    r"adults?|cohort|clinical trial)\b"
)
_CONTROL = re.compile(
    r"\b(control(s|led)?|randomi[sz]ed|placebo|double.blind|single.blind|"
    r"sham|comparison group|baseline group)\b"
)
# Causal language, as used by a headline.
_CAUSAL = re.compile(
    r"\b(causes?|caused|causing|leads? to|led to|triggers?|triggered|"
    r"makes?|prevents?|cures?|reverses?|drives?)\b"
)
# Hedged, correlational language, as used by a careful abstract.
_CORRELATIONAL = re.compile(
    r"\b(associat(ed|ion)|correlat(ed|ion)|linked to|relationship between|"
    r"predict(s|ed|or)?|observational|cross.sectional)\b"
)
_SAMPLE = re.compile(r"\b(?:n\s*=\s*|sample of |cohort of |enrolled )(\d[\d,]*)\b")


def _sample_sizes(text: str) -> list[int]:
    return [int(m.replace(",", "")) for m in _SAMPLE.findall(normalize(text))]


@dataclass(frozen=True)
class _Context:
    """Everything the rules are allowed to look at."""

    news_text: str
    abstract: str
    corpus: str
    sample_sizes: list[int]
    small_sample_threshold: int
    best: dict[str, Any]
    corroboration: dict[str, Any]
    binding_status: str


def _animal_only(ctx: _Context) -> str | None:
    corpus = normalize(ctx.corpus)
    if _ANIMAL.search(corpus) and not _HUMAN.search(corpus):
        return "Evidence appears to come from a non-human model system only."
    return None


def _small_sample(ctx: _Context) -> str | None:
    if ctx.sample_sizes and min(ctx.sample_sizes) < ctx.small_sample_threshold:
        return (
            f"Smallest reported sample size is n={min(ctx.sample_sizes)} "
            f"(below {ctx.small_sample_threshold})."
        )
    return None


def _no_control(ctx: _Context) -> str | None:
    if not _CONTROL.search(normalize(ctx.abstract)):
        return "Abstract mentions no control group, randomisation or placebo."
    return None


def _causal_overreach(ctx: _Context) -> str | None:
    # The signature press-release distortion: the study observes an
    # association, the headline asserts a cause.
    abstract = normalize(ctx.abstract)
    if (_CAUSAL.search(normalize(ctx.news_text))
            and _CORRELATIONAL.search(abstract)
            and not _CAUSAL.search(abstract)):
        return "Headline uses causal language where the abstract reports an association."
    return None


def _preprint(ctx: _Context) -> str | None:
    if ctx.best.get("is_preprint"):
        return "Primary source is a preprint and may not be peer reviewed."
    return None


def _press_release_only(ctx: _Context) -> str | None:
    if ctx.corroboration.get("press_release_only"):
        return (
            "Only press-release republishers carry this story; "
            "no independent reporting found."
        )
    return None


def _unbound_primary(ctx: _Context) -> str | None:
    if ctx.binding_status != "bound":
        return (
            "Primary study could not be identified with confidence "
            f"(status: {ctx.binding_status})."
        )
    return None


# Which rules need an abstract to mean anything. Decided in SCI-13, not at
# call time: a `clear` from news text alone is not a clean bill of health for
# a check whose subject is the study rather than the headline. An unbound item
# therefore reaches 3 of 7 — evidence_completeness 0.43 — and no higher.
_RULES: tuple[tuple[str, bool, Callable[[_Context], str | None]], ...] = (
    ("animal_only", True, _animal_only),
    ("small_sample", True, _small_sample),
    ("no_control", True, _no_control),
    ("causal_overreach", True, _causal_overreach),
    ("preprint", False, _preprint),
    ("press_release_only", False, _press_release_only),
    ("unbound_primary", False, _unbound_primary),
)

TOTAL_RULES = len(_RULES)


def assess(item: Item, binding: Binding, corroboration: dict[str, Any],
           settings: Settings) -> dict[str, Any]:
    """Score an item's overreach risk and report what could and could not run.

    The score is the sum of the penalties of the rules that *fired*, capped at
    100. It is advisory for a human, and a gate input for the pipeline; the
    fired outcomes are what get written into the draft's limitations section.

    `evidence_completeness` is reported beside the score and answers "zero out
    of how many?". A score of 0 from three assessable rules and a score of 0
    from seven are opposite situations; they must not share a number, and with
    this record they no longer do.
    """
    penalties: dict[str, int] = dict(settings.pipeline.get("hype", "penalties"))
    small_at = int(settings.pipeline.get("hype", "small_sample_threshold"))

    # An unbound binding has no `abstract` attribute at all, so the branch is
    # forced here rather than silently yielding an empty string (ADR-001).
    abstract = "" if isinstance(binding, Unbound) else binding.abstract
    news_text = item.text
    corpus = f"{news_text} {abstract}"
    sizes = _sample_sizes(corpus)

    ctx = _Context(
        news_text=news_text,
        abstract=abstract,
        corpus=corpus,
        sample_sizes=sizes,
        small_sample_threshold=small_at,
        best={} if isinstance(binding, Unbound) else binding.candidate.as_dict(),
        corroboration=corroboration,
        binding_status=binding.status,
    )

    has_abstract = bool(abstract.strip())
    outcomes: list[dict[str, Any]] = []
    for key, requires_abstract, rule in _RULES:
        # The penalty is a published lever; a rule missing from the penalty
        # table still runs and still reports, it just costs nothing. The rule
        # suite fixes `total_rules`, so completeness cannot be moved by
        # editing the config.
        if requires_abstract and not has_abstract:
            outcomes.append({"flag": key, "status": NOT_ASSESSABLE,
                             "detail": _NO_ABSTRACT, "penalty": 0})
            continue
        detail = rule(ctx)
        if detail is None:
            outcomes.append({"flag": key, "status": CLEAR, "detail": "", "penalty": 0})
        else:
            outcomes.append({"flag": key, "status": FIRED, "detail": detail,
                             "penalty": int(penalties.get(key, 0))})

    fired = [o for o in outcomes if o["status"] == FIRED]
    assessable = [o for o in outcomes if o["status"] != NOT_ASSESSABLE]
    score = min(100, sum(int(o["penalty"]) for o in fired))
    return {
        "score": score,
        # One entry per rule, whatever its outcome. A consumer that wants the
        # stated limitations must select `status == FIRED` (see fired_flags);
        # the rest is the record of what the suite was able to check at all.
        "flags": outcomes,
        "flag_keys": [o["flag"] for o in fired],
        "assessable_count": len(assessable),
        "total_rules": TOTAL_RULES,
        "evidence_completeness": len(assessable) / TOTAL_RULES,
        "sample_sizes": sizes,
    }


def fired_flags(hype: dict[str, Any]) -> list[dict[str, Any]]:
    """The rules that actually fired, as limitation records.

    Selecting here rather than at each call site is what keeps a rule that
    could not run from being stated as a finding about the study.
    """
    return [o for o in hype.get("flags", []) if o.get("status") == FIRED]
