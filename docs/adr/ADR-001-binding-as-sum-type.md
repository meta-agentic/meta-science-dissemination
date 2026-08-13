# ADR-001 — Binding is a sum type, and `Unbound` has no abstract

Date: 2026-08-03
Status: accepted

## Context

The spike represented a binding as one dictionary with a `status` string and an
`abstract` key that was `""` when nothing was found. Verification code read
`binding["abstract"]`, got an empty string, and cheerfully compared claims
against the news text instead — reporting them verified.

The bug was not a missing check. It was that the illegal state — *"bound, but
with no evidence text"* — was representable, and every consumer had to remember
to guard against it. Four consumers, one forgot, and the system published
circular verification with full confidence.

## Decision

Model the binding as a sum type:

- `Bound` and `Weak` carry a **non-empty** `abstract`, enforced in
  `__post_init__`.
- `Unbound` has **no `abstract` attribute at all**.

Validity rule V5 (a candidate without an abstract is not a valid candidate) is
therefore guaranteed at construction, not by the search code remembering to
apply it.

## Consequences

**Good.** Code that reads `binding.abstract` on an unbound item raises
`AttributeError` during development rather than silently verifying nothing. The
spike's central defect becomes a crash — the cheapest possible failure. The
type also documents the invariant better than a comment could.

**Cost.** Consumers must branch on the binding variant. This is the point: the
branch is exactly the editorial decision "do we have evidence or not", and
forcing it to be written down is the benefit, not the tax.

**Rejected alternative.** Keep the dictionary and add a `has_abstract()` guard
at each call site. This is what the spike effectively did, and it failed for
the reason such schemes always fail — it relies on every future call site
remembering. There is no reason to believe the fifth consumer will be more
careful than the fourth.
