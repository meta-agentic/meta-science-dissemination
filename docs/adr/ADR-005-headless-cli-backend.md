# ADR-005 — Reasoning via the local `claude` CLI; no secret on disk

Date: 2026-08-03
Status: accepted

## Context

The pipeline runs unattended from launchd and needs a model for two stages
(ADR-004). The machine was probed on 2026-08-03: no `ANTHROPIC_API_KEY` is set,
ollama is installed but has no models pulled, and the `claude` CLI (2.1.220) is
present and authenticated (M8).

Introducing an API key would mean a long-lived secret in a plist, a shell
profile or an env file, on a machine whose repositories are published publicly.

## Decision

Shell out to `claude -p` in headless mode, reusing the CLI's existing
authentication. The subprocess boundary is the integration: prompt in on argv,
text out on stdout, non-zero exit treated as unavailability.

The `--model` flag is set from `config/pipeline.yaml` so the reasoning model is
configuration, not a constant buried in code.

## Consequences

**Good.** No secret is stored for the scheduled job, which matters more than
usual here because the repository is public (NFR-5). The pipeline uses the
subscription already paid for, and the model can be changed in config without
touching code.

**Cost.** Coupling to a CLI's interface, which can change between versions. The
flags used are the stable, documented headless ones (`-p`, `--model`,
`--append-system-prompt`), all verified present in 2.1.220. Startup overhead
per invocation is real but irrelevant at ten items a day.

**Failure behaviour.** If the CLI is missing, unauthenticated or times out, the
stage raises `LLMUnavailable`, claims become `unverifiable`, and every item
routes to review. The pipeline degrades into a triage tool that still fetches,
binds, corroborates and flags — it never degrades into one that publishes
unverified posts. This is INV-2 at the integration boundary.

**Portability note.** Under launchd the job inherits a minimal environment, so
the generated plist must set an explicit `PATH` that includes the CLI's
location. This is a known sharp edge of the decision and is handled in the
schedule generator rather than left to be rediscovered.
