# ADR-008 — Abstract resolution runs before V5, over a bounded candidate prefix

Date: 2026-09-23
Status: accepted
Amends: ADR-006 (which left the resolution point implicit and budgeted it per *item*)
Corrects: `docs/02-pseudocode.md` §A, whose V5 ordering made ADR-006's stated benefit unreachable

## Context

ADR-006 established *what* the abstract resolution chain is — an ordered,
DOI-keyed walk over OpenAlex, Crossref, Europe PMC and Semantic Scholar,
extended by ADR-007 with Springer Nature under an open-access-only condition.
It did not establish *when* the chain runs, and the two documents that imply an
answer contradict each other.

- ADR-006 budgets "up to three additional HTTP calls per bound item, only on
  the miss path". *Per bound item* places the chain **after** a candidate has
  been selected.
- `docs/02-pseudocode.md` §A applies validity rule V5 — discard any candidate
  with a blank abstract — **before** scoring, and therefore before any
  selection has happened.

Under §A's ordering, a paper whose abstract only Europe PMC holds is discarded
before the chain could ever be reached, and ADR-006's stated benefit — "items
that would have been rejected by V5 for a missing abstract now have three
further chances to become verifiable" — never occurs. The repository implements
§A: `bind.is_valid_candidate` runs V5 over the raw candidate list and
`bind.bind_item` calls no resolution chain at all.

This is the load-bearing dependency of INV-1. The abstract is the only
substrate a claim may be verified against, so how the pipeline obtains it
decides how much it can honestly publish.

## What the code's data flow actually establishes

Three facts read off the implementation, not off the prose, narrow the choice
before any preference is expressed.

**1. The first abstract is free.** `_search_openalex` requests
`abstract_inverted_index` as part of the *search* response, and
`_search_crossref` requests `abstract` the same way. Every candidate therefore
arrives with an abstract already attached, or with a blank one. Resolution is
not "fetch the abstract"; it is "repair the blanks". The miss path ADR-006
names is the only path that costs anything, and that was right.

**2. The abstract is a scoring input, not only a validity input.** `_score`
weights `0.55·title + 0.25·abstract + 0.12·proximity + 0.08·venue`. A blank
abstract is therefore worth two distinct penalties under the current code: the
candidate is discarded by V5, and had it survived it would have scored zero on
a quarter of the weight. Any placement that defers resolution past scoring must
either drop the abstract term or apply it asymmetrically — scoring candidates
that happened to carry a free abstract against candidates that did not.

**3. The chain is keyed by DOI, so not every blank is rescuable.** A candidate
with a blank abstract and no DOI cannot be looked up in any of the five
catalogues. The rescuable population is the intersection of: blank abstract,
DOI present, and passing V1–V4. A candidate already dead on venue (V2), DOI
shape (V3) or work type (V4) is not one the chain could save, and spending
calls on it is waste with no upside.

**4. The cost ceiling is set by `binding.max_candidates`, not by the item.**
That key is 25. ADR-006's budget of three calls assumed one item yields one
DOI; in the implemented flow one item yields up to 25 DOIs. The difference
between "three per item" and "three per candidate per item" is a factor of 25,
and it is the whole cost question.

## Decision

**1. The chain runs inside `bind`, between candidate discovery and V5, over a
bounded prefix of the candidate list.** The order is: discover → apply V1–V4 →
rank on the abstract-free components → resolve abstracts for the top N
survivors that have a blank abstract and a DOI → apply V5 → score with the full
four-term formula → threshold.

Ranking twice is deliberate. The pre-resolution rank exists only to decide
*which* candidates are worth a call; it uses `title`, `proximity` and `venue`
renormalised to 1.0, which is the same signal `_score` already computes and
which title overlap already dominates. The post-resolution score is the real
one and uses all four terms, so a rescued abstract earns its contribution
rather than being excluded from it.

**2. The per-item call budget is `N × (chain length − 1)`, and N is
configuration.** `binding.resolve_top_n` is a new key. The chain length is
counted excluding the discovery catalogue, which has already answered. With the
ADR-006/007 chain this is at most four hops per resolved candidate — Springer
Nature (for `10.1038/` and `10.1007/` prefixes), Crossref, Europe PMC,
Semantic Scholar — and the walk stops at the first non-empty result, so four is
a ceiling reached only when every catalogue misses.

**3. Failure of a hop is a miss, not an error, and there are no retries within
a hop.** The chain *is* the retry: the next catalogue is the fallback, which is
why ADR-006 exists. A hop inherits `fetch.timeout_seconds` (25) and
`fetch.delay_seconds` (1.0) as a per-host politeness delay. A timeout, a
non-200 or an unparseable body all degrade to the empty string and advance to
the next hop, matching how `_get_json` already degrades a dead catalogue to
"no candidates" rather than aborting the run.

**4. A new module owns the chain: `src/sci/abstracts.py`.** Not `bind.py`,
which is already 407 lines against the project's 500-line cap, and not `fetch`,
which is feed ingestion and never touches a DOI. The resolver takes a DOI and
returns text plus provenance, which is a different job from ranking candidates
and deserves its own test surface. ADR-007's open-access gate is a condition on
the value at the point the value is constructed, so it belongs in this module
rather than in the binder that consumes it.

**5. Provenance is a separate field from the discovery catalogue.**
`Candidate.catalogue` currently records which catalogue *found* the candidate.
ADR-006's `binding.abstract_source` records which catalogue *supplied the
text*. After this decision those routinely differ — discovered on OpenAlex,
abstract from Europe PMC — and collapsing them would make an SC-5 replay
attribute a verdict to the wrong source. Both are written to the ledger, along
with ADR-007's licence identifier where one applies.

**6. Non-open-access text rejected under ADR-007 is dropped where it is
fetched.** Resolving before V5 means a non-OA abstract may transit the
resolver's memory. ADR-007 §3 holds unchanged: it is not stored, not cached,
not passed to the binder, not written to the ledger. The candidate records that
it was rejected for licence reasons and its DOI, never its text — which is why
the gate lives in the resolver and not downstream of it.

## The measurement

<!-- pending: live probe of the current Science news feed against the chain -->

## Rejected placements

<!-- pending -->

## Consequences

<!-- pending -->

## Open decisions

<!-- pending -->
