# sci

Turns science news into Italian post drafts that are bound to their evidence.

The pipeline reads publisher RSS, works out which paper each story is actually
about, checks whether anyone independent has confirmed it, extracts the factual
claims, verifies each one against the source text, and only then writes a post
— attaching the full evidence ledger to it.

The invariant the whole design serves:

> **A model never decides that its own output is true.**

A model proposes claims and writes prose. Deterministic Python decides what is
verified. That split is why the output is auditable instead of merely fluent.

## What it does not do

It does not log in to any publisher, and it does not fetch article bodies.
Full text is paywalled; retrieving it with subscriber credentials would breach
the publisher's terms and put the account at risk. Headlines, standfirsts,
DOIs and dates are enough to decide what deserves a human read — and the
paywalled article is something you read yourself, in a browser, as a person.

## Pipeline

| Stage | What it decides | How |
|---|---|---|
| `fetch` | what is new | publisher RSS, polite delays, stable IDs |
| `bind` | which paper the story is about | OpenAlex then Crossref, scored, `bound` / `weak` / `unbound` |
| `corroborate` | who else is reporting it | title similarity in a date window, weighted by independence |
| `claims` | what is actually being asserted | model proposes, Python verifies |
| `hype` | what the story is not telling you | deterministic rules over abstract vs headline |
| `gate` | whether it may become a post | every blocker recorded |
| `draft` | the Italian post | verified claims only, limitations mandatory |

### Binding is inferred, not read

The Science news feed carries the DOI of the *news article*, never of the
underlying study. So the primary source has to be found: salient headline terms
are queried against OpenAlex within a date window around publication, and
candidates are scored on title overlap, abstract overlap, date proximity and
venue plausibility. Above `bound_threshold` the paper is considered identified;
between the thresholds it is `weak`; below, the item is `unbound` and carries no
abstract — so its quantity claims cannot be verified and it will not be
published. A confidently wrong paper is worse than an admitted gap.

### Corroboration counts independence, not outlets

Phys.org, ScienceDaily and MedicalXpress overwhelmingly republish institutional
press releases. Three hits there can be one press office speaking three times.
Each source therefore carries an `independence` weight in `config/sources.yaml`,
and matches are split into **independent** confirmation and **echo**. Only
independent confirmation counts toward the gate; echo-only coverage raises the
`press_release_only` flag.

### Claims are proposed by a model and verified by code

Numbers are the clearest case. The model extracts `"a 30% reduction"`; then
`number_supported()` checks that `30` literally occurs in the abstract or the
standfirst. If it does not, the claim is `unsupported` and never reaches the
draft. Causal claims are checked against the abstract's own language: if the
headline says *causes* where the abstract says *associated with*, the claim is
downgraded to `hedged` and the discrepancy is disclosed in the post.

### The gate explains itself

Nothing is silently dropped. An item that fails gets a note in
`drafts/<date>/_review/` listing exactly which conditions it missed — often the
more interesting editorial signal.

## Install

```bash
uv venv && uv pip install -e ".[dev]"
```

Reasoning stages shell out to the `claude` CLI in headless mode, so there is no
API key to store. Without it, the pipeline still fetches, binds, corroborates
and flags — it just routes everything to review instead of drafting.

## Use

```bash
sci status
```

```bash
sci run --limit 5
```

Individual stages, each idempotent and independently runnable:

```bash
sci fetch
```

```bash
sci analyse --limit 10
```

```bash
sci draft
```

Inspect the full evidence ledger behind any decision:

```bash
sci list
```

```bash
sci show science_news:abc123def456
```

## Output

Drafts land in `drafts/YYYY-MM-DD/<slug>.md`, each with frontmatter carrying the
bound DOI, binding score, corroborator counts, hype flags, per-claim verdicts and
the gate decision — followed by the Italian post and a verification log. Blocked
items land in `drafts/YYYY-MM-DD/_review/`.

## Configuration

`config/sources.yaml` — feeds and their independence weights.
`config/pipeline.yaml` — every threshold that decides what gets published,
in one place, defensible in public.

## Scheduling

`scripts/install-schedule.sh` generates and loads a launchd job that runs the
pipeline daily. The generated plist contains machine-specific absolute paths
and is gitignored.

## Tests

```bash
.venv/bin/python -m pytest tests -q
```

The tests cover the verification layer and the gate — the parts that decide
what gets published. The prose generator is not unit-tested, because its output
is constrained by what those functions let through.

## Licence

MIT.
