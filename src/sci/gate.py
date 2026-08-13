"""The publication gate.

One function decides whether an item is allowed to become a post, and it
records every reason it said no. Everything blocked here still gets a review
entry, because "we could not verify this" is useful editorial information —
it is often the more interesting story — and silently dropping it would make
the pipeline look more productive than it is.
"""

from __future__ import annotations

from typing import Any

from .config import Settings


def evaluate(binding: dict[str, Any], corroboration: dict[str, Any],
             claims: list[dict[str, Any]], hype: dict[str, Any],
             settings: Settings) -> dict[str, Any]:
    """Decide publishability and explain the decision."""
    require_source = bool(settings.pipeline.get("gate", "require_primary_or_independent"))
    min_independent = int(settings.pipeline.get("gate", "min_independent_corroborators"))
    max_hype = float(settings.pipeline.get("gate", "max_hype_score"))
    min_verified = int(settings.pipeline.get("gate", "min_verified_claims"))

    verified = [c for c in claims if c.get("status") == "verified"]
    hedged = [c for c in claims if c.get("status") == "hedged"]
    independent = int(corroboration.get("independent_count", 0))
    is_bound = binding.get("status") == "bound"
    hype_score = float(hype.get("score", 0))

    blockers: list[str] = []
    passed: list[str] = []

    if require_source:
        if is_bound:
            passed.append("primary study identified")
        elif independent >= min_independent:
            passed.append(f"{independent} independent outlets corroborate")
        else:
            blockers.append(
                f"no primary study bound and only {independent} independent "
                f"corroborator(s), need {min_independent}"
            )

    if hype_score <= max_hype:
        passed.append(f"hype score {hype_score:.0f} within limit {max_hype:.0f}")
    else:
        blockers.append(
            f"hype score {hype_score:.0f} exceeds limit {max_hype:.0f} "
            f"({', '.join(hype.get('flag_keys', []))})"
        )

    if len(verified) >= min_verified:
        passed.append(f"{len(verified)} verified claim(s)")
    else:
        blockers.append(f"only {len(verified)} verified claim(s), need {min_verified}")

    return {
        "passes": not blockers,
        "blockers": blockers,
        "passed": passed,
        "counts": {
            "verified": len(verified),
            "hedged": len(hedged),
            "unsupported": len(claims) - len(verified) - len(hedged),
            "independent": independent,
            "echo": int(corroboration.get("echo_count", 0)),
        },
        "hype_score": hype_score,
        "binding_status": binding.get("status"),
    }
