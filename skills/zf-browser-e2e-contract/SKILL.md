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

- For a Candidate Reader, first follow the shared-host-evidence step in
  `zf-harness-verification-checklist`. Apply the Docker commands below when
  the assigned obligation needs a new execution and the dispatch provides
  its execution route. The signed observation's approval identifies its producer.

- Preserve an exact browser command or security boundary supplied by the
  canonical Task, PRD, or owner contract byte-for-byte. That run-scoped
  contract takes precedence over this skill's generic command pattern.
- Treat the host-network / `mcp/playwright:latest` pattern below as a fallback
  only when the canonical contract does not provide an exact command. Never
  replace a contract-pinned image, bridge network, read-only mount, or `tmpfs`
  boundary with the fallback image, host network, writable mount, or install
  sequence.
- Respect `zf.yaml` and `project.state_dir`; do not hard-code `.zf`.
- Use Docker with `mcp/playwright:latest` for browser E2E unless the operator
  explicitly asks for host browsers and no exact run-scoped command is pinned.
- Start local API/UI services on `0.0.0.0` when a container must reach them.
- Declare the exact owner-facing service URL and origin in the Task/real-E2E
  matrix. A pass on `http://localhost` does not prove behavior on
  `http://<LAN-IP>` or another delivery origin.
- Use host networking for local service smoke tests only when the canonical
  contract does not pin a different network boundary.
- Run the container with the host UID/GID so mounted workdirs remain writable.
- Bound browser install commands with `timeout`; never leave
  `playwright install chromium` unbounded.
- Do not wrap an existing Docker Playwright command in another Docker command.
- The `mcp/playwright:latest` image has an MCP CLI entrypoint. A browser
  command MUST either use `--entrypoint bash -lc` (as in the fallback below)
  or explicitly invoke the image's documented runner; never append `node`,
  `npm`, or `playwright` directly after the image name. That form is parsed as
  MCP options (for example `unknown option --test`) and is a runner failure,
  not product evidence.
- Do not downgrade a runner/setup failure into product rework.

## Command Pattern

When no exact Task/PRD/owner command is supplied, use this fallback shape for
browser gates:

```bash
docker run --rm --network host \
  --user "$(id -u):$(id -g)" \
  --entrypoint bash \
  -v "$PWD:/work" -w /work \
  -e PLAYWRIGHT_BROWSERS_PATH=0 \
  mcp/playwright:latest \
  -lc 'set -euo pipefail; timeout 180s ./packages/web-tui/node_modules/.bin/playwright install chromium; ./packages/web-tui/node_modules/.bin/playwright test --config packages/web-tui/playwright.config.ts'
```

Adapt paths to the current `task_map`; keep the runner flags intact. When an
exact command is supplied, preserve it instead of adapting this fallback.

> The `mcp/playwright:latest` image name, the `INSTALLATION_COMPLETE` marker
> referenced below, and this exact command shape are **skill-owned convention
> (no kernel validator)** — the kernel does not parse or enforce them; they are
> operator/agent discipline.

## Task Map Checklist

- Keep verification commands machine-readable. If `verification` is a list, each
  item must be a standalone command; never stringify a Python list into a
  contract field.
- Planning readiness is not execution success. Before implementation, missing
  screenshots, traces, or browser receipts are expected future outputs, not an
  environment blocker. Keep the plan dispatchable when it names a runnable
  Docker Playwright command and assigns the runner/config/test/evidence paths to
  a producer task. Mark the plan blocked only when the declared runner itself is
  unavailable or forbidden and no sanctioned equivalent exists.
- A browser acceptance criterion must be bound before dispatch to an `e2e`
  command plus writable runner/config/test/evidence paths. Do not leave viewport,
  pointer, network, storage, refresh, or screenshot acceptance attached only to
  `npm test`, a build command, or a future Verify interpretation.
- Record `bind_host`, `service_url`, and `browser_origin` in the browser row or
  its evidence contract. Verify the owner-facing origin in addition to any
  runner-internal localhost origin; preserve both receipts when both are part
  of the delivery path.
- When the product depends on WebCrypto or another secure-context API, run a
  capability probe at the exact owner-facing origin (for example
  `window.isSecureContext` and the required `crypto.subtle` operation). An
  unsupported insecure origin is a product failure unless the product contract
  declares and the test proves a supported fallback. Do not replace this with
  a localhost-only assertion, because browsers treat localhost and LAN/IP HTTP
  origins differently.
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
URLs/ports, bind host, tested origins, secure-context/capability probe results,
whether it ran in Docker or failed before Docker, package setup
commands run, pass/fail counts, and either product-failure evidence or the
environment/setup blocker. Emit a role-consistent event: `dev.failed`,
`review.child.failed` / `review.child.completed`, or `verify.child.failed` /
`verify.child.completed`.

For every mandatory browser journey in a current Product Acceptance Spec,
produce all applicable evidence layers:

1. DOM/business assertions proving the expected content, state, navigation,
   and key interaction rather than only HTTP 200 or a mounted root element.
2. Screenshot or canvas-pixel evidence proving the main viewport is nonblank,
   correctly framed, and free of incoherent overlap at the declared desktop
   and mobile viewports. For canvas/media products, record deterministic pixel
   statistics or frame checks in addition to the screenshot.
3. A mandatory demo-difference assertion when the journey claims dynamic
   behavior: compare before/after state, frames, route, data, or control output
   so a static placeholder cannot pass as the feature.

Persist large screenshots, traces, and pixel reports as sidecars and return
their exact ref/digest in the Product Acceptance Report. Do not embed image
bodies in events or let narrative "looks correct" replace observable checks.

容器回执的媒体若已由运行时提供 `container-media-index.v1`，先核对原证据摘要、候选和镜像，再按索引的 state_dir 相对路径用图片工具直接查看。媒体索引是可重建的只读投影，不能替代原回执或业务断言；按冻结验收条款区分媒体留存、交互证明与播放审阅：留存需核验回执、摘要和身份；交互可使用契约认可的 DOM 断言、trace、截图或视频。只有条款明确要求播放/解码时，才将缺少播放器记作该义务的阻塞；不能临时新增播放义务，也不能豁免已经明确要求的播放检查。不要为读取图片写产品目录、结果 scratch 或尝试 `/proc` 内存文件。若没有可读媒体路径，明确报告证据交付缺口，不能以文件存在或监工看过代替独立视觉审查。

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
