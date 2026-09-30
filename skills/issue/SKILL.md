---
name: issue
description: "Run a continuous, provider-neutral Issue Agent for /issue: understand, investigate, reconcile evidence, compose a complete report, and confirm it without requiring a user-visible Draft or ZaoFu runtime."
---

# Continuous Issue Agent

This skill is host- and project-neutral. It can run in a local project,
standalone CLI, Web host, Codex session, or CI worker. Host adapters provide
the project root, authorized attachments, persistence, provider execution, and
optional publication; the portable Core must not import or assume ZaoFu.

## User flow

1. Treat `/issue <description>` as one complete raw report. Preserve the exact
   text and attachment manifest; never replace the raw report with a Draft.
2. Create one persistent Issue Session and run one continuous Issue Agent. Its
   internal phases are `understand → investigate → reconcile → gap_check →
   synthesize → validate`; these are model responsibilities inside one
   provider conversation, not separate user-visible agents or a Draft handoff.
3. Use bounded, read-only tools to inspect authorized files, logs, attachments,
   and controlled commands. Always compare Raw Report, Working Set, and
   Evidence; an initial interpretation is only a candidate.
4. After each investigation, run a gap check. If the Agent can obtain a fact
   from code, tests, logs, read-only commands, or browser evidence, continue
   investigating. If the fact is genuine user intent and cannot be inferred,
   ask all unresolved questions in one batch; store the answer as a new
   reported claim and continue the same Session.
5. Validation returns a routable result, never only a boolean:
   `missing_evidence → investigation`, `missing_user_intent → ask_user`,
   `conflicting_evidence → reconcile`, and `malformed/incomplete_body →
   re_synthesize`. Only a passing validation may enter confirmation.
6. Show progress, questions, provenance, evidence, conflicts, limitations,
   and the final Issue Report. A Working Set/Draft may exist internally for
   resume and CAS, but it is not a required user-facing step.
7. Only after the user confirms the final report create an immutable
   Publication Snapshot bound to Session revision, Working Set/Report/Evidence
   digests, attachment manifest digest, confirmation actor/time, provider, and
   target. Publication is a separate host-side side effect.

## Contracts and boundaries

- Use versioned, provider-neutral Session, Working Set, Evidence/Claim,
  provider, and Publication Snapshot contracts. Every turn/reply increments
  the Session revision and invalidates stale results.
- Tool calls are read-only and bounded. Path escape, unsupported tools,
  timeout, cancellation, invalid provider output, and stale revision fail
  closed while preserving the Session and collected Evidence.
- A provider adapter may be a model API, Codex/Claude bridge, or JSON
  subprocess. It translates protocol messages; it does not own Issue state,
  permissions, or publication rules.
- The Agent never edits source files, mutates canonical host stores, writes a
  remote Issue, or changes labels/PRs/merges/deployments during investigation.
- Preserve Raw Report, Reported/Observed/Inferred Claims and Evidence as
  separate provenance layers. Do not silently overwrite a user claim with an
  inference; represent disagreement as Conflict or Suggestion.
- Attachments remain private until the user chooses disclosure. Unknown
  provenance, stale evidence, private attachments, and unconfirmed inference
  must not enter a public publication payload.

## Host boundary

ZaoFu, Web, CLI, Codex, and CI are optional hosts. They may start, query,
resume, cancel, and project a portable worker, but the portable Core must run
without ZaoFu and must not import `zf.*`. `waiting_for_runtime` is never a
required `/issue` terminal state; an unavailable host returns a retryable
`worker_unavailable` result while preserving the Session.

## Empty `/issue`

An empty `/issue` still creates a Session. The Agent may ask the reporter for
missing information, but it must not open a fixed questionnaire implicitly.
Hosts may retain a historical Draft/Intake flow only behind an explicit
legacy-only command or API; the public `/issue` entry point never selects it.

## Compatibility resources

Legacy Draft schemas and the historical host alias remain available only for
data migration and old API reads. They must not define a second public `/issue`
workflow. See `references/draft-and-provider-contract.md` when migrating old
Draft/Evidence data into a Working Set.
