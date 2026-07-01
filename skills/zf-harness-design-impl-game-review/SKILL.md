---
name: zf-harness-design-impl-game-review
description: "Use for evidence-backed ZaoFu architecture reviews that adjudicate docs/design intent against the current implementation."
---

# ZaoFu Design-Impl Game Review

This skill turns a one-off design-vs-implementation review prompt into a
ZaoFu-native review discipline. It is for ZaoFu harness architecture audits,
refactor planning, release readiness, and incident-driven design reconciliation.

It shapes reviewer output. It does not authorize direct edits to runtime truth.

## When to use

Use this skill when the task asks for any of:

- reviewing `docs/design` against `src/zf`
- deciding whether code should change or a design doc is stale
- finding ghost implementations with no design coverage
- finding ghost designs that were never wired into runtime
- reviewing ZaoFu operator surfaces: webkanban, channel, Feishu, Kanban Agent
- reviewing self-monitoring surfaces: supervisor, autoresearch, self-repair
- synthesizing a ZaoFu refactor review report from fanout reader shards

Do not use it for ordinary application code review unless the task is explicitly
about ZaoFu harness correctness.

## Precedence

- `agent-skills` provide general review, planning, and engineering methods.
- `yoke` role contexts constrain role behavior and evidence discipline.
- This skill constrains the specific ZaoFu harness review question:
  **when design and implementation disagree, who should change?**

If another loaded skill says "check implementation against the spec", keep that
check, but add the reverse question: maybe the spec is stale, over-designed, or
contradicted by the current deterministic kernel.

## Boundary

Default mode is read-only.

- Do not edit source, docs, events, kanban, session, feature, or runtime state
  unless the dispatch explicitly assigns implementation.
- Do not write `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml` by hand.
- Do not turn webkanban, channel, Feishu, or Kanban Agent into a second control
  plane. They may request controlled actions and display projections; kernel
  state changes remain kernel-owned.
- Do not add LLM decision logic inside deterministic kernel code.
- Do not treat a historical prompt or report as current evidence. Refresh facts
  from the current checkout.

## ZaoFu North Star

All findings and proposed directions must strengthen the ZaoFu harness:

1. deterministic kernel and `zf-cli`
2. `zf.yaml` as the single control plane
3. `events.jsonl` and kernel-managed state as runtime truth
4. bounded L1 environment / L2 brain / L3 hands autonomy
5. adversarial gates and independent verification against reward hacking
6. operator collaboration modules as projections plus token-gated actions:
   webkanban, channel, Feishu, Kanban Agent
7. self-monitoring and self-evolution modules:
   supervisor, autoresearch, self-repair

External products built with ZaoFu, such as Cangjie, are validation benches.
Fixing an external product only counts as ZaoFu work when it exposes and fixes a
general harness failure mode.

## Required fact baseline

Before making claims, collect a compact baseline. Prefer commands like:

```bash
git status --short --branch
git log --oneline -30
find docs/design -maxdepth 1 -name '[0-9][0-9]-*.md' | sort | tail
wc -l src/zf/web/server.py
rg -n "emit\\(|task.status_changed|controlled action|second control|autoresearch|supervisor|channel|feishu" src/zf docs/design tests
```

For each reviewed subsystem, record:

- current file paths and line references
- tests or lack of tests
- event producers and consumers when events are involved
- config fields and actual consumers when config is involved
- call path evidence for webkanban, channel, Feishu, supervisor, autoresearch,
  and self-repair
- incident or run evidence when the claim comes from a real failure

Doc self-description is not enough. Code, tests, runtime events, and current
operator decisions must be checked.

## Evidence classes

Use these labels in reports:

- `code-test`: code line, test name, passing/failing test output
- `runtime-event`: event id, event type, producer, consumer, payload field
- `config-liveness`: config schema plus runtime consumer
- `doc-current`: current design/manual/record reference
- `doc-conflict`: two current docs disagree
- `incident`: run record, failure report, or reproduced failure
- `operator-decision`: explicit owner decision from the task context
- `unverified`: plausible but not proven

Only `code-test`, `runtime-event`, `config-liveness`, `doc-current`,
`incident`, and `operator-decision` can support a final verdict. Mark everything
else as `unverified`.

## Review procedure

For every divergence, run the five-step game:

1. State the divergence:
   `design says X` vs `implementation does Y`, both with evidence.
2. Argue for the design:
   why the design is still the better ZaoFu north star.
3. Argue for the implementation:
   why current code is better, simpler, already proven, or why the design is
   stale or over-built.
4. Adjudicate with one classification.
5. Produce one concrete action with a minimal verification step.

## Classifications

Use exactly one:

| Class | Meaning | Action |
|---|---|---|
| `DOC-STALE` | implementation is correct or newer than docs | update docs |
| `IMPL-DRIFT` | design remains correct and implementation regressed | create code-fix backlog |
| `GENUINE-GAP` | design describes capability not actually built | build it or mark roadmap/defer |
| `ACCEPTED-DIVERGENCE` | both are valid and divergence is intentional | document rationale |
| `OVER-DESIGN` | design is speculative or too heavy for observed failures | prune or supersede |
| `DOC-CONFLICT` | docs disagree with each other | choose canonical doc and align others |
| `GHOST-IMPL` | important code exists with no design coverage | add design/ADR coverage |
| `NEEDS-REPRO` | claim depends on a failure that was not reproduced | add repro task |
| `NEEDS-OWNER-DECISION` | direction is product ownership, not engineering proof | ask owner |

## Mandatory lenses

Apply these lenses before finalizing:

- Invariant enforcement:
  is the invariant mechanically enforced by code/test/guard, or only prose?
- Event liveness:
  is every important event both emitted and consumed?
- Config liveness:
  is every important config field parsed and consumed?
- No-dead-end:
  does a failure class have a bounded recovery, replan, or escalation path?
- Second control plane:
  can any operator surface directly write truth or bypass kernel checks?
- Skill/runtime boundary:
  is a behavior being trusted only because a skill says so, when kernel should
  enforce it?
- Failure-driven scope:
  does the proposed work solve an observed ZaoFu failure mode, or is it only a
  borrowed idea that looks attractive?

## Drift self-check

Before proposing any architecture direction, answer:

- Which north-star item does it strengthen?
- Which observed failure or risk does it address?
- Is this ZaoFu harness work, or an external product feature?
- Is it the smallest useful change?

If the answer is unclear, list the idea under `Rejected as drift` instead of
promoting it to a recommendation.

## Output contract

For a single reviewer, output:

```text
## Fact Baseline
...

## Adjudication Table
| subsystem | divergence | design argument | impl argument | verdict | class | evidence | action |

## Architecture Directions
| direction | north_star | failure_mode | build/prune/reconcile | priority | trigger |

## Docs Lifecycle
| doc_or_path | action | reason | evidence |

## Module Maturity
| module | design | implementation_wiring | tests | status | gap |

## Backlog Candidates
| id | title | class | step | verify |

## Rejected As Drift
| idea | reason |

## Unverified Assumptions
...
```

For fanout reader shards, keep the payload small and mergeable:

- `fact_baseline`
- `coverage_matrix`
- `adjudication_rows`
- `findings`
- `backlog_candidates`
- `docs_lifecycle_actions`
- `module_maturity`
- `rejected_as_drift`
- `unverified_assumptions`
- `evidence_refs`

For synthesis roles, merge only rows with evidence. When two shards disagree,
prefer `code-test` over `runtime-event`, then `config-liveness`, then
`doc-current`, then `incident`, then `operator-decision`. Keep unresolved rows
as `NEEDS-REPRO` or `NEEDS-OWNER-DECISION`; do not invent certainty.

## ZaoFu runtime reporting

When running inside a ZaoFu role:

- Put review proof in `evidence_refs` and replayable artifact paths.
- If producing a report file, publish it through the normal artifact path or
  the role's assigned lifecycle event.
- If assigned as a review or critic role, approval still requires the role's
  normal gate contract. This skill adds adjudication; it does not bypass
  `zf-harness-gate-evaluator`, `zf-harness-evidence-collection`, or
  deterministic runtime checks.
- If implementation is needed, produce backlog candidates. Do not mutate state
  or dispatch workers unless your role and task explicitly permit it.
