# Phase 1 — Specification

Status: complete, QA gate passed
Inputs: intake decisions (2026-08-03) + measurements from spike `v0.1.0`

## 1. Problem

A subscriber to Science/AAAS wants to publish Italian-language science posts.
The scarce resource is not text generation — it is **editorial confidence**:
knowing which of the day's stories is worth a human's reading time, and knowing
that every number in a published post is one the underlying study actually
reports.

The system therefore optimises for **defensible output**, not throughput. Ten
posts a day with unverifiable numbers is a failure. Two posts a day, each
bound to a paper with a verification log, is a success.

## 2. Users and use

Single user, single machine, daily cadence. Output is markdown drafts reviewed
by a human before publication. The system never publishes.

## 3. Measured facts (from the spike — not assumptions)

These were established by probing live sources on 2026-08-03. They constrain
the design and must be re-verified if the design is revisited.

| # | Fact | Consequence |
|---|---|---|
| M1 | `science.org/rss/news_current.xml` returns 10 items: title, one-line dek, news DOI, date, author, image. No body. | The article body is never available. All reasoning happens over ~40 words. |
| M2 | `prism:doi` in the news feed is the DOI **of the news article** (`10.1126/science.z*`), never of the study. | The primary source cannot be read off. It must be inferred and scored. |
| M3 | OpenAlex indexes Science's own news articles as works, under venue `AAAS Articles DO Group`, with **no abstract**. | A naive title search binds a news item **to itself**. Observed on 4/4 items. |
| M4 | EurekAlert returns 404 on every documented feed path. | Dropped. A silently dead feed biases corroboration downward. |
| M5 | Altmetric's public API returns 403 without a key. | Dropped. |
| M6 | Of five reachable corroborator feeds, three (phys.org, ScienceDaily, MedicalXpress) are press-release republishers. | Counting outlets overstates confirmation. Independence must be weighted. |
| M7 | Only two configured sources have independent newsrooms (Nature, Ars Technica). | A gate requiring 2 independent corroborators is effectively unreachable. See OQ-1. |
| M8 | The `claude` CLI is present and authenticated; no API key is on the machine. | Reasoning shells out headlessly. No secret is stored for cron. |

## 4. The failure the spike exposed

The spike passed 4 of 4 items. It should have passed approximately zero.

Root cause chain:

1. Binding matched each news item to its own OpenAlex record (M3).
2. That record carries **no abstract**, so the binding produced no independent
   text — but still reported status `bound`.
3. Claims were extracted from the news text and then verified **against the
   news text**, since no abstract existed. One claim scored "100% of claim
   terms present in source text". It was compared with itself.
4. Hype rules compare the headline's language against the abstract's. With no
   abstract, no rule can fire, so `hype_score` was 0 — read by the gate as a
   clean bill of health.
5. The gate saw: bound primary, zero hype, verified claims. It passed
   everything.

The system was not merely wrong; it was **confidently wrong in the direction of
publishing**. Every defensive layer degraded to "approve" when evidence was
absent.

This yields the two invariants below, which are the specification's core.

## 5. Invariants

> **INV-1 — Provenance separation.**
> The text a claim is *extracted from* must never be the text it is *verified
> against*. Verification uses the primary-source abstract only. If no abstract
> was obtained, no claim may reach status `verified`.

> **INV-2 — Absence of evidence blocks.**
> Every check must fail closed. A rule that cannot run because its input is
> missing must register as *unknown and blocking*, never as *passed*. No
> configuration may make a missing input publishable.

Both are testable, and both are directly traceable to the observed failure.

## 6. Functional requirements

| ID | Requirement | Acceptance |
|---|---|---|
| FR-1 | Ingest configured RSS feeds without authenticating to any publisher. | No credential is read; no request targets an article body. |
| FR-2 | Identify the peer-reviewed study a news item refers to, or state that it could not. | Given a news item whose only OpenAlex match is its own record, binding returns `unbound`. |
| FR-3 | A binding is valid only if it yields non-empty abstract text. | `bound` with an empty abstract is unrepresentable — enforced by the type, not a check. |
| FR-4 | Distinguish independent reporting from press-release republication. | Three echo-only matches never satisfy the independent-corroboration condition. |
| FR-5 | Extract atomic claims and assign each a verdict by deterministic code. | A model's self-assessment is never read. Numbers are matched literally. |
| FR-6 | Detect limitation and overreach patterns (animal-only, small n, no control, causal overreach, preprint). | Each rule reports `fired` / `clear` / `not_assessable`, never a silent zero. |
| FR-7 | Gate publication and record every blocking reason. | A blocked item produces a review note listing all failed conditions, not the first. |
| FR-8 | Generate an Italian draft restricted to verified claims, with mandatory limitations. | Unsupported claim text is not present in the prompt sent to the generator. |
| FR-9 | Attach a replayable evidence ledger to every draft and every review note. | Ledger contains binding score and components, corroborators, per-claim verdict and reason, gate decision. |
| FR-10 | Run unattended on a daily schedule. | Non-zero exit on total failure; partial feed failure is tolerated and reported. |

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | No secret on disk. Reasoning uses the already-authenticated local CLI. |
| NFR-2 | Stages are idempotent and independently runnable; a threshold change replays over stored items without re-fetching. |
| NFR-3 | Every published threshold lives in `config/`, not in code. |
| NFR-4 | Deterministic stages must be reproducible offline from stored data. |
| NFR-5 | Public repository: no local paths, tracker keys or instance data in files or git metadata. |
| NFR-6 | Modules stay under 500 lines; input validated at every system boundary. |

## 8. Constraints

- **Legal.** No authenticated scraping of science.org. Full text is read by the
  human in a browser. The pipeline handles metadata only.
- **Politeness.** Public APIs (OpenAlex, Crossref) are unauthenticated and free;
  requests are rate-limited and carry a contact User-Agent.
- **Language.** Output is Italian; sources are English. Translation happens only
  after verification, never before — a claim is verified in its source language.

## 9. Out of scope

Auto-publishing; social scheduling; image generation; full-text ingestion;
multi-user operation; non-Science primary feeds (deferrable, see OQ-2).

## 10. Success criteria

| ID | Criterion | Measurement |
|---|---|---|
| SC-1 | No self-binding. | Over ≥20 live items, zero bindings to a `AAAS Articles DO Group` record or to the item's own DOI. |
| SC-2 | Verification is non-circular. | Every `verified` claim has `checked_against: abstract`; no claim is verified when abstract is absent. |
| SC-3 | The gate discriminates. | Over a live run, pass rate is strictly between 0% and 100%, and each block cites a specific condition. |
| SC-4 | Honest failure. | Items that cannot be bound appear in the review queue with the reason, rather than being dropped or passed. |
| SC-5 | Ledger replay. | Any published draft's verdicts can be recomputed from stored data with no network access. |

SC-1 through SC-3 are the regression tests for the spike's failure. They are
the QA gate on Phase 3.

## 11. Open questions

- **OQ-1 (blocking Architecture).** With only two independent corroborator
  sources (M7), the "2 independent outlets" alternative path to publication is
  effectively dead, leaving binding as the only route. Either widen the
  independent source set, or reduce the requirement to 1 independent outlet
  *plus* a weak binding. Decided in ADR-003.
- **OQ-2 (deferred).** Whether to add Nature/Cell/NEJM as *primary* feeds. Out
  of scope for v1; the intake chose Science news only.
- **OQ-3 (deferred).** Whether a `weak` binding should permit publication when
  its abstract is present and claims verify against it. Leaning yes, since
  INV-1 is satisfied; the binding score would be disclosed in the post.

## QA gate — Phase 1

| Check | Result |
|---|---|
| Every requirement is testable | Pass — each FR has an acceptance condition |
| The observed failure is addressed by a stated invariant | Pass — INV-1, INV-2 trace to §4 |
| Success criteria are measurable without a model's opinion | Pass — SC-1..SC-5 are all mechanical |
| Assumptions separated from measurements | Pass — §3 is probe data, dated and re-verifiable |
| Blocking unknowns identified | Pass — OQ-1 raised before Architecture |

Proceed to Phase 2.
