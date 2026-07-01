# Skills、Workdir 与 Git Evidence

> 适用对象: 需要组合 `agent-skills`、`yoke`、ZaoFu 本地 skills,并用 worktree/git evidence 支撑 long-horizon 交付的操作者。

## 1. Skills 分层策略

建议的能力分层:

| 来源 | 用途 |
|---|---|
| `agent-skills` | 主线通用 coding/review/testing/design 能力 |
| `yoke` | harness/role context/gate/evaluator/critic 等补充能力 |
| `zaofu/skills` | ZaoFu 专用 glue skill、约束、补丁化适配 |

策略:

- 主线 coding skills 优先使用 `agent-skills`。
- `yoke` 只加载 harness 相关能力。
- 如果 `agent-skills` 和 `yoke` 同名且能力相同,不要加载 `yoke`。
- 如果 `yoke` 某个 skill 需要为 ZaoFu 修改,优先 copy 到 `zaofu/skills` 后按需修改,不要直接改外部 repo。
- Plan/task-map 阶段必须加载 ZaoFu 标准 contract skill;客户或项目自定义
  skills 只能作为补充,不能替代 `task_map_ref` / `task_map.json` 机器合约。

### Plan Contract 默认口径

产品化 workflow 允许客户追加任意业务 skills,但 ZaoFu 固定以下调度合约:

| 场景 | 必选 ZaoFu skill | 产物 |
|---|---|---|
| issue | `zf-issue-plan-synth` + `zf-plan-task-map-contract` | `issue-plan.md`, `task_map.json`, `source_index.json` |
| PRD | `zf-prd-plan-synth` + `zf-plan-task-map-contract` | `prd.md`, `prd-plan.md`, `task_map.json` |
| refactor | `zf-refactor-plan-synth` + `zf-plan-task-map-contract` | `refactor-plan.md`, lane-compatible `task_map.json` |

通用 `planning-and-task-breakdown`、`frontend-ui-engineering`、客户 domain
skill 等可以叠加,用于提升判断质量;但后续 writer fanout、Kanban、verify、
judge 只读取标准事件字段和 artifact refs。`task_map.ready` 缺
`task_map_ref` 应视为 plan contract 失败,不能进入实现阶段。

## 2. 推荐 Skill Sources

```yaml
skill_sources:
  - name: agent-skills
    path: ${ZF_AGENT_SKILLS_DIR:-/path/to/external-skills-root/skills}
    mode: readonly
  - name: zaofu-local
    path: ${ZF_ZAOFU_SKILLS_DIR:-/path/to/zaofu/skills}
    mode: readonly
  - name: yoke-critic
    path: ${ZF_YOKE_CRITIC_DIR:-/path/to/role-gate-skills-root/role-skills/critic}
    mode: readonly
```

`mode: readonly` 表示启动时只读取来源并 materialize 到 runtime pool。运行态投影属于 rebuildable state。

## 3. 典型 Role Skills

`examples/dev-codex-backends.yaml` 中的组合可作为参考:

| Role | 典型 skills |
|---|---|
| orchestrator | `using-agent-skills`, `planning-and-task-breakdown`, `context-engineering`, `zf-yoke-orchestrator-role-context`, `zf-harness-state-sync`, `zf-harness-instruction-hygiene` |
| arch | `spec-driven-development`, `api-and-interface-design`, `documentation-and-adrs`, `source-driven-development` |
| critic | `zf-yoke-critic-role-context`, `document-review`, `plan-option-scoring`, `skeptic-observation`, `security-review`, `zf-harness-gate-evaluator` |
| dev | `incremental-implementation`, `frontend-ui-engineering`, `test-driven-development`, `debugging-and-error-recovery`, `source-driven-development`, `git-workflow-and-versioning`, `zf-yoke-dev-worker-role-context`, `zf-harness-done-contract`, `zf-harness-state-sync` |
| review | `code-review-and-quality`, `code-simplification`, `security-and-hardening`, `performance-optimization`, `zf-yoke-review-role-context`, `zf-harness-gate-evaluator`, `zf-harness-evidence-collection` |
| test | `test-driven-development`, `debugging-and-error-recovery`, `browser-testing-with-devtools`, `zf-yoke-test-evaluator-role-context`, `zf-harness-verification-checklist`, `zf-harness-eval-harness` |
| judge | `final-meta-review`, `pre-release-review`, `council`, `zf-yoke-quality-gate-role-context`, `zf-harness-archive-contract`, `zf-harness-evaluator-scoring` |

ZaoFu 自身的架构审计、refactor review 或 release-readiness review 可以额外加载
`zf-harness-design-impl-game-review`。它用于裁决设计文档与当前实现
分歧时“该改代码还是该改文档”，不建议作为普通产品开发 review 的默认 skill。

## 4. 检查 Skills

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --strict-skills
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills list
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills doctor
```

检查重点:

- `status=resolved`。
- 没有 `missing` / `invalid`。
- 没有不符合预期的 `collision_candidates`。
- `materialized_to` 指向当前 `project.state_dir` 下 runtime pool 或 worker runtime 目录。

## 5. Workdir 运行模式

默认 `runtime.workdirs.mode: dry-run` 只做计划。真实隔离建议:

```yaml
runtime:
  workdirs:
    enabled: true
    root: .zf/workdirs
    mode: worktree
```

writer/reader 策略:

- `writer`: 适合 dev,可以在隔离 branch/worktree 修改代码。
- `reader`: 适合 review/test/judge,读取候选 ref 做验证。
- `auto`: 适合 orchestrator 或不明确需要写入的 role。

检查:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor workdirs
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main workdir repair dev-1
```

## 6. Git Evidence 的作用

Git evidence 让 review/test/judge/recovery 不只依赖 agent 自述,而是能看到:

- base ref。
- head/candidate ref。
- 相关 git log。
- changed files。
- diff summary / diff hash。
- dirty state。

这对以下问题特别关键:

- worker 提前宣告完成但没有真实 diff。
- retry/resume 后上下文丢失。
- 多 worktree 下 review/test 对错了候选 ref。
- rework 没有实际 delta。
- final judge 无法证明交付内容来自本轮任务。

## 7. Review Git Log 的建议

有必要把 git log review 集成到 review/test/judge 的 skill 和 briefing 中,但不要让 agent 直接把 git log 当作唯一 truth。

推荐口径:

- dev 完成时报告 changed files、tests、base/head。
- review 必须看 diff 和最近 log,并明确指出本轮改动是否与 task contract 对齐。
- test 必须说明验证所基于的 ref。
- judge 必须核对 terminal evidence、git evidence、contract 和 gate 结果。
- recovery briefing 必须带最近 task 事件和 git evidence,帮助新 session 接上断点。

## 8. 多 Worktree 下的质量验证

多 dev worktree 场景不要让每个 dev 自己宣布全局完成。建议分层验证:

1. dev writer 在自己的 worktree 完成局部改动和局部测试。
2. review reader 对 candidate ref 做 diff review。
3. test reader 在 candidate ref 上跑独立测试。
4. judge 在集成候选或最终 ship target 上做终态判定。
5. kernel 通过 terminal evidence、refs 和 discriminator 决定是否 done。

这样不同 role 可以并行,但 truth 仍收敛到 `events.jsonl`、`kanban.json` 和 git refs。
