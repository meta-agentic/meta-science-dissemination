"""Regression tests for the spike's central failure (SC-1) and the type that
makes it unrepresentable.

The spike bound four of four news items to *themselves*: OpenAlex indexes
Science's own journalism as works, under an editorial venue, with no abstract.
Those records scored 0.48-0.55 on title similarity — arithmetically correct and
editorially worthless. These tests assert that can no longer happen.
"""

from __future__ import annotations

import pytest

from sci import bind
from sci.config import load
from sci.store import Item

SETTINGS = load()


def news_item(title: str = "Major highway of the human nervous system gets a complete road map",
              doi: str = "10.1126/science.z5r6v9y") -> Item:
    return Item(
        id="science_news:test", source_id="science_news", title=title,
        summary="First comprehensive map of the vagus nerve",
        link="https://example.org/a", doi=doi,
        published_at="2026-08-03T12:40:00+00:00",
    )


def candidate(**kw) -> bind.Candidate:
    base = dict(
        doi="10.1038/s41586-026-00001-0",
        title="A comprehensive anatomical map of the human vagus nerve",
        abstract="We present a complete map of the human vagus nerve across four donors.",
        venue="Nature", published="2026-08-01", type="article",
    )
    base.update(kw)
    return bind.Candidate(**base)


class TestValidity:
    """V1-V5, checked before scoring."""

    def test_valid_candidate_passes(self):
        ok, rule, _ = bind.is_valid_candidate(candidate(), news_item(), SETTINGS)
        assert ok, rule

    def test_v1_candidate_that_is_the_item_itself_is_rejected(self):
        ok, rule, _ = bind.is_valid_candidate(
            candidate(doi="10.1126/science.z5r6v9y"), news_item(), SETTINGS)
        assert not ok and rule == "V1"

    def test_v1_is_case_insensitive(self):
        ok, rule, _ = bind.is_valid_candidate(
            candidate(doi="10.1126/SCIENCE.Z5R6V9Y"), news_item(), SETTINGS)
        assert not ok and rule == "V1"

    def test_v2_editorial_venue_is_rejected(self):
        ok, rule, _ = bind.is_valid_candidate(
            candidate(venue="AAAS Articles DO Group"), news_item(), SETTINGS)
        assert not ok and rule == "V2"

    def test_v3_news_shaped_doi_is_rejected(self):
        ok, rule, _ = bind.is_valid_candidate(
            candidate(doi="10.1126/science.zgjqlml"), news_item(), SETTINGS)
        assert not ok and rule == "V3"

    def test_v3_spares_a_research_doi_from_the_same_publisher(self):
        ok, _, _ = bind.is_valid_candidate(
            candidate(doi="10.1126/science.adr1420", venue="Science"),
            news_item(), SETTINGS)
        assert ok

    def test_v4_non_research_type_is_rejected(self):
        for kind in ("editorial", "letter", "erratum", "paratext"):
            ok, rule, _ = bind.is_valid_candidate(
                candidate(type=kind), news_item(), SETTINGS)
            assert not ok and rule == "V4", kind

    def test_v4_admits_a_preprint(self):
        # Preprints are bindable and separately flagged, not excluded here.
        ok, _, _ = bind.is_valid_candidate(
            candidate(type="preprint", venue="bioRxiv"), news_item(), SETTINGS)
        assert ok

    def test_v5_candidate_without_an_abstract_is_rejected(self):
        for blank in ("", "   ", "\n"):
            ok, rule, _ = bind.is_valid_candidate(
                candidate(abstract=blank), news_item(), SETTINGS)
            assert not ok and rule == "V5", repr(blank)

    def test_the_spikes_actual_failure_case(self):
        """SC-1. The exact record that bound four of four items to themselves."""
        ok, rule, reason = bind.is_valid_candidate(
            candidate(
                doi="10.1126/science.z5r6v9y",
                title="Major highway of the human nervous system gets a complete road map",
                abstract="",
                venue="AAAS Articles DO Group",
                type="article",
            ),
            news_item(), SETTINGS,
        )
        assert not ok
        assert rule == "V1", f"expected the identity rule to fire first, got {rule}: {reason}"


class TestBindingType:
    """The illegal state is unconstructible, not merely unchecked."""

    def test_unbound_has_no_abstract_attribute_at_all(self):
        unbound = bind.Unbound(reason="nothing found", rejected=())
        assert not hasattr(unbound, "abstract")
        with pytest.raises(AttributeError):
            _ = unbound.abstract

    def test_bound_with_a_blank_abstract_cannot_be_constructed(self):
        for blank in ("", "   "):
            with pytest.raises(ValueError):
                bind.Bound(candidate=candidate(), abstract=blank, score=0.5)

    def test_weak_also_carries_an_abstract(self):
        weak = bind.Weak(candidate=candidate(), abstract="real text", score=0.3)
        assert weak.abstract == "real text"
        with pytest.raises(ValueError):
            bind.Weak(candidate=candidate(), abstract="", score=0.3)

    def test_statuses_are_distinct(self):
        assert bind.Bound(candidate=candidate(), abstract="x", score=0.5).status == "bound"
        assert bind.Weak(candidate=candidate(), abstract="x", score=0.3).status == "weak"
        assert bind.Unbound(reason="r", rejected=()).status == "unbound"

    def test_unbound_retains_rejections_for_the_ledger(self):
        """'Found a preprint' must remain distinguishable from 'found nothing'."""
        rejected = (bind.Rejection(doi="10.1/x", title="t", rule="V5",
                                   reason="no abstract"),)
        unbound = bind.Unbound(reason="all candidates invalid", rejected=rejected)
        assert unbound.to_dict()["rejected"][0]["rule"] == "V5"

    def test_ledger_of_an_unbound_binding_exposes_no_abstract_key(self):
        led = bind.Unbound(reason="r", rejected=()).to_dict()
        assert "abstract" not in led
        assert led["status"] == "unbound"
