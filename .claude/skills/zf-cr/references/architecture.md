# 架构审查检查单

architecture / full 模式读取；scoped 仅当调用链跨越相应边界时读取相关章节。
下列历史文件名和符号是搜索线索，若已迁移，跟踪当前 owner，不能把旧位置当作实现保证。

## 系统地图

Before deep review, build a whole-system inventory so the second-pass prompt is
not biased toward only the files already known to the reviewer:

- entrypoints: CLI commands, Web/API routes, runtime loops, background
  sweepers, scripts, examples
- data/control flow: config -> kernel -> events/stores -> runtime projections
  -> Web/API/operator surfaces
- external contracts: provider CLIs, tmux, git worktrees, browser E2E,
  Feishu/OpenClaw integrations, file artifacts
- state/artifact inventory: source files vs event ledger vs canonical stores vs
  required artifacts/sidecars vs rebuildable projections and exported reports
- security boundaries: tokens, permission modes, path guards, read-only vs
  mutation routes, secret redaction
- compatibility surfaces: examples, runbooks, CLI flags, YAML schema, skill
  source resolution, migration behavior
- observability: events, traces, diagnostics, costs, token/context metrics,
  stuck-worker and recovery signals
- performance/scale risks: oversized handlers, polling, log scans, fanout
  concurrency, Web payload size, context growth

## 控制面

- Confirm `zf.yaml` is the only control-plane config.
- Search for hard-coded `.zf`, direct state path assumptions, duplicate task
  contracts (`sprint_contract` vs `contract`), and schema drift.
- Verify `project.state_dir` is respected by CLI, runtime, Web/API,
  simulations, examples, and tests.
- Check YAML schema/backward compatibility: old examples should fail clearly or
  migrate explicitly, not silently activate stale behavior.

## 状态归属

- Verify layered authority against current code and the available authority docs: event occurrence/causation/verdict
  refs through `EventWriter` / `EventLog`; current state through `TaskStore`,
  `FeatureStore`, `SessionStore`, and `RoleSessionRegistry`; complete semantic
  bodies/evidence through sanctioned artifact/sidecar writers.
- Flag direct writes to `events.jsonl`, `kanban.json`, `session.yaml`,
  `feature_list.json`, or `role_sessions.yaml` outside allowed helpers.
- Treat `skills/` as source and provider skill trees as synced copies. Do not
  classify active workdirs/worktrees as disposable while they may contain
  uncommitted candidates. Classify lockfiles, progress, cost, diagnostics,
  traces, and dashboard state individually as projections unless code proves a
  narrower authority.
- Trace event type -> payload schema -> projector/store update -> Web/API
  projection for the critical workflows under review.

## Runtime / 编排

- Separate deterministic kernel behavior from agent-driven behavior.
- Review orchestrator, worker protocol, Star fanout, child/synth payloads,
  inline override, heartbeat, recovery, stuck-worker fallback, rework routing,
  and task completion gates.
- Flag self-declared completion paths without supporting deterministic gate
  evidence.
- Check the 4-level rework cap (evidence_reissue / respawn / dispatch_retry /
  circuit_breaker) actually bounds long-running failures.
- Verify the contract surfaces that landed after the rework cap (基线检查单,
  漏掉即当新发现/漏真热点):
  - **Tier-2 诊断路由**:`diagnosis.requested` / `diagnosis.completed`
    (`src/zf/core/events/known_types.py` `KNOWN_EVENT_TYPES`;铸造/消费在
    `src/zf/runtime/diagnosis.py` + `orchestrator.py` `_run_diagnosis_sweep`,propose-only
    路由,`diagnosis.completed` 判 needs_owner 才升级 owner)。
  - **plan 指纹判重**:`plan.minting.suppressed`
    (`src/zf/runtime/orchestrator_fanout.py` `_plan_approval_satisfied`,FIX-12;语义指纹重复则
    抑制铸造,防 judge cap 冻结期重复铸 plan)。
  - **判审收敛 delta 门 + pin-commit**:`fanout.retrigger.suppressed`
    (`orchestrator_fanout.py` `_delta_gate_allows`,FIX-9/15)配合
    `fanout.child.dispatched` payload 的 `target_commit`(pin 审计对象,
    历史审计取 dispatch 时锁定的 commit,而非漂移的当前 HEAD)。
  - **verify report schema `non_empty` 档位**:required 只保证键在,空转合约
    需 `non_empty`(`src/zf/core/verification/event_schema.py` `EventSchemaRule.non_empty`,FIX-14;
    典型字段 `requirement_coverage_matrix` / `gap_findings` 必须非空)。
  - **candidate 增量幂等**:`candidates.py` 的
    `git rev-list --cherry-pick` 按 patch-id 排除 base 侧已含等价补丁
    (`src/zf/runtime/candidates.py` `CandidateRebuilder._task_commits`,FIX-10)。
- For Star/refactor-planning workflows, verify reader/writer/synth routes,
  artifact contracts, `target_ref` behavior, and final plan materialization
  boundary.
- Check concurrency semantics: fanout/fanin ordering, idempotency, retry
  dedupe, stale worker cleanup, and whether independent tasks can be traced
  under one parent without false dependencies.

## 集成与 Web/API

- Verify integrations do not write canonical business state directly or couple to
  orchestrator internals.
- Verify Web/API projections stay read-oriented unless a deterministic,
  token-gated kernel action path exists.
- Check workspace/project switching, Kanban agent, channel/group chat,
  headless provider sessions, token/context telemetry, and runtime metrics for
  project-state leakage.
- Check UX-backed runtime claims: if the UI shows streaming, progress, token
  usage, role context, or completion status, verify the authoritative backend
  field/ref, its provenance, and failure state.

## 测试、文档与已知债务

有相关 task/backlog 时，只读核对状态和触发条件，避免重复报告已知债务。
缺失本地 backlog 或 slash command 不阻止审查，也不据此推断实现有缺陷。
不执行可能改写状态的 backlog audit；必要时直接读取相关记录。

Then:

- Identify coverage gaps for config validation, runtime state transitions,
  event append/query behavior, orchestration, verification, recovery, Web/API,
  Star fanout, skills, workspace/project, and regressions.
- Check whether architecture, runtime, config schema, Web/API, security, or
  external control-plane changes are reflected in relevant docs.
- Check doc 00-index hygiene: new design docs registered? orphan docs?
- Check task/backlog files follow status, acceptance, and done/defer rules.
- Cross-check examples and runbooks against current validation/runtime code;
  stale examples are review findings when they can mislead real runs.

## 模块体积

Known oversized files are explicit defer debt, not surprise findings:

| File | 历史参考 LOC（须现场核实） | Status |
|---|---:|---|
| `src/zf/web/server.py` | ~9186 | explicit defer |
| `src/zf/runtime/orchestrator_fanout.py` | ~7687 | 第二大文件、近期新合约主要落点(plan 判重 / 判审收敛 / pin-commit) |
| `src/zf/runtime/orchestrator_dispatch.py` | ~5019 | already split, residual cohesion |
| `src/zf/runtime/orchestrator.py` | ~4576 | already split, residual cohesion |

`orchestrator_fanout.py` 已是第二大文件,且是 FIX-12/14/9/15 等合约的主要落点。
新合约不断往它里加是一条真热点,不要因为它是已知 defer 就漏看。

Do not repeat-flag these as new findings. Instead check:

- Has any **new** file violated the ≤1000-line discipline?
  (`.claude/rules/code.md:34`、CLAUDE.md Discipline Health Signals:新文件
  首版 ≤1000 行,无后续"切分"refactor commit。旧超大文件是 out-of-scope
  defer,不是新发现。)
- Has any new code been appended to these oversized files instead of creating
  a sibling module when a sibling owner would be clearer? (尤其
  `orchestrator_fanout.py`,近期合约都往这里堆。)
- Did a refactor reduce or increase coupling around these files?
