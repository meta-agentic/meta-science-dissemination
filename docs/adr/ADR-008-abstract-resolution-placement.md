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

Four facts read off the implementation, not off the prose, narrow the choice
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

**R1 — Resolve at ingestion, as each feed item is fetched.** Cheapest to
reason about and wrong for a reason that no budget can fix: the dependency does
not exist yet. M2 records that `prism:doi` in the Science news feed is the DOI
*of the news article*, never of the study. At ingestion the only key in hand is
precisely the one key the chain must not be given. There is nothing to resolve
against until discovery has produced candidate DOIs, so this placement is not
expensive, it is impossible.

**R2 — Resolve after selection, on the winning candidate only.** This is
ADR-006 read literally — "three additional HTTP calls per bound item" — and it
is the cheapest placement that is actually implementable. It loses on
correctness, twice over.

First, it cannot deliver the benefit ADR-006 claims for it. V5 has already
discarded every blank-abstract candidate, so the winner is drawn from a
population the chain was never allowed to widen. A paper whose abstract only
Europe PMC holds is gone before the call is made, and ADR-006's "three further
chances to become verifiable" describes an event that cannot occur. The only
candidate the chain can reach is one that already had an abstract and therefore
did not need it.

Second, if V5 is moved out of the way to let blank candidates through to
scoring, the ranking becomes dishonest rather than merely incomplete. `_score`
gives the abstract term 0.25 of the weight. A blank-abstract candidate scores
zero on that term while a candidate that happened to carry a free abstract
scores up to 0.25, so the two are not compared on the same basis — the winner
would be selected partly for the coverage luck of its catalogue record rather
than for being the right paper. Dropping the abstract term to restore symmetry
discards a quarter of the scoring signal from every binding, including the
majority that never needed a rescue.

**R3 — Resolve before V5, for every candidate.** This is `docs/02-pseudocode.md`
§A taken at face value, and it is the only option that is unambiguously
faithful to the written ordering. It loses on cost, and the margin is not
close.

`binding.max_candidates` is 25. With the ADR-006/007 chain at four hops beyond
discovery, the ceiling is 100 calls per news item and roughly 1,000 a day at
the ten items M1 measures. ADR-007 records the only hard quota in the project —
Springer Nature at 500 requests a day and 100 a minute — so the worst case is
twice the documented daily limit of a catalogue this pipeline depends on, and
the per-minute limit would be breached inside a single item. §8 also constrains
the pipeline to be polite to key-free public APIs; issuing 100 DOI lookups to
resolve one news item is not that. The bound in Decision 1 exists precisely to
convert this ceiling from a function of `max_candidates` into a function of a
number someone chose.

**R4 — Resolve lazily in the verification stage, when a claim first needs a
substrate.** Attractive because it spends nothing on items that never reach
verification. It is forbidden by the type system, and that is the correct
outcome rather than an obstacle. ADR-001 makes `Bound` carry a non-empty
abstract by construction and `Bound.__post_init__` raises on a blank one, so a
binding cannot be returned in a state where an abstract is still outstanding.
Deferring resolution past `bind` therefore requires reintroducing exactly the
representable-but-invalid state — bound, no abstract — that produced the
spike's four self-verified items. The lazy placement is unavailable because
D2 deliberately removed the state it needs.

## Consequences

**Good.** ADR-006's stated benefit becomes reachable for the first time: a
candidate whose abstract lives in a catalogue other than the one that found it
can now enter scoring instead of being discarded. The abstract term keeps its
0.25 of the weight and is applied to every candidate on the same basis, because
resolution happens before the score rather than after it. The pipeline's
exposure to a single catalogue's coverage or uptime drops at the point where
ADR-006 always meant it to.

**Cost, stated as a ceiling rather than an average.** The worst case is
`resolve_top_n × 4` calls per news item, all on the miss path. Against M1's ten
items a day, an N of 5 gives a ceiling of 200 calls a day spread over five
catalogues — inside Springer's 500-a-day limit with room for the backfill and
replay traffic ADR-007 warns about, and inside the per-minute limit given the
existing one-second politeness delay. The average will be far below the
ceiling, because the walk stops at the first hit and most candidates arrive
with an abstract already attached.

**The gate feels this, not only the binder.** Four of the seven limitation
rules declare `requires_abstract`, so an item bound without one reaches
`evidence_completeness` of 3/7 = 0.43 and no higher, against G4's floor of 0.6.
A rescued abstract therefore does not merely let an item bind — it is the
difference between an item that can clear G4 and one that structurally cannot.
Placing resolution before V5 moves candidates across that line; placing it
after selection cannot.

**The binder gains a network dependency in its middle.** `bind_item` previously
made one or two calls and then computed; it now makes calls between two
computation steps, which lengthens the stage and makes its duration depend on
how many blanks the discovery catalogue returned. Every hop degrades to a miss
rather than an error, so a catalogue outage costs latency and yield but never
correctness — the run still completes and the item still reports honestly.

**Two ranks are computed where there was one.** The pre-resolution rank is
throwaway and must never be written to the ledger or compared against a
threshold, or a reader will find two different scores for the same candidate
and no way to tell which one decided anything. Only the post-resolution score
is the score.

**A new kind of unbound reason.** An item may now fail to bind because its
rescuable candidates fell outside the top N, which is a budget outcome rather
than an evidence outcome. The reason string has to say so, for the same reason
ADR-007 insisted a licence refusal must not read as a coverage failure: an
operator who reads "no valid candidate" and goes looking for a missing paper,
when the answer is that N is too small, is debugging the wrong system.

## Open decisions

**OD-1 — The value of `resolve_top_n`.** The measurement below bounds it from
one side but does not fix it. The number that settles it is the rank, in the
abstract-free ordering, at which the eventual winner sits — measured over
enough items to be more than an anecdote. Until that exists, 5 is a defensible
starting value and is explicitly provisional.

**OD-2 — Whether a rescued abstract should be cached across runs.** The chain
is idempotent and DOIs are stable, so a store-backed cache would cut the
steady-state call count sharply on replay and backfill. It is deliberately not
decided here, because ADR-007 §3 forbids retaining non-OA text at all and a
cache that cannot distinguish OA from non-OA entries would breach that. Whoever
takes this must design the cache around the licence flag, not add the flag to a
cache.

**OD-3 — Whether Springer should be consulted first for all DOIs or only for
`10.1038/` and `10.1007/`.** ADR-007 decided the prefix rule on plausibility,
not measurement. The hop-order question is only worth reopening if the
measurement shows Springer rescuing DOIs outside those prefixes often enough to
justify moving it earlier for everyone; it is unmeasured here because the probe
ran without the keys.

**OD-4 — Whether the pre-resolution rank should use a renormalised three-term
score or simply title overlap.** Decision 1 specifies renormalisation because
it reuses a formula that already exists. Title overlap alone would be simpler
and, given that it already carries 0.55 of the weight, might select the same
prefix. Not worth a decision until someone has both orderings over real items.
