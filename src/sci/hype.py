"""Rule-based limitation and overreach detection.

These are the recurring failure modes of popular science writing, and every
one of them is decidable from text without asking a model to be honest about
its own output. The checks run over the paper abstract where one is bound,
and over the headline and dek always — because the gap between what the paper
says and what the headline says is itself the most informative signal here.
"""

from __future__ import annotations

import re
from typing import Any

from .config import Settings
from .store import Item
from .textutil import normalize

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


def assess(item: Item, binding: dict[str, Any], corroboration: dict[str, Any],
           settings: Settings) -> dict[str, Any]:
    """Score an item's overreach risk and list the reasons.

    The score is a sum of configured penalties capped at 100. It is advisory
    for a human, and a gate input for the pipeline; the flags themselves are
    what get written into the draft's limitations section.
    """
    penalties: dict[str, int] = dict(settings.pipeline.get("hype", "penalties"))
    small_at = int(settings.pipeline.get("hype", "small_sample_threshold"))

    abstract = str(binding.get("abstract") or "")
    news_text = item.text
    corpus = f"{news_text} {abstract}"

    flags: list[dict[str, str]] = []

    def flag(key: str, detail: str) -> None:
        if key in penalties:
            flags.append({"flag": key, "detail": detail, "penalty": penalties[key]})

    if _ANIMAL.search(normalize(corpus)) and not _HUMAN.search(normalize(corpus)):
        flag("animal_only", "Evidence appears to come from a non-human model system only.")

    sizes = _sample_sizes(corpus)
    if sizes and min(sizes) < small_at:
        flag("small_sample", f"Smallest reported sample size is n={min(sizes)} (below {small_at}).")

    if abstract and not _CONTROL.search(normalize(abstract)):
        flag("no_control", "Abstract mentions no control group, randomisation or placebo.")

    # The signature press-release distortion: the study observes an
    # association, the headline asserts a cause.
    if _CAUSAL.search(normalize(news_text)) and abstract and _CORRELATIONAL.search(normalize(abstract)):
        if not _CAUSAL.search(normalize(abstract)):
            flag(
                "causal_overreach",
                "Headline uses causal language where the abstract reports an association.",
            )

    best = binding.get("best") or {}
    if best.get("is_preprint"):
        flag("preprint", "Primary source is a preprint and may not be peer reviewed.")

    if corroboration.get("press_release_only"):
        flag(
            "press_release_only",
            "Only press-release republishers carry this story; no independent reporting found.",
        )

    if binding.get("status") != "bound":
        flag(
            "unbound_primary",
            f"Primary study could not be identified with confidence (status: {binding.get('status')}).",
        )

    score = min(100, sum(int(f["penalty"]) for f in flags))
    return {
        "score": score,
        "flags": flags,
        "flag_keys": [f["flag"] for f in flags],
        "sample_sizes": sizes,
    }
