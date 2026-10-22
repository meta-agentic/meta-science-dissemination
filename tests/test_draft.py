"""Tests for what the drafting prompt may and may not contain.

A rejected claim is withheld from the model by omission, not by an
instruction to ignore it: the prompt carries only how many were withheld.
The human-facing ledger keeps the full text of every claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sci import draft
from sci.config import ROOT, Settings, load_pipeline, load_sources
from sci.store import Analysis, Item

VERIFIED = "Il caffe' riduce del 30% il rischio osservato nel campione."
HEDGED = "Il consumo di caffe' e' associato a una vita piu' lunga."
UNSUPPORTED = "Il caffe' allunga la vita dell'87% dei bevitori."
UNKNOWN_STATUS = "Una cifra del 55% citata solo nel comunicato."


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        sources=load_sources(ROOT / "config" / "sources.yaml"),
        pipeline=load_pipeline(ROOT / "config" / "pipeline.yaml"),
        root=tmp_path,
    )


@pytest.fixture
def item() -> Item:
    return Item(id="t:1", source_id="science_news", title="Coffee and longevity",
                summary="A cohort study.", link="https://example.org/a",
                published_at="2026-10-01T00:00:00+00:00")


@pytest.fixture
def analysis() -> Analysis:
    return Analysis(
        binding={"status": "bound", "best": {"doi": "10.1038/x", "title": "A study",
                                             "venue": "Nature", "score": 0.5}},
        claims=[
            {"text": VERIFIED, "status": "verified", "reason": "nell'abstract"},
            {"text": HEDGED, "status": "hedged", "reason": "solo associazione"},
            {"text": UNSUPPORTED, "status": "unsupported", "reason": "assente"},
            {"text": UNKNOWN_STATUS, "status": "contradicted", "reason": "smentita"},
        ],
        gate={"passes": False, "blockers": ["prova insufficiente"]},
    )


class TestPromptWithholdsRejectedClaims:
    def test_a_rejected_statistic_never_reaches_the_prompt(self, item, analysis, settings):
        prompt = draft.build_prompt(item, analysis, settings)

        assert "87" not in prompt
        assert "30" in prompt
        assert VERIFIED in prompt

    def test_any_status_outside_verified_and_hedged_is_withheld(
        self, item, analysis, settings
    ):
        prompt = draft.build_prompt(item, analysis, settings)

        assert UNSUPPORTED not in prompt
        assert UNKNOWN_STATUS not in prompt
        assert "55" not in prompt

    def test_hedged_claims_stay_in_the_prompt(self, item, analysis, settings):
        assert HEDGED in draft.build_prompt(item, analysis, settings)

    def test_the_prompt_states_how_many_claims_were_withheld(
        self, item, analysis, settings
    ):
        prompt = draft.build_prompt(item, analysis, settings)

        assert "AFFERMAZIONI ESCLUSE: 2 affermazioni" in prompt

    def test_generate_sends_the_same_prompt_to_the_model(self, item, analysis, settings):
        sent: list[str] = []

        class FakeLLM:
            def complete(self, prompt: str, *, system: str | None = None) -> str:
                sent.append(prompt)
                return " corpo "

        assert draft.generate(item, analysis, FakeLLM(), settings) == "corpo"
        assert sent == [draft.build_prompt(item, analysis, settings)]
        assert "87" not in sent[0]

    def test_a_non_draftable_status_cannot_be_formatted_for_the_prompt(self, analysis):
        with pytest.raises(ValueError):
            draft._format_claims(analysis.claims, "unsupported")


class TestHumanLedgerKeepsRejectedText:
    def test_the_draft_carries_every_claim_including_rejected(
        self, item, analysis, settings
    ):
        text = draft.write(item, analysis, "corpo", settings).read_text(encoding="utf-8")

        assert "claims_total: 4" in text
        for claim in (VERIFIED, HEDGED, UNSUPPORTED, UNKNOWN_STATUS):
            assert claim in text
        assert "**[NON CONFERMATA]** " + UNSUPPORTED in text

    def test_the_review_note_carries_every_claim_including_rejected(
        self, item, analysis, settings
    ):
        text = draft.write_review(item, analysis, settings).read_text(encoding="utf-8")

        for claim in (VERIFIED, HEDGED, UNSUPPORTED, UNKNOWN_STATUS):
            assert claim in text
        assert "**[NON CONFERMATA]** " + UNSUPPORTED in text
