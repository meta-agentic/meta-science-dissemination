"""Claim extraction and verification.

The division of labour is the point. A model reads the headline, the dek and
the bound abstract and *proposes* atomic factual claims — it is good at that
and bad at knowing when it is wrong. Python then *verifies* each claim against
the source texts: numbers must literally appear, causal claims must be backed
by causal language in the abstract rather than hedged association, and every
remaining claim must overlap its stated evidence.

A model never marks its own claim verified. Only unverified claims are
excluded from the draft, so a hallucinated statistic cannot reach a post.
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .llm import LLM, LLMUnavailable
from .store import Item
from .textutil import containment, normalize, number_supported, tokens
from .hype import _CAUSAL, _CORRELATIONAL

VERIFIED = "verified"
UNSUPPORTED = "unsupported"
HEDGED = "hedged"

_SYSTEM = (
    "You extract atomic factual claims from science journalism for a "
    "fact-checking pipeline. You never invent facts, never round numbers, and "
    "never add context that is not present in the supplied text. You return "
    "JSON only."
)

_PROMPT = """Extract the distinct factual claims made by this science news item.

HEADLINE: {title}
STANDFIRST: {summary}

ABSTRACT OF THE UNDERLYING STUDY ({binding_status}):
{abstract}

Rules:
- One claim per object. Do not merge two facts into one claim.
- Copy numbers exactly as written. Never round, convert or infer a number.
- If a claim appears only in the headline and not in the abstract, still
  extract it — the pipeline needs to detect exactly that discrepancy.
- At most {max_claims} claims.

Return a JSON array. Each element:
{{
  "text": "the claim, in one plain English sentence",
  "type": "quantity" | "causal" | "generalisation" | "attribution",
  "numbers": ["every numeric literal in the claim, as written, no units"],
  "evidence": "the exact sentence fragment from the supplied text it rests on"
}}

Return only the JSON array."""


def _verify_quantity(claim: dict[str, Any], sources: list[str]) -> tuple[str, str]:
    numbers = [str(n) for n in (claim.get("numbers") or []) if str(n).strip()]
    if not numbers:
        return _verify_textual(claim, sources)

    missing = [n for n in numbers if not number_supported(n, *sources)]
    if missing:
        return UNSUPPORTED, f"number(s) not found in any source text: {', '.join(missing)}"
    return VERIFIED, f"all numeric literals present in source text: {', '.join(numbers)}"


def _verify_causal(claim: dict[str, Any], abstract: str, sources: list[str]) -> tuple[str, str]:
    status, reason = _verify_textual(claim, sources)
    if status != VERIFIED:
        return status, reason

    if not abstract:
        return HEDGED, "no abstract available to confirm a causal reading"

    normalised = normalize(abstract)
    if _CAUSAL.search(normalised):
        return VERIFIED, "abstract uses causal language"
    if _CORRELATIONAL.search(normalised):
        return HEDGED, "abstract reports an association, not a cause"
    return HEDGED, "abstract does not state a causal relationship explicitly"


def _verify_textual(claim: dict[str, Any], sources: list[str]) -> tuple[str, str]:
    """Whether the claim's content words actually occur in the sources."""
    claim_tokens = tokens(str(claim.get("text", "")))
    if not claim_tokens:
        return UNSUPPORTED, "claim text is empty"

    pool: set[str] = set()
    for source in sources:
        pool |= tokens(source)

    overlap = containment(claim_tokens, pool)
    if overlap >= 0.6:
        return VERIFIED, f"{overlap:.0%} of claim terms present in source text"
    return UNSUPPORTED, f"only {overlap:.0%} of claim terms present in source text"


def verify(claim: dict[str, Any], item: Item, abstract: str) -> dict[str, Any]:
    """Attach a verdict and a reason to one proposed claim."""
    sources = [t for t in (abstract, item.text) if t]
    kind = str(claim.get("type") or "generalisation").lower()

    if kind == "quantity":
        status, reason = _verify_quantity(claim, sources)
    elif kind == "causal":
        status, reason = _verify_causal(claim, abstract, sources)
    else:
        status, reason = _verify_textual(claim, sources)

    return {
        "text": str(claim.get("text", "")).strip(),
        "type": kind,
        "numbers": [str(n) for n in (claim.get("numbers") or [])],
        "evidence": str(claim.get("evidence", "")).strip(),
        "status": status,
        "reason": reason,
        # Recorded so an audit can tell a claim checked against a real
        # abstract from one checked against a headline alone.
        "checked_against": "abstract+news" if abstract else "news_only",
    }


def extract(item: Item, binding: dict[str, Any], llm: LLM,
            settings: Settings) -> list[dict[str, Any]]:
    """Propose claims with the model, then verify each one in code."""
    max_claims = int(settings.pipeline.get("claims", "max_claims_per_item"))
    abstract = str(binding.get("abstract") or "")

    prompt = _PROMPT.format(
        title=item.title,
        summary=item.summary or "(none provided)",
        binding_status=binding.get("status", "unbound"),
        abstract=abstract or "(the underlying study could not be identified)",
        max_claims=max_claims,
    )

    try:
        proposed = llm.complete_json(prompt, system=_SYSTEM)
    except (LLMUnavailable, ValueError) as exc:
        # No claims is a correct, safe outcome: the gate requires at least
        # one verified claim, so the item routes to review instead of
        # producing a post backed by nothing.
        return [{
            "text": "", "type": "error", "numbers": [], "evidence": "",
            "status": UNSUPPORTED, "reason": f"claim extraction failed: {exc}",
            "checked_against": "none",
        }]

    if not isinstance(proposed, list):
        return [{
            "text": "", "type": "error", "numbers": [], "evidence": "",
            "status": UNSUPPORTED, "reason": "model did not return a JSON array",
            "checked_against": "none",
        }]

    return [
        verify(claim, item, abstract)
        for claim in proposed[:max_claims]
        if isinstance(claim, dict)
    ]
