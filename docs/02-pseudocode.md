# Phase 2 — Pseudocode

Status: complete, QA gate passed
Depends on: `01-specification.md` (INV-1, INV-2)

Only the algorithms that carry the invariants are specified here. Fetching,
persistence and rendering are mechanical and are left to Architecture.

---

## A. Candidate validity — the fix for self-binding

The spike's defect was not a bad *score*; it was a missing *validity* step. A
news article scoring 0.55 against itself is arithmetically correct and
editorially worthless. Validity is therefore checked **before** scoring, and a
candidate that fails is discarded rather than down-weighted — a rejected
candidate must never be able to win on tie-breakers.

```
FUNCTION is_valid_candidate(candidate, news_item) -> (bool, reason)

    # V1 — identity. The item cannot be its own primary source.
    IF normalise(candidate.doi) == normalise(news_item.doi):
        RETURN false, "candidate is the news item itself"

    # V2 — editorial venue. Publishers index their own journalism as works.
    # `AAAS Articles DO Group` is the observed instance (M3); the list is
    # configuration, because every publisher has its own such container.
    IF normalise(candidate.venue) IN config.editorial_venues:
        RETURN false, "candidate is journalism, not a study"

    # V3 — news DOI shape. Science news DOIs use an opaque suffix
    # (10.1126/science.z5r6v9y) where research uses a structured one
    # (10.1126/science.adr1420). Matching the news shape is disqualifying.
    IF matches(candidate.doi, config.news_doi_patterns):
        RETURN false, "DOI matches a known news-content pattern"

    # V4 — work type. Editorials, letters, corrections and news are not the
    # study the story is about.
    IF candidate.type NOT IN config.research_types:
        RETURN false, "work type '{candidate.type}' is not research"

    # V5 — THE INVARIANT. Binding exists solely to obtain independent text to
    # verify against. A candidate with no abstract cannot serve that purpose,
    # however well its title matches. This single rule would have blocked all
    # four of the spike's false bindings.
    IF is_blank(candidate.abstract):
        RETURN false, "no abstract: cannot serve as verification substrate"

    RETURN true, "valid"
```

**Consequence, stated plainly:** binding will now fail often. That is the
correct behaviour, not a regression. A news item whose study cannot be
identified is a news item whose claims cannot be checked, and the honest output
is a review note, not a post.

---

## B. Binding

```
FUNCTION bind(news_item) -> Binding

    window   <- date_window(news_item.published_at, lookback, lookahead)
    raw      <- openalex_search(salient_terms(news_item), window)
    IF raw is empty:
        raw <- crossref_search(salient_terms(news_item), window)

    valid, rejected <- partition(raw, is_valid_candidate)

    # Rejections are retained. "We found the paper but it was a preprint" and
    # "we found nothing at all" are different editorial situations, and the
    # ledger must be able to tell them apart.
    IF valid is empty:
        RETURN Unbound(reason=summarise(rejected), rejected=rejected)

    scored <- [score(c, news_item) FOR c IN valid]
    best   <- max(scored)

    IF best.score >= bound_threshold:   RETURN Bound(best, runners_up, rejected)
    IF best.score >= weak_threshold:    RETURN Weak(best, runners_up, rejected)
    RETURN Unbound(reason="best candidate scored below weak threshold",
                   rejected=rejected)
```

`Bound` and `Weak` both carry a non-empty abstract by construction of A/V5.
`Unbound` carries none. This is what makes INV-1 enforceable by type rather
than by discipline (see Architecture, ADR-001).

Scoring is unchanged from the spike and remains: `0.55·title + 0.25·abstract +
0.12·proximity + 0.08·venue`. It was never the problem.

---

## C. Claim verification — provenance separation

```
FUNCTION verify(claim, binding, news_item) -> Verdict

    # INV-1 enforced at the single point where it matters. The news text is
    # deliberately NOT in scope: it is where the claim came from, so
    # confirming a claim against it proves only that the model can copy.
    IF binding has no abstract:
        RETURN Verdict(status = UNVERIFIABLE,
                       reason = "no primary abstract; claim cannot be checked",
                       checked_against = NONE)

    substrate <- binding.abstract          # the ONLY verification substrate

    SWITCH claim.type:

        CASE quantity:
            missing <- [n FOR n IN claim.numbers
                        IF NOT appears_literally(n, substrate)]
            IF missing is non-empty:
                RETURN UNSUPPORTED("number(s) absent from abstract: " + missing)
            RETURN VERIFIED("all numeric literals present in abstract")

        CASE causal:
            IF NOT terms_overlap(claim, substrate) >= 0.6:
                RETURN UNSUPPORTED("claim terms absent from abstract")
            IF abstract_uses_causal_language(substrate):
                RETURN VERIFIED("abstract states a causal relationship")
            IF abstract_uses_correlational_language(substrate):
                # The signature press-release distortion, now detectable
                # because there is finally something to compare against.
                RETURN HEDGED("abstract reports an association, not a cause")
            RETURN HEDGED("abstract does not state causation explicitly")

        DEFAULT:
            overlap <- terms_overlap(claim, substrate)
            IF overlap >= 0.6: RETURN VERIFIED(overlap)
            RETURN UNSUPPORTED(overlap)
```

Four statuses, and the fourth is the new one:

| Status | Meaning |
|---|---|
| `verified` | Checked against the abstract and supported. |
| `hedged` | Supported, but the abstract is weaker than the claim. Publishable **with** disclosure. |
| `unsupported` | Checked and contradicted or absent. Never published. |
| `unverifiable` | **Could not be checked at all.** Never published, and distinct from `unsupported` — the difference between "this is wrong" and "we do not know", which is exactly what the spike collapsed. |

---

## D. Limitation rules — three-valued, per INV-2

```
FUNCTION assess_rule(rule, news_text, abstract) -> Outcome

    IF rule.requires_abstract AND is_blank(abstract):
        # The spike returned "no flag" here, which the gate read as "clean".
        RETURN NOT_ASSESSABLE(rule, "no abstract available")

    IF rule.fires(news_text, abstract):
        RETURN FIRED(rule, detail, penalty)

    RETURN CLEAR(rule)


FUNCTION assess(news_item, binding, corroboration) -> Assessment

    outcomes <- [assess_rule(r, news_item.text, binding.abstract)
                 FOR r IN RULES]

    RETURN Assessment(
        hype_score            = sum(penalty FOR o IN outcomes IF o is FIRED),
        # Reported separately: how much of the check suite could actually run.
        # A score of 0 out of 2 applicable rules is not a clean bill of health.
        assessable_count      = count(o NOT NOT_ASSESSABLE),
        total_rules           = count(RULES),
        evidence_completeness = assessable_count / total_rules,
        outcomes              = outcomes)
```

`evidence_completeness` is the direct answer to the spike's `hype_score = 0`.
Zero penalties out of zero applicable rules and zero penalties out of seven
applicable rules are opposite situations and must not share a number.

---

## E. Gate — fail closed

```
FUNCTION gate(binding, corroboration, verdicts, assessment) -> Decision

    blockers <- []

    # G1 — provenance. Verified claims exist only where an abstract existed,
    # so this condition transitively enforces INV-1 at the publication boundary.
    verified <- [v FOR v IN verdicts IF v.status == VERIFIED]
    IF count(verified) < min_verified_claims:
        blockers += "only {n} verified claim(s), need {min}"

    # G2 — sourcing. Either the study is identified, or enough independent
    # newsrooms have checked it. Echo republication never satisfies this.
    IF binding.status != BOUND
       AND corroboration.independent_count < min_independent:
        blockers += "no primary study bound and {k} independent corroborator(s)"

    # G3 — overreach.
    IF assessment.hype_score > max_hype_score:
        blockers += "hype score {s} exceeds {max}"

    # G4 — INV-2 made explicit. Too few rules could run for the clean result
    # to mean anything. Without this, an item with no abstract scores a
    # perfect 0 and sails through — the spike's exact path.
    IF assessment.evidence_completeness < min_evidence_completeness:
        blockers += "only {p}% of checks could run; insufficient evidence"

    RETURN Decision(passes = is_empty(blockers),
                    blockers = blockers,       # ALL of them, never the first
                    passed = satisfied_conditions)
```

G1 and G4 are independent on purpose. G1 asks whether anything was confirmed;
G4 asks whether the confirmation apparatus was able to run. An item can fail
G4 while passing G1, and that item must not be published.

---

## F. Draft prompt construction

```
FUNCTION build_prompt(news_item, verdicts, assessment, binding)

    publishable <- [v FOR v IN verdicts IF v.status == VERIFIED]
    disclose    <- [v FOR v IN verdicts IF v.status == HEDGED]

    # Unsupported and unverifiable claim TEXT is never placed in the prompt.
    # Listing it even as "do not use this" reintroduces the wording into the
    # generator's context, where a fluent sentence can quietly recover it.
    # Only the count is passed, so the model knows material was withheld.
    withheld_count <- count(verdicts) - count(publishable) - count(disclose)

    RETURN template(claims   = publishable,
                    hedged   = disclose,
                    limits   = [o.detail FOR o IN assessment.outcomes IF FIRED],
                    withheld = withheld_count,
                    source   = binding.citation)
```

This is a correction to the spike, which passed rejected claims into the prompt
under a "do not use these" heading. Negative instructions are a weak control
over a generative model; omission is a strong one.

---

## QA gate — Phase 2

| Check | Result |
|---|---|
| INV-1 has a single enforcement point | Pass — §C, one guard, all types flow through it |
| INV-2 is expressed in every check | Pass — §D three-valued outcomes, §E G4 |
| The spike's 4/4 pass is now impossible | Pass — traced: V5 rejects → `Unbound` → no abstract → all claims `unverifiable` → G1 and G4 both block |
| `unsupported` distinguished from `unverifiable` | Pass — §C |
| No algorithm depends on a model's self-assessment | Pass — model output enters only as *proposed* claims |
| Rejected candidates retained for the ledger | Pass — §B |
| OQ-1 still open | Carried — decided in ADR-003 |

Proceed to Phase 3.
