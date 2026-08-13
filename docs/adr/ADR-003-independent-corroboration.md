# ADR-003 — Widen the independent pool; unbound is unpublishable by construction

Date: 2026-08-03
Status: accepted
Resolves: OQ-1

## Context

The gate offers two routes to publication: the study is identified, or enough
independent newsrooms have confirmed the story. The spike configured the second
route as "≥2 independent corroborators" while registering only two independent
sources — Nature and Ars Technica (M7). Both would have had to carry the same
story on the same day. The alternative route was decorative.

The tempting fix is to lower the threshold to 1. That trades a dead condition
for a weak one, and it does not address the actual problem: the corroboration
pool was too small to mean anything.

## Decision

**1. Widen the pool with measured sources.** Six further independent newsrooms
were probed live on 2026-08-03 and all returned parseable feeds:

| Source | Items | Independence |
|---|---|---|
| The Guardian — Science | 28 | 1.0 |
| BBC — Science & Environment | 42 | 1.0 |
| NYT — Science | 22 | 1.0 |
| STAT News | 20 | 1.0 |
| New Scientist — News | 10 | 1.0 |
| Quanta Magazine | 5 | 1.0 |

Independent sources go from 2 to 8. `nature.com/subjects/news.rss` returned 404
and is not registered — a dead feed silently depresses every corroboration
score, and that is how M4 was missed the first time.

**2. Keep the requirement at 2 independent outlets.** With eight sources the
condition is now reachable, so it can stay strict.

**3. State the real guarantee: `Unbound` is unpublishable by construction, not
by threshold.** An unbound item carries no abstract (ADR-001), so no claim can
be verified (INV-1), so the gate's verified-claim condition blocks it
regardless of how many outlets carry the story. Corroboration therefore
functions as a **strengthener of a weak binding**, never as a substitute for
evidence.

The publication routes reduce to:

| Binding | Independent outlets | Publishable |
|---|---|---|
| `Bound` | any | yes, if claims verify and checks run |
| `Weak` | ≥2 | yes, with the binding score disclosed |
| `Weak` | <2 | review |
| `Unbound` | any | **never** |

## Consequences

**Good.** The alternative route is real rather than nominal. The strongest
guarantee — no evidence, no post — now follows from the type system rather than
from a number in a config file that a future edit could weaken.

**Cost.** Eight more feeds per run: about eight seconds of extra fetching, and
more cross-outlet title matching, which is noisy. Mitigated by the date window
and the similarity floor, both configurable.

**Also resolves OQ-3.** A `weak` binding may publish, because V5 guarantees it
carries an abstract, so INV-1 is satisfied and claims verify against real
primary text. The binding score is disclosed in the draft's frontmatter so the
weaker identification travels with the post.
