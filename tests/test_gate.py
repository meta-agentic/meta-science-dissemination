"""Tests for the publication gate and the independence weighting.

The failure this file exists to prevent: three press-release republishers
being counted as three independent confirmations, so an unverified story
sails through the gate looking well sourced.
"""

from __future__ import annotations

import pytest

from pathlib import Path

from sci import gate
from sci.bind import Bound, Candidate, Unbound
from sci.config import ROOT, ConfigError, Source, load_pipeline


@pytest.fixture
def settings():
    class Stub:
        pipeline = load_pipeline(ROOT / "config" / "pipeline.yaml")

    return Stub()


def verified(n: int) -> list[dict]:
    return [{"text": f"claim {i}", "status": "verified"} for i in range(n)]


def assessment(score: int = 0, *, flags: list[str] | None = None,
               completeness: float = 1.0) -> dict:
    """A limitation-assessment record as the gate receives it.

    `completeness` defaults to 1.0 — every rule was able to run — so a test
    that is not about G4 is not accidentally about G4. The interesting value
    is 3/7: what an item with no abstract can reach.
    """
    return {
        "score": score,
        "flag_keys": flags or [],
        "evidence_completeness": completeness,
    }


BLIND = 3 / 7  # No abstract: only the three text-only rules could run.


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
            hype=assessment(10),
            settings=settings,
        )
        assert decision["passes"], decision["blockers"]

    def test_unbound_with_only_echo_is_blocked(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 0, "echo_count": 3},
            claims=verified(2),
            hype=assessment(10),
            settings=settings,
        )
        assert not decision["passes"]
        assert any("independent" in b for b in decision["blockers"])

    def test_unbound_with_two_independent_outlets_passes(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 2, "echo_count": 0},
            claims=verified(1),
            hype=assessment(0),
            settings=settings,
        )
        assert decision["passes"], decision["blockers"]

    def test_high_hype_score_blocks_a_well_sourced_item(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 3, "echo_count": 0},
            claims=verified(4),
            hype=assessment(95, flags=["causal_overreach", "animal_only"]),
            settings=settings,
        )
        assert not decision["passes"]
        assert any("hype" in b for b in decision["blockers"])

    def test_no_verified_claims_blocks(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 5, "echo_count": 0},
            claims=[{"text": "x", "status": "unsupported"}],
            hype=assessment(0),
            settings=settings,
        )
        assert not decision["passes"]
        assert any("verified" in b for b in decision["blockers"])

    def test_blockers_are_recorded_for_review(self, settings):
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 0, "echo_count": 0},
            claims=[],
            hype=assessment(99, flags=["unbound_primary"], completeness=BLIND),
            settings=settings,
        )
        assert not decision["passes"]
        # Every independent reason must be stated, not just the first one hit.
        assert len(decision["blockers"]) == 4


class TestEvidenceCompletenessGate:
    """G4 — INV-2 at the publication boundary.

    G1 asks whether anything was confirmed. G4 asks whether the apparatus
    that would have found a problem was able to run. An item can satisfy the
    first and fail the second, and that item is exactly the one the spike
    published: a perfect hype score of 0 from checks that never executed.
    """

    def test_an_item_that_passes_g1_is_still_blocked_by_g4(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 3, "echo_count": 0},
            claims=verified(1),
            hype=assessment(0, completeness=BLIND),
            settings=settings,
        )
        assert not decision["passes"]
        assert decision["blockers"] == ["only 43% of checks could run; need 60%"]
        # G1 is satisfied — the two conditions are independent.
        assert any("verified claim" in p for p in decision["passed"])

    def test_full_completeness_passes_g4(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 0, "echo_count": 0},
            claims=verified(1),
            hype=assessment(0, completeness=1.0),
            settings=settings,
        )
        assert decision["passes"], decision["blockers"]
        assert any("checks could run" in p for p in decision["passed"])

    def test_g1_g3_and_g4_are_all_reported_from_one_call(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 3, "echo_count": 0},
            claims=[{"text": "x", "status": "unsupported"}],
            hype=assessment(95, flags=["causal_overreach"], completeness=BLIND),
            settings=settings,
        )
        blockers = decision["blockers"]
        assert any("verified claim" in b for b in blockers)      # G1
        assert any("hype score" in b for b in blockers)           # G3
        assert any("checks could run" in b for b in blockers)     # G4
        assert len(blockers) == 3

    def test_a_record_without_a_completeness_figure_fails_closed(self, settings):
        """Absence of the measurement is not permission to publish."""
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 3, "echo_count": 0},
            claims=verified(2),
            hype={"score": 0, "flag_keys": []},
            settings=settings,
        )
        assert not decision["passes"]
        assert any("checks could run" in b for b in decision["blockers"])

    def test_the_spike_path_is_closed(self, settings):
        """No primary source, no abstract, nothing verified — and a hype 0.

        The assessment record here is the shape SCI-13 emits for an item with
        no abstract: three of seven rules could run, and none of them fired.
        Under the old gate that was a clean bill of health.
        """
        decision = gate.evaluate(
            binding=unbound(),
            corroboration={"independent_count": 2, "echo_count": 0},
            claims=[{"text": "x", "status": "unverifiable"}],
            hype=assessment(0, completeness=BLIND),
            settings=settings,
        )
        assert not decision["passes"]
        assert any("verified claim" in b for b in decision["blockers"])    # G1
        assert any("checks could run" in b for b in decision["blockers"])  # G4

    def test_the_decision_records_the_measurement(self, settings):
        decision = gate.evaluate(
            binding=bound(),
            corroboration={"independent_count": 0, "echo_count": 0},
            claims=verified(1),
            hype=assessment(0, completeness=BLIND),
            settings=settings,
        )
        assert decision["evidence_completeness"] == pytest.approx(BLIND)


class TestThresholdConfiguration:
    """The threshold is a published lever, so a bad one is a hard failure."""

    def _pipeline_with(self, tmp_path, gate_block: str) -> Path:
        source = (ROOT / "config" / "pipeline.yaml").read_text(encoding="utf-8")
        head = source.split("\ngate:\n")[0]
        path = tmp_path / "pipeline.yaml"
        path.write_text(f"{head}\ngate:\n{gate_block}", encoding="utf-8")
        return path

    _COMPLETE = (
        "  require_primary_or_independent: true\n"
        "  min_independent_corroborators: 2\n"
        "  max_hype_score: 60\n"
        "  min_verified_claims: 1\n"
    )

    def test_shipped_config_declares_the_threshold(self):
        pipeline = load_pipeline(ROOT / "config" / "pipeline.yaml")
        assert 0.0 <= float(pipeline.get("gate", "min_evidence_completeness")) <= 1.0

    def test_missing_threshold_is_a_config_error(self, tmp_path):
        path = self._pipeline_with(tmp_path, self._COMPLETE)
        with pytest.raises(ConfigError, match="min_evidence_completeness"):
            load_pipeline(path)

    @pytest.mark.parametrize("value", ["1.4", "-0.1"])
    def test_threshold_outside_zero_to_one_is_a_config_error(self, tmp_path, value):
        path = self._pipeline_with(
            tmp_path, f"{self._COMPLETE}  min_evidence_completeness: {value}\n"
        )
        with pytest.raises(ConfigError, match=r"\[0, 1\]"):
            load_pipeline(path)
