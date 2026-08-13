# ADR-004 — The model proposes claims and writes prose; nothing else

Date: 2026-08-03
Status: accepted

## Context

Two jobs in this pipeline genuinely need a language model: reading a headline
and an abstract to enumerate the factual claims being made, and writing fluent
Italian. Both are things models do well.

A third job looks similar and is not: deciding whether a claim is *true*. The
obvious implementation — ask the model to extract claims and label each one
verified or unverified — produces confident labels that correlate with
plausibility rather than with the source text. For a pipeline whose entire
value proposition is verification, that is not a limitation to work around; it
is the thing that must not happen.

## Decision

The model's output is treated as a **proposal**, never as a finding.

- **Extraction.** The model returns candidate claims with their numeric
  literals and the fragment each rests on. It is given no verdict field.
- **Verification.** Python decides. A quantity claim is verified only if its
  numbers appear literally in the abstract (`number_supported`). A causal claim
  is verified only if the abstract uses causal rather than correlational
  language. Everything else is checked by term overlap against the abstract.
- **Drafting.** The model receives only claims that already passed. It cannot
  promote a rejected claim, because the rejected text is never in its context
  (see §F of the pseudocode).

## Consequences

**Good.** Every verdict is reproducible offline from stored data, by functions
with unit tests — which is what makes the evidence ledger meaningful and SC-5
achievable. A hallucinated statistic cannot reach a post: it fails the literal
number check and is withheld from the drafting prompt.

**Cost.** Deterministic verification is blunt. Term overlap will reject a
correctly paraphrased claim whose wording diverges from the abstract, and the
causal/correlational regexes will miss constructions they were not written for.
The system is biased toward false negatives.

That bias is deliberate and is the correct direction for this product. A
rejected true claim costs one paragraph of a draft. An accepted false claim
costs the credibility the whole pipeline exists to protect.

**Rejected alternative.** An LLM-as-judge second pass over each claim. It would
catch the paraphrase cases, but it reintroduces exactly the failure this ADR
exists to prevent — a model assessing a model — and it cannot be replayed
offline. If recall becomes a real problem, the right move is better
deterministic matching (stemming, synonym sets), not a model with a verdict
field.
