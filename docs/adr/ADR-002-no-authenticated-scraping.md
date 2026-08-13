# ADR-002 — Metadata only; never authenticate to a publisher

Date: 2026-08-03
Status: accepted

## Context

The user holds a personal science.org subscription and asked for scraping. A
credentialed scraper would yield full article text, which is genuinely the
richest input the pipeline could have.

It would also breach the AAAS terms of use, and subscription publishers detect
automated access readily — the realistic outcome is a terminated account, on
the very subscription the project exists to exploit. The legal objection and
the practical objection point the same way.

## Decision

Read publisher RSS only. Never send credentials, never request an article body,
never store a session cookie. The pipeline reasons over titles, standfirsts,
DOIs and dates, plus abstracts obtained from open catalogues (OpenAlex,
Crossref) that publish them for exactly this purpose.

The paywalled article is read by the human, in a browser, as a person.

## Consequences

**Good.** No terms-of-use exposure and no account risk. As it turns out, no
capability is lost either: the pipeline's job is to decide *which* story
deserves a human's attention and whether its numbers hold up, and the abstract
is the right substrate for both — it is the peer-reviewed claim, where the news
body is a journalist's rendering of it.

**Cost.** Roughly 40 words of Science's own text per item. Binding must be
inferred rather than read from the article's own link to the study (M2), which
is the source of most of the system's complexity.

**Consequence for the product.** The output is a decision aid, not a
replacement for reading. That is the honest framing and the one the README
states.
