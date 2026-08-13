# ADR-006 — Resolve abstracts through a catalogue chain, not a single source

Date: 2026-08-05
Status: accepted
Supersedes part of: ADR-001 (which assumed OpenAlex as sole abstract source)

## Context

INV-1 makes the primary-source abstract the only substrate against which a
claim may be verified. Everything the pipeline can honestly publish therefore
depends on obtaining that abstract.

In the Phase 3 design, abstracts came from OpenAlex alone, with Crossref used
only as a fallback *search* when OpenAlex returned no candidates. This is a
single point of failure on the one component the central invariant rests on.
Its failure mode is also the worst available: when OpenAlex has a record but no
abstract for it, validity rule V5 rejects the candidate and the item becomes
`unbound` — so an outage or a coverage gap is indistinguishable from "this
paper does not exist", and the pipeline quietly stops being able to verify
anything while reporting a clean run.

Crossref abstracts are sparse (deposit is optional and many publishers skip
it), so it cannot carry this alone either.

## Decision

Resolve abstracts through an ordered chain, keyed by DOI, stopping at the first
non-empty result:

| Order | Source | Auth | Verified 2026-08-05 |
|---|---|---|---|
| 1 | OpenAlex | none | in use since v0.1.0 |
| 2 | Crossref | none | in use since v0.1.0 |
| 3 | Europe PMC | none | 1205-char abstract for a test DOI |
| 4 | Semantic Scholar | none | 1428-char abstract for the same DOI |

All four are key-free and DOI-queryable. Candidate *discovery* remains
OpenAlex-then-Crossref search; this ADR governs abstract *retrieval* once a
DOI is in hand.

The resolved abstract records which catalogue supplied it, and that provenance
is written to the evidence ledger.

## Consequences

**Good.** The invariant no longer depends on one provider's coverage or
uptime. Europe PMC in particular has strong biomedical coverage, which is where
Science's news desk concentrates. Items that would have been rejected by V5 for
a missing abstract now have three further chances to become verifiable — this
raises the pipeline's yield without weakening any check, because the abstract
still has to exist and the claims still have to survive verification against it.

**Cost.** Up to three additional HTTP calls per bound item, only on the miss
path. At ten items a day this is negligible.

**Deliberately not done.** When two catalogues both return an abstract for the
same DOI, the chain takes the first and does not compare them. Cross-checking
abstract texts against each other would detect catalogue corruption, but that
failure has not been observed and the comparison would add a threshold nobody
can currently calibrate. Revisit only with evidence.

**Ledger change.** `binding.abstract_source` is added, so a verdict can be
traced to the catalogue that supplied the text it was checked against — needed
for SC-5 replay.
