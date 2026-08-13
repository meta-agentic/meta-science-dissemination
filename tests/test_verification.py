"""Tests for the parts that decide what gets published.

The prose generator is not tested here — it is a model, and its output is
constrained by what these functions let through. These are the constraints.
"""

from __future__ import annotations

import pytest

from sci import claims, textutil
from sci.store import Item


def make_item(title: str, summary: str = "") -> Item:
    return Item(id="t:1", source_id="science_news", title=title, summary=summary, link="https://example.org/a")


class TestNumberVerification:
    """A number that is not in the source text must never be publishable."""

    def test_number_present_is_supported(self):
        assert textutil.number_supported("42", "the effect was 42% in treated animals")

    def test_number_absent_is_not_supported(self):
        assert not textutil.number_supported("87", "the effect was 42% in treated animals")

    def test_thousands_separator_is_normalised(self):
        assert textutil.number_supported("1200", "we enrolled 1,200 participants")

    def test_percent_matches_bare_number(self):
        assert textutil.number_supported("42%", "a 42 percent reduction")

    def test_trailing_zero_decimal_normalised(self):
        assert textutil.number_supported("3.5", "a 3.50 fold increase")

    def test_empty_number_is_not_supported(self):
        assert not textutil.number_supported("", "anything at all")


class TestClaimVerification:
    def test_quantity_claim_with_invented_number_is_rejected(self):
        item = make_item("Drug cuts risk", "A trial reported a reduction")
        claim = {"text": "The drug cut risk by 70%", "type": "quantity", "numbers": ["70"]}
        result = claims.verify(claim, item, "The trial reported a 30% reduction in risk.")
        assert result["status"] == claims.UNSUPPORTED
        assert "70" in result["reason"]

    def test_quantity_claim_with_real_number_is_verified(self):
        item = make_item("Drug cuts risk", "A trial reported a reduction")
        claim = {"text": "The drug cut risk by 30%", "type": "quantity", "numbers": ["30"]}
        result = claims.verify(claim, item, "The trial reported a 30% reduction in risk.")
        assert result["status"] == claims.VERIFIED

    def test_causal_claim_over_correlational_abstract_is_hedged(self):
        item = make_item("Coffee causes longer life")
        claim = {"text": "Coffee consumption causes longer life", "type": "causal", "numbers": []}
        abstract = "Coffee consumption was associated with longer life in this cohort."
        result = claims.verify(claim, item, abstract)
        assert result["status"] == claims.HEDGED
        assert "association" in result["reason"]

    def test_causal_claim_with_causal_abstract_is_verified(self):
        item = make_item("Gene edit reverses blindness")
        claim = {"text": "The edit reverses blindness in the model", "type": "causal", "numbers": []}
        abstract = "The edit reverses blindness in the mouse model, and restores function."
        result = claims.verify(claim, item, abstract)
        assert result["status"] == claims.VERIFIED

    def test_causal_claim_without_abstract_is_hedged_not_verified(self):
        item = make_item("Vaccine prevents disease", "Researchers say it prevents disease")
        claim = {"text": "The vaccine prevents disease", "type": "causal", "numbers": []}
        result = claims.verify(claim, item, "")
        assert result["status"] == claims.HEDGED
        assert result["checked_against"] == "news_only"

    def test_claim_unrelated_to_sources_is_rejected(self):
        item = make_item("Vagus nerve mapped in detail")
        claim = {"text": "Quantum entanglement powers photosynthesis in tundra lichen",
                 "type": "generalisation", "numbers": []}
        result = claims.verify(claim, item, "We present a complete map of the vagus nerve.")
        assert result["status"] == claims.UNSUPPORTED


class TestSimilarity:
    def test_identical_titles_score_one(self):
        assert textutil.similarity("Vagus nerve mapped", "Vagus nerve mapped") == 1.0

    def test_unrelated_titles_score_low(self):
        score = textutil.similarity("Vagus nerve mapped", "Jupiter storm shrinks again")
        assert score < 0.1

    def test_short_headline_matches_long_paper_title(self):
        score = textutil.similarity(
            "Complete map of the vagus nerve",
            "A comprehensive anatomical and molecular map of the human vagus nerve "
            "across four donors using spatial transcriptomics",
        )
        assert score > 0.3

    def test_empty_input_is_zero_not_error(self):
        assert textutil.similarity("", "anything") == 0.0


class TestSlug:
    def test_accents_are_stripped(self):
        assert textutil.slugify("Perché la ricerca è utile") == "perche-la-ricerca-e-utile"

    def test_long_title_truncates_on_word_boundary(self):
        slug = textutil.slugify("a" * 30 + " " + "b" * 40, max_length=40)
        assert len(slug) <= 40
        assert not slug.endswith("-")

    def test_empty_title_has_a_fallback(self):
        assert textutil.slugify("!!!") == "senza-titolo"
