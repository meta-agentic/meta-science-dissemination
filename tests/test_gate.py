"""Tests for the publication gate and the independence weighting.

The failure this file exists to prevent: three press-release republishers
being counted as three independent confirmations, so an unverified story
sails through the gate looking well sourced.
"""

from __future__ import annotations

import pytest

from sci import gate
from sci.bind import Bound, Candidate, Unbound
from sci.config import ROOT, Source, load_pipeline


@pytest.fixture
def settings():
    class Stub:
        pipeline = load_pipeline(ROOT / "config" / "pipeline.yaml")

    return Stub()


def verified(n: int) -> list[dict]:
    return [{"text": f"claim {i}", "status": "verified"} for i in range(n)]


def bound() -> Bound:
    """A real binding — carries an abstract by construction (ADR-001)."""
    return Bound(
        candidate=Candidate(doi="10.1038/x", title="A study",
                            abstract="We report a finding."),
        abstract="We report a finding.",
        score=0.51,
    )


def unbound() -> Unbound:
    """No primary source — and therefore no abstract attribute at all."""
    return Unbound(reason="all candidates failed validity")


class TestIndependence:
    def test_press_release_republisher_is_not_independent(self):
        assert not Source(id="phys", name="Phys.org", url="x", independence=0.0).is_independent

    def test_original_newsroom_is_independent(self):
        assert Source(id="nature", name="Nature", url="x", independence=1.0).is_independent

    def test_half_weight_source_counts_as_independent(self):
        assert Source(id="mixed", name="Mixed", url="x", independence=0.5).is_independent


class TestGate:
    def test_bound_paper_with_verified_claim_passes(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 0, "echo_count": 0},
            claims=verified(2),
            hype={"score": 10, "flag_keys": []},
            settings=settings,
        )
        assert decision["passes"], decision["blockers"]

    def test_unbound_with_only_echo_is_blocked(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 0, "echo_count": 3},
            claims=verified(2),
            hype={"score": 10, "flag_keys": []},
            settings=settings,
        )
        assert not decision["passes"]
        assert any("independent" in b for b in decision["blockers"])

    def test_unbound_with_two_independent_outlets_passes(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 2, "echo_count": 0},
            claims=verified(1),
            hype={"score": 0, "flag_keys": []},
            settings=settings,
        )
        assert decision["passes"], decision["blockers"]

    def test_high_hype_score_blocks_a_well_sourced_item(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 3, "echo_count": 0},
            claims=verified(4),
            hype={"score": 95, "flag_keys": ["causal_overreach", "animal_only"]},
            settings=settings,
        )
        assert not decision["passes"]
        assert any("hype" in b for b in decision["blockers"])

    def test_no_verified_claims_blocks(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 5, "echo_count": 0},
            claims=[{"text": "x", "status": "unsupported"}],
            hype={"score": 0, "flag_keys": []},
            settings=settings,
        )
        assert not decision["passes"]
        assert any("verified" in b for b in decision["blockers"])

    def test_blockers_are_recorded_for_review(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 0, "echo_count": 0},
            claims=[],
            hype={"score": 99, "flag_keys": ["unbound_primary"]},
            settings=settings,
        )
        assert not decision["passes"]
        # Every independent reason must be stated, not just the first one hit.
        assert len(decision["blockers"]) == 3
