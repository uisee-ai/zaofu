# Skills, Workdirs, and Git Evidence

> Audience: operators composing role skills and using worktree/ref evidence to
> support long-horizon delivery.

## 1. Skill Layering

Recommended ownership:

| Source | Use |
|---|---|
| general agent skills | Coding, testing, design, review, and debugging methods |
| harness/yoke role skills | Role context, evaluators, critics, and harness discipline |
| `zaofu/skills` | ZaoFu-specific glue, contracts, controlled workflows, and adaptations |

Avoid loading duplicate skills with the same responsibility. When a reusable
method needs ZaoFu-specific behavior, maintain the adapted version in the
repository rather than modifying an external source in place.

### Plan Contract Defaults

Product workflows may add customer/domain skills, but dispatch consumes stable
ZaoFu artifacts:

| Flow | Required method/contract | Main artifacts |
|---|---|---|
| issue | issue plan synthesis plus task-map contract | issue plan, task map, source index |
| PRD | PRD plan synthesis plus task-map contract | PRD, plan, task map |
| refactor | refactor plan synthesis plus task-map contract | refactor plan and lane-compatible task map |

Generic planning skills improve judgment but do not replace `task_map_ref`,
artifact manifests, or canonical task contracts.

## 2. Skill Sources

```yaml
skill_sources:
  - name: agent-skills
    path: ${ZF_AGENT_SKILLS_DIR:-/path/to/agent-skills}
    mode: readonly
  - name: zaofu-local
    path: ${ZF_ZAOFU_SKILLS_DIR:-/path/to/zaofu/skills}
    mode: readonly
```

`readonly` means the runtime reads and materializes the source. The generated
pool and manifests are rebuildable state.

## 3. Typical Role Skills

Typical responsibilities:

| Role | Skill focus |
|---|---|
| orchestrator | planning, decomposition, context, state-sync discipline |
| arch | specifications, interfaces, ADRs, source analysis |
| critic | document review, option scoring, security and gate evaluation |
| dev | incremental delivery, TDD, debugging, Git evidence, done contract |
| review | code quality, security, simplification, evidence review |
| test | deterministic tests, browser verification, evaluation harness |
| judge | release readiness, final evidence, archive contract |

## 4. Inspect Skills

```bash
uv run zf validate --strict-skills
uv run zf skills list
uv run zf skills doctor
```

Check for `resolved` status, unexpected collision candidates, missing metadata,
and materialization paths outside the active state directory.

## 5. Workdir Modes

Planning-only mode does not create real Git worktrees. Enable isolation with:

```yaml
runtime:
  workdirs:
    enabled: true
    root: .zf/workdirs
    mode: worktree
```

- writers implement in isolated worktrees/branches;
- readers inspect pinned task or candidate refs;
- `auto` preserves compatibility where role intent is not explicit.

```bash
uv run zf doctor workdirs
uv run zf workdir repair dev-1
```

## 6. Why Git Evidence Matters

Useful evidence includes:

- base commit/ref;
- source and candidate commit/ref;
- recent log;
- changed paths;
- diff summary or digest;
- verification commands and outcomes;
- dirty state.

This evidence detects false completion, stale resume context, wrong candidate
selection, rework without a real delta, and terminal judgment without delivery
proof.

## 7. Reviewing Git History

- The developer reports base, head, changed paths, and tests.
- Review reads the diff and relevant log and compares them with the contract.
- Test records the exact ref under verification.
- Judge checks terminal, Git, contract, and gate evidence together.
- Recovery briefings include recent task events and Git evidence.

Git is evidence, not the only runtime truth. Task status and workflow
transitions remain kernel-owned.

## 8. Quality Verification with Multiple Worktrees

1. Each writer performs scoped implementation and local verification.
2. A reader reviews a pinned candidate ref.
3. A separate reader runs candidate-level tests.
4. Judge evaluates the integrated candidate or ship target.
5. The kernel decides terminal state from events, refs, and discriminators.

Parallel roles can work independently while truth still converges through the
event log, task store, and controlled Git refs.
