# ADR-007 — Springer Nature as a catalogue, under an open-access-only constraint

Date: 2026-09-02
Status: accepted
Amends: ADR-005 (which asserted no secret is stored)
Extends: ADR-006 (the abstract resolution chain)

## Context

Abstract availability is the binding constraint on everything this pipeline can
honestly publish: no abstract means no verification substrate, which means no
verified claim, which means no post.

Two measurements make Springer Nature the obvious next catalogue.

1. **The Nature-family feeds carry no abstract.** `nature.com/srep.rss` and
   `ncomms.rss` return 248 characters of boilerplate — "Published online: …
   doi:…" — and nothing else. Measured 2026-08-05. Under the validity rules
   those items are unbindable by construction.
2. **Nature is the most-covered journal in science journalism after Science
   itself.** The gap sits exactly where coverage concentrates, so it suppresses
   yield far more than its share of the corpus suggests.

Springer Nature publishes an official API — a metadata endpoint and an
open-access endpoint. Probed 2026-08-26: live, returning a well-formed 401
without a key. Unlike the situation in ADR-002 this is a sanctioned
programmatic interface, which is why this decision can stand while ADR-002 does.

## The constraint that shapes the decision

Reading the API agreements before integrating, rather than after, turned up a
term that governs the whole design.

The metadata agreement grants text-and-data-mining rights over **non-open-access
abstracts** and then removes the use this pipeline depends on:

> The rights granted … explicitly exclude any use in connection with generative
> artificial intelligence systems. [Users] may not use … the non OA Abstracts,
> any TDM Material or any TDM Output to develop, train, program, improve, and/or
> enrich any generative artificial intelligence systems.

This pipeline sends abstracts to a model for claim extraction and sends verified
claims to a model to write prose. That is use in connection with a generative
system, plainly. Non-OA abstracts are therefore unusable here — not awkward,
not a grey area: outside the granted rights.

Open-access content is governed separately and is not caught by that exclusion:

> Metadata and OA Abstracts are subject to a Creative Commons (CC) license …
> Any use of such Content is solely subject to the license terms of the
> applicable Creative Commons license.

CC licences impose attribution, not a use ban. So the split is clean: OA content
is usable with attribution, non-OA abstracts are not usable at all.

The gap this ADR set out to close happens to fall on the usable side. Scientific
Reports and Nature Communications — the two feeds measured as carrying no
abstract — are fully open access. Confirmed end-to-end on 2026-09-06: a
Scientific Reports DOI whose feed entry carried 248 characters of boilerplate
resolves through the metadata endpoint to an 875-character abstract flagged
`openaccess: true`.

## Decision

**1. Add Springer Nature to the abstract resolution chain**, consulted first for
DOIs under `10.1038/` and `10.1007/`, as a later fallback otherwise. It is a
catalogue, not a source: it resolves abstracts by DOI inside `bind`, never
appears in `config/sources.yaml`, and never contributes to `independent_count`.
A publisher's own metadata is not an independent newsroom confirming a story.

**2. Open-access content only, enforced in code.** An abstract may enter the
verification path only when it is **positively flagged** open access. This is a
fail-closed rule in the sense of INV-2: unknown or absent OA status is treated
as non-OA and rejected, exactly as a missing abstract is. The licence is not a
note in a README to be remembered by whoever edits `bind` next; it is a
condition on the value, checked where the value is constructed.

**3. Non-OA abstracts are never retained.** Not stored, not sent to a model, not
written to the evidence ledger, not held in a cache. A rejected candidate
records *that* it was rejected for licence reasons and its DOI — never its text.

**4. Attribution is a product obligation, not a courtesy.** CC-licensed
abstracts and full text carry attribution terms, so a draft resting on them must
name the source and its licence. The evidence ledger already carries the DOI,
venue and binding score; the licence identifier joins them.

**5. Amend ADR-005 honestly.** That decision claimed no secret is stored, true
while the only backend was an already-authenticated local CLI. It is no longer
true. The free tier issues a **separate key per API product** — verified
2026-09-06, the two values differ — so there are two credentials:
`SPRINGER_META_API_KEY` and `SPRINGER_OPENACCESS_API_KEY`, read from the
environment and never from a file in the repository. Absence of either is a
normal state, handled like a catalogue outage; no key value reaches a log
line, an error message, or the ledger.

Environment files carrying these keys must use **legal shell identifiers**.
A name containing a hyphen cannot be assigned by a POSIX shell, so sourcing
the file executes the line instead and prints the secret to stderr. That is a
standing leak into any log or CI transcript that sources it, not a one-off.

**6. The agreement is entered by an individual**, acting for purposes relating
to their own trade or profession — the first limb the agreement offers. No
institution is represented, because none has authorised anyone to bind it.

## Consequences

**Good.** The measured gap closes: the two Nature-family feeds carrying no
abstract are both fully open access, so their items become bindable and
therefore verifiable. The chain gains a fifth source, further loosening the
single-provider dependency ADR-006 exists to break.

**Cost, stated plainly.** The flagship *Nature* is hybrid, so a large share of
Nature papers are non-OA and remain unusable — the yield gain is real but
narrower than "Springer covers Nature" suggests. Rate limits are 500 requests a day and 100 a
minute. Ample at roughly ten news items a day, but the budget is per *request*,
not per item: the chain may query several candidates per item, so the limit is
worth counting against before any backfill or replay over stored items.

**Non-OA full text is not merely disallowed, it is unavailable.** The paid TDM
product is the only route to it. The free tier offers exactly the two endpoints
this decision permits, so the licence boundary and the access boundary coincide
— there is no configuration in which the pipeline could reach content it has no
right to use.

**A credential now exists** in the operating environment, with the attendant
risk of leaking into a log or a public commit. Mitigated by reading it from the
environment only and never rendering it, but the risk is the price of the
coverage. The scheduled job inherits a minimal environment and must be given the
keys explicitly; its generated job file is ignored by version control precisely
because it will carry them.

**A new class of gate.** Until now every check answered "is this true?". This
one answers "are we permitted to use this?" — and it can reject an abstract that
is present, correct and perfectly verifiable. That is a genuinely different kind
of rejection and the review note must say so, or an operator will read a licence
refusal as a coverage failure and go looking for a bug that does not exist.

**Deliberately deferred, and harder than it looked.** Whether open-access
*full text* should replace the abstract as the verification substrate is not
settled here. Abstracts systematically omit what the limitation rules most need
— sample size, control design and stated limitations live in Methods and
Results — so full text would materially strengthen verification.

An earlier draft of this ADR assumed the open-access endpoint made that spike
cheap. Measured 2026-09-06 against a Scientific Reports article, it does not:
neither the JSON nor the JATS response carries a `<body>` element. Both return
metadata and the abstract only. Whether that holds across the corpus is
unmeasured — it may vary by article or need a parameter not yet found — but the
spike must establish that full text is actually retrievable before assuming a
richer substrate is available at all.
