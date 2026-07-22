---
paths:
  - "src/**"
  - "tests/**"
---

# Code Rules — src/ and tests/

This rule auto-loads only when working on Python source or tests. Keeps
the main CLAUDE.md focused on architecture; pulls in code-specific
discipline only when relevant.

## Code Conventions

- Python 3.11+, `src/` layout, type hints
- Prefer `pathlib`, `dataclasses`, stdlib first
- Modules: `src/zf/cli/`, `src/zf/core/`, `src/zf/runtime/`, `src/zf/integrations/`, `src/zf/web/`
- Deterministic kernel (zf-cli) — no LLM calls in core
- Do not hard-code project semantics, business quality judgments, scan
  strategy, semantic constraints, or project-specific gates into common
  runtime. Prefer skill/prompt/agent artifacts for those decisions, and
  promote repeated methods to reusable skills/profiles. Kernel code should
  validate schemas, state transitions, evidence presence, permissions,
  path/secret/budget safety, replay/resume, lifecycle, and external side
  effects.
- Side effects at the edges
- Resolve runtime state through project context / `project.state_dir`;
  do not hard-code `.zf`

## Test Scope Discipline

Do not select pytest scope from changed-file count alone. Use the impact
closure: changed module, direct callers, and shared Event/Schema/Store
contracts. Web/UI and backend are separate default test domains; cross-domain
tests are required when a change crosses an API, projection, EventLog, Store,
or schema boundary.

Required minimums:

- UI-only: frontend build/typecheck and affected browser/component tests.
- Backend module: affected module, direct callers, and contract tests.
- EventLog/Store/schema/config/orchestration/recovery: impact closure,
  `scripts/dev-premerge-gate.sh`, and relevant mock E2E.
- Provider/tmux/worktree/host changes: isolated mock provider tests first;
  real provider and host sensors are explicit test tiers.

Full pytest is for release, major cross-domain refactors, three-or-more core
domain changes, or explicit owner request. It may be sharded by module,
class, or node in fresh processes with time and memory budgets. A monolithic
pytest process that exhausts resources is a test-infrastructure finding, not
an automatic blocker for ordinary development.

## Module Size Discipline (Forward-Looking Only)

Existing oversized files (e.g. `src/zf/web/server.py` ~7000 lines,
`orchestrator_dispatch.py` / `orchestrator.py` each ~3000) are
**out of scope** — do not refactor them as part of unrelated work.
This rule applies to **new** code:

- **New files start ≤1000 lines.** A 200-line module with one job beats
  a 600-line module covering "related" things.
- **Alarm at ~800 lines with 2+ orthogonal concerns** (e.g. reactor
  handlers + state mutation + HTTP routes mixed) — split by
  responsibility. Precedent: the
  `orchestrator.py → orchestrator_dispatch / _lifecycle / _reactor`
  split.
- **Do not split by line count alone.** A long module that is N
  parallel `_on_<event>` handlers each <30 lines is fine — cost is
  scrolling, not cognition. Splitting into `_part1.py / _part2.py`
  makes it worse.
- **Adding new behavior to an already-oversized file → new sibling
  module, not appending.** A new dispatch path goes in
  `src/zf/runtime/<new-concern>.py`, not at line 3283 of
  `orchestrator_dispatch.py`.
- **Raising a size-freeze cap** (`tests/test_structure_discipline.py`
  `_OVERSIZED_FILE_CAPS`) requires the same-PR commit message to answer
  "why can this not be a sibling module". Shrinking a file → lower its
  cap to new size +10% in the same PR.

## Wire-Up Discipline (Library-Without-Callers Anti-Pattern)

A recurring debt class in this codebase: write a class + tests, never
import it from `src/zf/runtime/orchestrator.py` or `src/zf/cli/start.py`.
Examples that bit us in past sprints: `EscalationManager` (Sprint A
fix), `build_recovery_briefing` (Sprint D fix), `DriftDetector`,
`RefreshPolicy`, `ScopeRatchet` (Sprint F fix).

Before marking any new component "done":

1. `rg <ClassName> src/zf/runtime src/zf/cli src/zf/web`
2. Identify the actual runtime/CLI/Web entrypoint or registered service caller.
3. If there is no caller plus caller-level test, the component is **library
   code without callers**, not done.

Acceptance criteria for any new orchestration component must include
at least one caller reference and a caller-level verification proof.

## Changeset Simplicity (borrowed from karpathy-skills CLAUDE.md §2)

Module Size 上面讲的是 **per-file**(新文件 ≤1000)。Per-**changeset** 是另一维度:

- 写了 200 行如果能 50 行 → 重写
- 5 行根因的 bug 修动了 10 个文件 → 形状错了
- "test plan + helper module + docstring 段 + defensive try/except"
  包一个一行 fix = 4× 形状,strip back
- 测:`git diff --stat` 行数 ≈ 任务复杂度本身,**不是"顺便想到的"**

LLM 加塞最常见的 4 种"顺便":
1. 防御性 try/except 包不会失败的代码
2. 加 helper function 给只用一次的逻辑
3. docstring 写成段落而不是一行
4. 改名 / 调整格式 / "顺手清理" 无关代码(踩了 §Surgical 边界)

如果发现 changeset 形状不对,**砍掉再交**而不是 commit。
