# Phase 3 — Architecture

Status: complete, QA gate passed
Depends on: `01-specification.md`, `02-pseudocode.md`

## 1. Shape

A linear pipeline over a single SQLite store. No services, no queue, no
daemon: one user, one machine, one daily run. Concurrency would add failure
modes and buy nothing at ten items a day.

```
   RSS ──▶ fetch ──▶ [ items ]
                        │
                        ▼
                      bind ──────▶ OpenAlex / Crossref
                        │           (validity filter, then score)
                        ▼
                   Binding{Bound|Weak|Unbound}
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  corroborate       claims           assess
  (independence)  (propose→verify)  (3-valued)
        │               │               │
        └───────────────┼───────────────┘
                        ▼
                      gate  ──── blocked ──▶ drafts/<date>/_review/
                        │
                     passed
                        ▼
                      draft ──▶ drafts/<date>/<slug>.md
```

Every arrow crossing a module boundary carries a typed value, and every
decision is written to the store before the next stage reads it. That is what
makes NFR-2 (independent, idempotent stages) and SC-5 (offline replay) hold.

## 2. Modules and contracts

| Module | Owns | Contract | Network | Model |
|---|---|---|---|---|
| `config` | thresholds, source registry | validates on load; bad config is fatal | – | – |
| `textutil` | similarity, numeric literals, slugs | pure functions, total | – | – |
| `store` | persistence, the ledger | typed rows in, typed rows out | – | – |
| `fetch` | RSS ingestion | `Source → [Item]`, failures reported not raised | yes | – |
| `bind` | primary-source identification | `Item → Binding` | yes | – |
| `corroborate` | cross-source matching | `Item → Corroboration` | – (reads store) | – |
| `claims` | claim extraction and verdicts | `(Item, Binding) → [Verdict]` | – | **propose only** |
| `assess` | limitation rules | `(Item, Binding) → Assessment` | – | – |
| `gate` | publishability | `(...) → Decision` | – | – |
| `draft` | Italian prose, markdown | `(Item, Analysis) → Path` | – | **prose only** |
| `pipeline` | stage orchestration | idempotent stage runners | – | – |
| `cli` | entry point | argument validation | – | – |

The model appears in exactly two cells. Everything that decides *truth* is in
a cell where it does not appear. That is the architecture, stated as a table.

## 3. Making INV-1 unrepresentable

The spike enforced provenance separation by discipline, and discipline lost.
The fix is to make the illegal state unconstructible.

```python
@dataclass(frozen=True)
class Bound:
    candidate: Candidate
    abstract: str          # non-empty, enforced in __post_init__
    score: float

@dataclass(frozen=True)
class Weak(Bound):
    """Identified, but below confidence. Still carries an abstract."""

@dataclass(frozen=True)
class Unbound:
    reason: str
    rejected: tuple[Rejection, ...]
    # deliberately has NO `abstract` attribute at all

Binding = Bound | Weak | Unbound
```

`Unbound` does not have an `abstract` field. Verification code reaches for
`binding.abstract` and, on an unbound item, gets an `AttributeError` at
development time rather than an empty string that silently verifies a claim
against nothing. The spike's central bug becomes a crash instead of a
publication.

`Bound.__post_init__` rejects a blank abstract, so validity rule V5 is
guaranteed at the type boundary rather than trusted to the search code.

## 4. ADR index

| ADR | Decision |
|---|---|
| [ADR-001](adr/ADR-001-binding-as-sum-type.md) | Binding is a sum type; `Unbound` has no abstract field |
| [ADR-002](adr/ADR-002-no-authenticated-scraping.md) | Metadata only; never authenticate to a publisher |
| [ADR-003](adr/ADR-003-independent-corroboration.md) | Widen the independent pool to 8; unbound is unpublishable by construction |
| [ADR-004](adr/ADR-004-model-proposes-code-verifies.md) | The model proposes claims and writes prose; nothing else |
| [ADR-005](adr/ADR-005-headless-cli-backend.md) | Reasoning via the local `claude` CLI; no secret on disk |

## 5. Data flow and the ledger

One `Analysis` row per item, holding `binding`, `corroboration`, `claims`,
`assessment`, `gate` as JSON. Written once, read by drafting and by `sci show`.

The ledger is append-only in effect: re-analysis overwrites the row, but the
`runs` table records every execution with its stage, timing and outcome. A
draft published in March can be explained in June by reading the row — with no
network access, satisfying SC-5.

## 6. Failure policy

| Failure | Response | Rationale |
|---|---|---|
| One feed unreachable | Log, continue, record in run detail | Partial data is useful; a dead feed must be visible, not silent (M4) |
| All feeds unreachable | Stage fails, non-zero exit | Nothing to do |
| OpenAlex and Crossref both silent | `Unbound` with reason | Indistinguishable from "no such paper" at the editorial level, but the reason string preserves the distinction |
| `claude` CLI missing or unauthenticated | Claims become `unverifiable`; all items route to review | Fail closed, INV-2. The pipeline degrades to a triage tool rather than producing unverified posts |
| Model returns malformed JSON | One reparse attempt, then `unverifiable` | Never guess at claim content |
| Draft generation fails | Item stays undrafted, error recorded | Idempotent — the next run retries it |

Every row is a form of INV-2: no failure path leads to publication.

## 7. Directory layout

```
sci/
├── config/          sources.yaml, pipeline.yaml     — every threshold
├── docs/            01..03, adr/                    — this methodology trail
├── src/sci/         modules from §2                 — none over 500 lines
├── tests/           unit + the SC-1..SC-3 regressions
├── scripts/         launchd generator (gitignored output)
├── data/            sci.db                          — gitignored
└── drafts/          YYYY-MM-DD/*.md, _review/*.md   — gitignored
```

## 8. Deltas from the spike

| # | Change | Traces to |
|---|---|---|
| D1 | Candidate validity filter (V1–V5) before scoring | M3, SC-1 |
| D2 | `Binding` becomes a sum type; `Unbound` has no abstract | INV-1, ADR-001 |
| D3 | Verification substrate is the abstract only, never the news text | INV-1, SC-2 |
| D4 | New `unverifiable` status, distinct from `unsupported` | §C |
| D5 | Rules return fired/clear/not-assessable | INV-2 |
| D6 | `evidence_completeness` reported and gated (G4) | INV-2, SC-3 |
| D7 | Independent pool 2 → 8 sources | OQ-1, ADR-003 |
| D8 | Rejected claim text withheld from the draft prompt | §F |
| D9 | Rejected binding candidates retained in the ledger | SC-4 |

D1–D3 are the correctness fixes. D4–D6 are the honesty fixes: they stop the
system reporting confidence it has not earned.

## QA gate — Phase 3

| Check | Result |
|---|---|
| Every FR maps to an owning module | Pass — §2 |
| INV-1 enforced structurally, not by convention | Pass — §3, ADR-001 |
| INV-2 holds on every failure path | Pass — §6, all rows fail closed |
| OQ-1 resolved with measured data | Pass — ADR-003, 6 feeds probed live |
| Each delta traces to a measurement or an invariant | Pass — §8 |
| No module exceeds 500 lines as designed | Pass — largest is `store` at ~330 |
| Model confined to propose/prose | Pass — §2 table, two cells |

Proceed to Phase 4 (Refinement): implement D1–D9 test-first, with SC-1..SC-3
as the regression suite.
