---
name: zf-browser-e2e-contract
description: "ZaoFu browser/E2E (Docker Playwright) command-shape and clean-checkout environment checklist for writer, review, verify, and operator roles. Use when creating or reviewing task_map verification, worker briefings, Playwright/browser smoke commands, or Docker mcp/playwright runs. Taxonomy and the structured report contract live in the verify-review skill — this skill defers to it."
---

# ZaoFu Browser E2E Contract

## Overview

Use this skill when a ZaoFu task includes browser, Playwright, or E2E
verification. This skill is deliberately narrow: it owns **Docker/Playwright
command shape and the clean-checkout environment checklist** only. Hard
correctness still belongs in `task_map` validation, workflow runtime, and
deterministic gates.

Division of labor (avoid dual-source drift):

- **This skill**: Docker/Playwright command shape, `--user` / host-networking /
  bounded-install environment rules, clean-checkout + pin-commit setup.
- **`verify-review` skill** (canonical: `yoke/verify-review/SKILL.md`): the
  runner-vs-product failure **taxonomy** and the **structured report contract**
  (coverage matrix, gap findings, evidence refs). When this skill mentions
  classification or report fields, `verify-review` is the source of truth —
  cite it, do not fork a second copy of the taxonomy here.

## Core Rules

- Respect `zf.yaml` and `project.state_dir`; do not hard-code `.zf`.
- Use Docker with `mcp/playwright:latest` for browser E2E unless the operator
  explicitly asks for host browsers.
- Start local API/UI services on `0.0.0.0` when a container must reach them.
- Use host networking for local service smoke tests.
- Run the container with the host UID/GID so mounted workdirs remain writable.
- Bound browser install commands with `timeout`; never leave
  `playwright install chromium` unbounded.
- Do not wrap an existing Docker Playwright command in another Docker command.
- Do not downgrade a runner/setup failure into product rework.

## Command Pattern

Use this shape for browser gates:

```bash
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$PWD:/work" -w /work \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  mcp/playwright:latest \
  -lc 'set -euo pipefail; timeout 180s ./packages/web-tui/node_modules/.bin/playwright install chromium; ./packages/web-tui/node_modules/.bin/playwright test --config packages/web-tui/playwright.config.ts'
```

Adapt paths to the current `task_map`; keep the runner flags intact.

> The `mcp/playwright:latest` image name, the `INSTALLATION_COMPLETE` marker
> referenced below, and this exact command shape are **skill-owned convention
> (no kernel validator)** — the kernel does not parse or enforce them; they are
> operator/agent discipline.

## Task Map Checklist

- Keep verification commands machine-readable. If `verification` is a list, each
  item must be a standalone command; never stringify a Python list into a
  contract field.
- Include static/package checks separately from browser E2E checks.
- Mark browser checks with the canonical verification tier **`e2e`**. The kernel
  tier set is `static` / `runtime` / `e2e` / `manual_evidence`
  (`VALID_VERIFICATION_TIERS`, `src/zf/core/task/schema.py`). The underscore
  aliases `live_smoke`, `live_smoke_optional`, and `smoke` normalize to `e2e`
  at the runtime boundary (`src/zf/runtime/task_contract_normalize.py`), so they
  are safe but non-canonical.
  - **Do not** write `browser` or the hyphenated `live-smoke` as a tier: neither
    is a valid tier and neither has an alias, so an unknown token is **silently
    dropped** by `canonical_verification_tiers`, leaving the browser gate with
    no tier. Emit `e2e`.
- Reject or repair host-only commands such as bare `npx playwright test` for
  browser E2E.
- Reject or repair Docker Playwright commands that miss
  `--user "$(id -u):$(id -g)"`.
- Reject or repair unbounded browser install commands.
- When a mixed verification list includes static npm gates and a Docker browser
  gate, show the Docker command pattern for the browser gate only; do not create
  Docker-in-Docker examples.

## Clean Checkout Setup

For review and verify roles, a clean checkout often lacks dependencies. Before
calling a product regression:

- **Pin-commit first (audit-target binding).** Reader/verify child payloads
  carry a `target_commit` field (set in
  `src/zf/runtime/orchestrator_fanout.py`, `_pin_reader_target_or_reject`), and
  the reader workspace is pinned to that commit via
  `git worktree add --detach <commit>`
  (`src/zf/runtime/orchestrator_reactor.py`) / `pin_reader_target`
  (`src/zf/runtime/workdirs.py`). Before running **any** gate, run
  `git rev-parse HEAD` and confirm it equals the briefing's `target_commit`. On
  a mismatch, report a `workdir mismatch` (environment class) and stop — do not
  run the gate against the wrong tree. (The kernel emits
  `fanout.child.workdir_mismatch` when it cannot pin the tree at dispatch; a
  reader that finds a drifted HEAD is the same class of blocker.)
- Run package setup implied by the verification command, for example
  `npm ci --prefix packages/web-tui` or `npm ci --prefix ui-tui`.
- Treat `tsc: not found`, missing `node_modules`, workspace file package
  resolution, registry failures, and network failures as setup/environment
  issues until setup has been attempted.
- After setup, rerun the exact verification command from `task_map` and report
  the result.

## Failure Classification

The full runner-vs-product taxonomy is owned by the **`verify-review`** skill
(`yoke/verify-review/SKILL.md`) — apply it there and cite it. In short: classify
runner/setup failures as environment/harness issues, not product failures, when
evidence includes Docker unavailability, `EACCES`/unwritable workdir, browser
install hang / exit 137 / stale `INSTALLATION_COMPLETE`, missing deps before
setup, a HEAD ≠ `target_commit` workdir mismatch, or runner image/tool mismatch
without product assertions failing. Classify as product failure only after the
runner is healthy and app behavior/DOM/API/trace/screenshot/assertion
contradicts the task contract.

**Delta gate — a fixed runner does not earn an automatic re-review.** The kernel
suppresses re-opening a review/verify against a `target_commit` that already has
a recorded failure: it emits `fanout.retrigger.suppressed` with
`reason=no_delta_since_failure` instead of re-dispatching
(`src/zf/runtime/orchestrator_fanout.py`). So after you fix a runner/environment
blocker, the expectation of "just re-run the same audit" **does not hold while
HEAD is unchanged**. To get a fresh review you must either produce a **new
commit** (the delta the gate checks for) or route the environment blocker
through ZaoFu recovery — not wait for a self-re-review at the same commit.

## Evidence To Emit

When reporting a browser gate result, include the exact command, service
URLs/ports, whether it ran in Docker or failed before Docker, package setup
commands run, pass/fail counts, and either product-failure evidence or the
environment/setup blocker. Emit a role-consistent event: `dev.failed`,
`review.child.failed` / `review.child.completed`, or `verify.child.failed` /
`verify.child.completed`.

**A verify/review child report is a structured contract, not free text.** Since
FIX-14, the `report` payload on verify/review child events is validated against
the configured event schema's `required` **and** `non_empty` tiers
(`src/zf/core/verification/event_schema.py`, the `non_empty` field). Concretely
the report must carry:

- `requirement_coverage_matrix`: **at least one row**, each row's
  `requirement_id` drawn from a task-contract / PRD acceptance clause, with a
  non-empty `evidence_refs` list;
- `evidence_refs`: non-empty at the report level too;
- on a reject/`reject` recommendation, non-empty `gap_findings` with
  file-level locations.

Plain prose "enough text for triage to classify" **no longer satisfies the
report contract** when the schema is active. You do not have to guess the shape:
the briefing auto-injects a schema-education placeholder sample keyed by the
required/non_empty fields (`_SCHEMA_EDU_PLACEHOLDERS` in
`src/zf/runtime/orchestrator_fanout.py`, via `_schema_education_report_fields`) —
copy that sample and fill it in.

Enforcement strength is per-project via `verification.event_schema.mode`
(canonical values `disabled` / `warning` / `blocking`, validated in
`src/zf/core/config/loader.py`; the effective rules come from
`workflow.dag.event_schemas`). This repo's `zf.yaml` sets `mode: warning`, so a
malformed report is logged rather than rejected; bizsim-class runs set
`mode: blocking`, where a report that misses the coverage matrix / non-empty
fields is a hard schema reject. The **report contract itself is owned by
`verify-review`** — treat the fields above as a pointer, and read that skill for
the authoritative field list and rationale.

## Runtime Boundary

Do not use this skill to waive a failing validator or gate. If `task_map`
validation fails, repair the task contract or route the issue through ZaoFu
recovery. If Docker is unavailable, report the exact blocker and intended command
instead of installing host browsers by default.

## How To Test

Ask an agent to prepare or review a ZaoFu `task_map` containing a browser E2E
command. The expected output should preserve Docker Playwright, host UID/GID,
bounded install, the canonical `e2e` tier (not `browser`/`live-smoke`), a
pin-commit check against `target_commit` before any gate, and runner-vs-product
failure classification that defers to `verify-review`.
