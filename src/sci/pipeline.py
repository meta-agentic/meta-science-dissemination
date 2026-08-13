"""Stage orchestration.

Each stage is independently runnable and idempotent, so a failure in drafting
never forces a re-fetch, and a threshold change can be replayed over stored
items without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import bind, claims, corroborate, draft, gate, hype
from .config import Settings
from .llm import LLM, LLMUnavailable, from_settings
from .store import Analysis, Item, Store

Reporter = Callable[[str], None]


def _noop(_: str) -> None:
    return None


@dataclass
class StageResult:
    stage: str
    ok: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


def run_fetch(store: Store, settings: Settings, *, report: Reporter = _noop) -> StageResult:
    from .fetch import fetch_all

    run_id = store.start_run("fetch")
    reports = fetch_all(store, settings)

    for item in reports:
        status = "ok" if item.ok else "FAILED"
        suffix = f"{item.new} new / {item.total}" if item.ok else item.error
        report(f"  {item.source_id:<16} {status:<7} {suffix}")

    failures = [r for r in reports if not r.ok]
    detail = {
        "sources": [r.as_dict() for r in reports],
        "new_total": sum(r.new for r in reports),
        "failed": [r.source_id for r in failures],
    }
    # A partial fetch is still useful; only a total failure is a failed stage.
    ok = len(failures) < len(reports)
    store.end_run(run_id, ok=ok, detail=detail)
    return StageResult("fetch", ok=ok, detail=detail)


def analyse_item(item: Item, store: Store, settings: Settings, llm: LLM) -> Analysis:
    """Run the four verification layers over one item and gate the result."""
    binding = bind.bind_item(item, settings)
    corroboration = corroborate.corroborate_item(item, store, settings)
    extracted = claims.extract(item, binding, llm, settings)
    flags = hype.assess(item, binding, corroboration, settings)
    decision = gate.evaluate(binding, corroboration, extracted, flags, settings)

    return Analysis(
        binding=binding,
        corroboration=corroboration,
        claims=extracted,
        hype=flags,
        gate=decision,
    )


def run_analyse(store: Store, settings: Settings, *, limit: int | None = None,
                report: Reporter = _noop) -> StageResult:
    run_id = store.start_run("analyse")
    llm = from_settings(settings)

    if not llm.available:
        report("  ! claude CLI not found — claims cannot be extracted, "
               "so every item will be routed to review")

    processed = 0
    passed = 0
    for source in settings.sources.primary:
        for item in store.unanalysed(source.id, limit=limit):
            analysis = analyse_item(item, store, settings, llm)
            store.save_analysis(item.id, analysis)
            processed += 1
            passed += 1 if analysis.passes else 0

            verdict = "PASS" if analysis.passes else "review"
            report(f"  [{verdict:<6}] {item.title[:64]}")
            if not analysis.passes:
                for blocker in analysis.gate.get("blockers", []):
                    report(f"           - {blocker}")

    detail = {"processed": processed, "passed": passed, "blocked": processed - passed}
    store.end_run(run_id, ok=True, detail=detail)
    return StageResult("analyse", detail=detail)


def run_draft(store: Store, settings: Settings, *, limit: int | None = None,
              report: Reporter = _noop) -> StageResult:
    run_id = store.start_run("draft")
    llm = from_settings(settings)

    written = 0
    review = 0
    errors: list[str] = []

    for source in settings.sources.primary:
        for item, analysis in store.analysed_items(source.id):
            if store.has_draft(item.id):
                continue
            if limit is not None and written >= limit:
                break

            if not analysis.passes:
                path = draft.write_review(item, analysis, settings)
                review += 1
                report(f"  review  {path.name}")
                continue

            try:
                body = draft.generate(item, analysis, llm, settings)
            except LLMUnavailable as exc:
                errors.append(f"{item.id}: {exc}")
                report(f"  FAILED  {item.title[:56]} — {exc}")
                continue

            path = draft.write(item, analysis, body, settings)
            store.record_draft(item.id, path, len(body.split()))
            written += 1
            report(f"  draft   {path.relative_to(settings.drafts_dir)}")

    detail = {"written": written, "review": review, "errors": errors}
    store.end_run(run_id, ok=not errors, detail=detail)
    return StageResult("draft", ok=not errors, detail=detail)


def run_all(store: Store, settings: Settings, *, limit: int | None = None,
            report: Reporter = _noop) -> list[StageResult]:
    report("fetch")
    results = [run_fetch(store, settings, report=report)]
    report("analyse")
    results.append(run_analyse(store, settings, limit=limit, report=report))
    report("draft")
    results.append(run_draft(store, settings, limit=limit, report=report))
    return results
