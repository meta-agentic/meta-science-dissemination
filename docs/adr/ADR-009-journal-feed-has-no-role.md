# ADR-009 — A journal table-of-contents feed has no role in the source model

Date: 2026-09-25
Status: accepted
Composes with: ADR-003 (independence), ADR-006 (abstract chain), ADR-008 (where the chain runs)
Leaves open: `docs/01-specification.md` OQ-2 (non-Science primary feeds), unchanged

## Context

`config/sources.yaml` has two roles. `primary` feeds are the news the pipeline
writes about. `corroborators` are other newsrooms whose coverage counts as
confirmation, weighted by `independence`. The proposal was to ingest the
Science Advances feed "with abstracts intact", on the claim that it overlaps
the news desk's coverage more than any other journal. The proposal named no
consumer, and neither existing role fits a journal feed.

- **As a corroborator it would corrupt the gate.** `corroborate_item` reads a
  match as evidence that another newsroom checked the story. A paper matching
  the news item about that paper is the primary source, not independent
  confirmation of it. Registering it with `independence >= 0.5` would let a
  study corroborate the story written about that study. That is the circularity
  INV-1 exists to prevent, moved from claim verification to G2.
- **As a primary feed it would be out of scope.** Specification §9 excludes
  non-Science primary feeds, and OQ-2 defers the question on purpose.

Two new roles were plausible:

1. **A fifth catalogue for binding.** Feed items become locally held binding
   candidates, matched by title and DOI inside `bind`.
2. **A pre-seeded abstract cache.** Feed abstracts are keyed by DOI and
   consulted by the ADR-006 chain before any HTTP call.

Both roles depend on two things nobody had measured: what the feed carries and
how often it holds the paper a news item is about.

## The measurement

The feed was probed live on 2026-09-25 with the pipeline's own User-Agent and
parsed with `fetch.parse_feed`.

| # | Observation | Figure |
|---|---|---|
| J1 | Issue feed (`showFeed?type=etoc&jc=sciadv`) | 45 items, one issue, one cover date |
| J2 | Ahead-of-print feed (`type=axatoc`) | 0 items |
| J3 | Text in each item's `summary`/`content` | 55 characters of issue boilerplate ("Science Advances, Volume 12, Issue 39, September 2026."). **No abstract.** |
| J4 | `prism:doi` | The study's own DOI (`10.1126/sciadv.*`) on 45 of 45 items |
| J5 | Those 45 DOIs already in OpenAlex, with an abstract | 45 of 45, within two days of the cover date |
| J6 | Science Advances papers in OpenAlex over the 12-week window, with an abstract | 1,170 of 1,182 (99.0%) |

The overlap was then measured in two ways.

**Current window.** The ten items in the live Science news feed were each
compared with all 45 feed items using the binder's own `similarity`. The best
title similarity was 0.148, against 0.40 needed for a title alone to reach the
weak threshold. **None of the ten items has a matching feed item.** The binder
itself produced no Science Advances winner.

**Twelve weeks back.** Science news items are indexed in OpenAlex under
`AAAS Articles DO Group` (M3). Between 2026-07-02 and 2026-09-24 that source
held 321 works, of which 260 carry a news-shaped DOI (V3's pattern). A seeded
random sample of 120 was taken. For each item, the simulated feed was the
Science Advances papers published in the seven days before it, which is the
depth one weekly issue gives. The median was 91 papers per item. Each item was
scored against every paper in its window with `_score`. Feed candidates were
given their OpenAlex abstract, which overstates what the real feed provides
(J3). Each item was also run through the real `bind_item`.

| Result over 120 news items | Count |
|---|---|
| Binder winner is a Science Advances paper | 0 |
| A same-window feed paper clears the weak threshold (0.22) | 68 |
| A same-window feed paper clears the bound threshold (0.38) | 1 |
| Feed paper that is, on reading, the study the item reports | **1** |
| ... and would have won the binding had the feed been a catalogue | 0 |

The 68 figure measures noise, not overlap. `venue_boost` lists
"science advances", and every paper in a seven-day window earns most of the
proximity term, so a feed candidate starts at about 0.20 before any text
matches. That is 0.02 below the weak threshold, and incidental word overlap
closes the gap. Reading all 68 pairs finds one real match: a news item about
sheeppox DNA in medieval parchment and the Science Advances paper on
sheeppox virus evolution, which scored 0.374. The binder had already bound
that item, wrongly, at 0.79 to a blog post that repeats the headline. So the
feed paper would have lost even as a candidate.

The history sample uses titles only, because OpenAlex does not store the dek.
This lowers every similarity a little. It does not change the finding, because
the feed adds no text of its own for matching.

The claim of "by far the highest overlap" is therefore refuted as far as it
matters here. The measured overlap is about 1 in 120, and the binder already
reaches those papers through OpenAlex.

## Decision

**1. The source model does not get a third role. The Science Advances feed is
not registered.** The question is closed for this feed, and the measurement
above is the reason.

**2. Option 2, the pre-seeded abstract cache, is void, not deferred.** The feed
carries no abstract (J3). The request to ingest it "with abstracts intact"
assumed a field the feed does not have. A cache needs something to put in it.

**3. Option 1, the fifth catalogue, is rejected on evidence.** It would add
nothing and would damage binding.

- *Nothing to add.* Every feed DOI is already in OpenAlex with an abstract
  (J5, J6). The feed's only unique asset, a study DOI known at ingestion (J4),
  duplicates what discovery already returns. In the one real match, the
  binder's miss was a discovery and validity failure, not a coverage gap.
- *Actively harmful.* Each news item would gain about 90 local candidates, each
  starting near 0.20 on proximity and venue alone. Under ADR-008 these enter at
  "discover" and are ranked by the renormalised three-term score. There,
  proximity and venue carry 0.20 / 0.75 = 0.27 of the weight, so same-week feed
  papers would crowd genuine candidates out of the `resolve_top_n` prefix.
  They would spend ADR-008's call budget on papers nobody wrote about.

**4. Whatever a journal feed becomes later, it never feeds corroboration.** This
constraint binds any future proposal, not only this one:

- A journal source is a **list of study DOIs**. It is a partial local view of a
  catalogue, not a newsroom, and `independence` has no meaning for it.
- It is told apart **by role, not by value**. It would sit under its own
  top-level key in `sources.yaml`, never under `corroborators` with some chosen
  `independence`. A value can be edited into a range that counts. A role cannot.
- Its only admissible consumer is **binding's discovery step**, at ADR-008's
  "discover" position before V1–V4. Its items pass the same validity rules and
  receive abstracts only through the ADR-006/ADR-008 chain.
- It **never contributes to `independent_count`** or `echo_count`, and
  `corroborate_item` never reads it. The `independence` semantics of ADR-003
  stay unchanged: journal items are not newsrooms.

## Consequences

**Good.** Nothing is added to fetch, store, bind or config, so no failure mode
is added either. The corroboration pool keeps the meaning ADR-003 gave it. A
request that rested on an unmeasured claim now rests on a figure. Anyone who
wants to reopen it has to beat that figure, not repeat the claim.

**Accepted cost.** The pipeline gives up a study-DOI hint for Science Advances
papers. On this measurement that hint would have changed no binding in 120.

**What would reopen this.** Any one of these observations:

- OpenAlex coverage of new Science Advances papers drops, measured as J5 falling
  well below 45 of 45 within a week of publication.
- The feed starts to carry abstracts, measured as J3 changing.
- A measured sample shows feed papers winning bindings that discovery misses,
  at a rate that justifies the candidate flood described in Decision 3.

In any of these cases, Decision 4 fixes the shape of the answer in advance.

**Not decided here.** OQ-2, whether journals such as Nature or Cell become
*primary* feeds that the pipeline writes about, is a separate editorial
question and stays deferred.

## Observed in passing

The measurement exposed two defects in binding. They are outside this
decision and are recorded so they are not lost.

**O1 — The non-textual floor sits just under the weak threshold.** Proximity
(0.12) and venue (0.08) together give a same-week paper in a boosted venue
0.20, against a weak threshold of 0.22. `_score`'s docstring says these terms
are tie-breakers that must never carry an unrelated paper over the threshold.
Combined, they very nearly do. Of the 20 `weak` bindings in the sample, most
are visibly unrelated to their news item. They include a paper in *Pakistan
BioMedical Journal* bound to a story about Dutch media fines.

**O2 — A blog post repeating a news headline passes V1–V4 and binds.** The
parchment item bound at 0.79 to a `posted-content` record whose title is the
news headline with a prefix. OpenAlex search returned nothing for this item, so
the record reached the binder through the Crossref fallback. V4 admits
`posted-content` so that preprints can bind. This is M3's self-binding failure
wearing a different venue, and SC-1 as worded, which checks the AAAS container
and the item's own DOI, would not catch it.
