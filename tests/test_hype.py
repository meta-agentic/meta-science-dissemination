"""Tests for three-valued limitation outcomes (INV-2).

The failure this file exists to prevent: a rule that could not run being
counted as a rule that passed. The spike's hype score of 0 was produced by
four checks that never executed, and the gate read it as a clean bill of
health. `clear` and `not_assessable` must never be the same value again.
"""

from __future__ import annotations

import pytest

from sci import draft, hype
from sci.bind import Bound, Candidate, Unbound
from sci.config import ROOT, load_pipeline
from sci.store import Analysis, Item


@pytest.fixture
def settings():
    class Stub:
        pipeline = load_pipeline(ROOT / "config" / "pipeline.yaml")

    return Stub()


def make_item(title: str, summary: str = "") -> Item:
    return Item(id="t:1", source_id="science_news", title=title,
                summary=summary, link="https://example.org/a")


def bound(abstract: str, *, is_preprint: bool = False) -> Bound:
    return Bound(
        candidate=Candidate(doi="10.1038/x", title="A study", abstract=abstract,
                            is_preprint=is_preprint),
        abstract=abstract,
        score=0.51,
    )


def unbound() -> Unbound:
    return Unbound(reason="all candidates failed validity")


def outcome(record: dict, key: str) -> dict:
    return next(o for o in record["flags"] if o["flag"] == key)


ABSTRACT_RULES = ("animal_only", "small_sample", "no_control", "causal_overreach")
TEXT_ONLY_RULES = ("preprint", "press_release_only", "unbound_primary")


class TestThreeValuedOutcomes:
    def test_every_rule_reports_exactly_one_outcome(self, settings):
        record = hype.assess(make_item("A finding"), unbound(), {}, settings)
        assert len(record["flags"]) == hype.TOTAL_RULES
        assert [o["flag"] for o in record["flags"]] == [
            *ABSTRACT_RULES, *TEXT_ONLY_RULES
        ]

    @pytest.mark.parametrize("key", ABSTRACT_RULES)
    def test_abstract_rules_are_not_assessable_without_an_abstract(self, settings, key):
        """The whole story: never `clear`, because nothing was checked."""
        record = hype.assess(make_item("Coffee causes longer life"), unbound(), {}, settings)
        assert outcome(record, key)["status"] == hype.NOT_ASSESSABLE

    def test_text_only_rules_still_run_without_an_abstract(self, settings):
        record = hype.assess(make_item("A finding"), unbound(), {}, settings)
        for key in TEXT_ONLY_RULES:
            assert outcome(record, key)["status"] != hype.NOT_ASSESSABLE

    def test_a_rule_that_ran_and_found_nothing_is_clear(self, settings):
        record = hype.assess(
            make_item("Trial in patients reports benefit"),
            bound("A randomised controlled trial in 4,000 patients reports benefit."),
            {},
            settings,
        )
        assert outcome(record, "no_control")["status"] == hype.CLEAR
        assert outcome(record, "animal_only")["status"] == hype.CLEAR

    def test_a_firing_rule_carries_its_detail_and_penalty(self, settings):
        record = hype.assess(
            make_item("Coffee causes longer life"),
            bound("Coffee consumption was associated with longer life in this "
                  "randomised cohort of 9,000 adults."),
            {},
            settings,
        )
        fired = outcome(record, "causal_overreach")
        assert fired["status"] == hype.FIRED
        assert "association" in fired["detail"]
        assert fired["penalty"] == 30
        assert "causal_overreach" in record["flag_keys"]

    def test_flag_keys_lists_fired_rules_only(self, settings):
        record = hype.assess(make_item("A finding"), unbound(), {}, settings)
        assert record["flag_keys"] == ["unbound_primary"]


class TestEvidenceCompleteness:
    def test_unbound_item_reaches_three_of_seven(self, settings):
        record = hype.assess(make_item("A finding"), unbound(), {}, settings)
        assert record["assessable_count"] == 3
        assert record["total_rules"] == 7
        assert record["evidence_completeness"] == pytest.approx(3 / 7)

    def test_bound_item_with_an_abstract_reaches_seven_of_seven(self, settings):
        record = hype.assess(
            make_item("Trial reports benefit"),
            bound("A randomised controlled trial in 4,000 adult patients."),
            {},
            settings,
        )
        assert record["assessable_count"] == 7
        assert record["evidence_completeness"] == pytest.approx(1.0)

    def test_zero_from_three_rules_differs_from_zero_from_seven(self, settings):
        """The spike's exact confusion, now impossible to reproduce."""
        blind = hype.assess(make_item("Trial reports benefit"), unbound(), {}, settings)
        checked = hype.assess(
            make_item("Trial reports benefit"),
            bound("A randomised controlled trial in 4,000 adult patients."),
            {},
            settings,
        )
        # Both would have scored 0 under the old flags-only record; only one of
        # them is actually a clean result.
        assert checked["score"] == 0
        assert blind["evidence_completeness"] < checked["evidence_completeness"]

    def test_completeness_is_reported_beside_the_score(self, settings):
        record = hype.assess(make_item("A finding"), unbound(), {}, settings)
        for key in ("score", "assessable_count", "total_rules", "evidence_completeness"):
            assert key in record


class TestPersistence:
    def test_the_full_record_survives_a_store_round_trip(self, settings, tmp_path):
        from sci.store import Store

        item = make_item("A finding")
        record = hype.assess(item, unbound(), {}, settings)
        store = Store(tmp_path / "sci.db")
        store.upsert_item(item)
        store.save_analysis(item.id, Analysis(hype=record))

        stored = store.get_analysis(item.id)
        assert stored is not None
        assert stored.hype["assessable_count"] == 3
        assert stored.hype["total_rules"] == 7
        assert stored.hype["evidence_completeness"] == pytest.approx(3 / 7)
        assert len(stored.hype["flags"]) == hype.TOTAL_RULES


class TestLimitationsUseFiredOutcomesOnly:
    def test_a_not_assessable_rule_is_never_stated_as_a_limitation(self, settings):
        record = hype.assess(make_item("Coffee causes longer life"), unbound(), {}, settings)
        analysis = Analysis(hype=record, claims=[],
                            binding={"status": "unbound"})

        text = draft._format_limitations(analysis)

        assert "could not run" not in text
        assert "Primary study could not be identified" in text

    def test_a_fired_rule_is_stated(self, settings):
        record = hype.assess(
            make_item("Coffee causes longer life"),
            bound("Coffee consumption was associated with longer life in this "
                  "randomised cohort of 9,000 adults."),
            {},
            settings,
        )
        analysis = Analysis(hype=record, claims=[],
                            binding={"status": "bound"})

        assert "association" in draft._format_limitations(analysis)

    def test_clear_outcomes_are_not_stated_either(self, settings):
        record = hype.assess(
            make_item("Trial in patients reports benefit"),
            bound("A randomised controlled trial in 4,000 adult patients."),
            {},
            settings,
        )
        analysis = Analysis(hype=record, claims=[],
                            binding={"status": "bound"})

        assert draft._format_limitations(analysis).startswith("- Nessun limite")
