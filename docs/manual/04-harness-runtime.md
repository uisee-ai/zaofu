# Harness 运行流程

> 适用对象: 需要理解 ZaoFu 如何从用户目标推进到代码、review、test、judge 和 done 的操作者。

## 1. 三层架构

ZaoFu 当前按三层运行:

| 层 | 责任 | 示例 |
|---|---|---|
| Layer 1 deterministic kernel | 解析事件、推进状态、执行门禁、生成 briefing、检测 stuck/orphan/recycle | `src/zf/runtime/`、`src/zf/core/` |
| Layer 2 orchestrator agent | 拆解目标、分配任务、处理返工、选择下一步 | `roles: orchestrator` |
| Layer 3 worker agents | 设计、实现、review、测试、最终判定 | `arch/dev/review/test/judge` |

Kernel 管 truth 和机械状态转移;agent 通过事件和 CLI 表达意图。

## 2. 任务链路

经典代码任务通常按以下链路推进:

```text
user.message
  -> feature.created / task.created
  -> task.assigned(dev)
  -> dev.build.done
  -> task.assigned(review)
  -> review.approved 或 review.rejected
  -> task.assigned(test)
  -> test.passed 或 test.failed
  -> task.assigned(judge)
  -> judge.passed 或 judge.failed
  -> discriminator.passed
  -> task.status_changed(done)
```

失败事件会触发返工:

| 失败事件 | 默认返工方向 |
|---|---|
| `review.rejected` | `dev` |
| `test.failed` | `dev` |
| `judge.failed` | `dev` |
| `gate.failed` | `dev`,可由 `workflow.rework_routing` 覆盖 |
| `discriminator.failed` | `dev`,可由 task contract 或 workflow 覆盖 |

设计评审场景可把 `gate.failed` 或 critic rejection 路由回 `arch`。

这条链不是唯一合法拓扑。当前实现支持 `workflow.stages` / `workflow.pipelines`、
`static_gate.*`、`impl.child.*`、`verify.child.*`、fanout/fan-in、reader/writer 分工,
以及 issue / PRD / refactor flow。实际阶段、terminal predicate 和返工路由以
`zf.yaml` 与 `zf workflow inspect` 的推导结果为准。

## 3. 防止提前宣告完成

ZaoFu 不以 agent 口头说“完成”为终点。done 需要满足:

- task 已经历合法阶段事件链。
- dev/verify/judge 或配置中的 terminal predicate 已满足。
- 严格配置下通过 contract discriminator。
- scope、architecture、promoted rule、quality gate 等已按配置通过。
- task done evidence 可追溯到事件链和 git evidence。

`zf kanban move <task_id> done` 也会检查前置事件。在经典链路下,缺少
`judge.passed`、`test.passed`、`review.approved` 或 `discriminator.passed` 时会被拒绝
并写入非法迁移事件。自定义 workflow 下,检查项按当前拓扑和 terminal predicate 收敛。

## 4. Contract 与 Dispatch Token

严格 harness 会要求 task contract:

- `behavior`: 目标行为。
- `verification`: 如何验证。
- `quality`: 质量要求。
- `out_of_scope`: 明确不做什么。
- `rework_delta`: 返工时必须说明本轮相对上一轮改变了什么。
- `dispatch_id`: worker briefing 中的调度 token。

worker 完成事件必须能对上 dispatch token。这样可以避免旧 session、重复事件、手工事件或上下文漂移把任务误关。

## 5. Watcher 与 Wake Patterns

`zf start` 会启动 `EventWatcher`。它做两件事:

- 新事件命中 wake pattern 时,调用 orchestrator 处理。
- 即使没有事件,也周期性 tick,驱动 stuck/orphan/context recycle 扫描。

这就是为什么真实长任务推荐保持 `zf start` 前台运行。`--foreground` 现在只是兼容旧命令的 no-op alias;
只有 `--no-watch` 会明确选择不长期运行 watcher。只启动 tmux 而不运行 watcher,
worker 可能完成了但 pipeline 不继续推进。

可观察命令:

```bash
uv run zf events --last 30
uv run zf watch --follow
uv run zf status --workers
```

## 6. Stuck、Orphan 与 Recovery

ZaoFu 处理三类长任务风险:

| 风险 | 触发方式 | 处理方式 |
|---|---|---|
| worker stuck | pane/session 长时间无输出或无进展 | 写入 stuck 事件,尝试恢复/重启/重新投递 |
| task orphan | task 已 in-progress 但长时间没有阶段完成事件 | warning 后 escalated,可 requeue |
| context 过大 | provider 上下文使用超过阈值 | warning 先 checkpoint;compact 阈值后压缩/回收;hard cap 会阻止新 dispatch |

相关 role 字段:

```yaml
stuck_threshold_seconds: 180
orphan_warning_seconds: 300
orphan_escalate_seconds: 600
context_window_tokens: 200000
context_warning_threshold: ${ZF_CONTEXT_WARNING_THRESHOLD:-0.6}
context_compact_threshold: ${ZF_CONTEXT_COMPACT_THRESHOLD:-0.7}
context_hard_cap: ${ZF_CONTEXT_HARD_CAP:-0.9}
drain_hold_seconds: 180
```

`zf.yaml` 仍是唯一控制面。`zf.yaml` 同目录 `.env` 只提供变量值,例如
`ZF_CONTEXT_COMPACT_THRESHOLD=0.75`;未被 `zf.yaml` 引用的 `.env` 值不会改变
runtime 行为。旧字段 `recycle_threshold` / `recycle_hard_cap` 仍兼容。

## 7. Bounded Rework

每个 role 可设置:

```yaml
max_rework_attempts: 3
```

当同一 task 的 review/test/judge/gate/discriminator 失败超过上限,会产生 capped/escalation 类事件,避免无限循环。

返工路由优先级:

1. task contract 中的 `rework_to`。
2. `workflow.rework_routing`。
3. 默认回 `dev`。

## 8. Agent Telemetry

启动时会为 provider 写入 hook settings:

- Claude: `.zf/hooks/settings.json`
- Codex: `.codex/hooks.json`

Session tailer 会把 provider session jsonl 中的工具调用、文本、usage 等转为 `agent.*` 事件,用于观测和指标统计,不需要 SDK headless 调用。

如果 Codex 提示 hooks 需要 review,需要在 Codex 交互里完成 hook review,否则 hook telemetry 可能不会进入 `events.jsonl`。

## 9. 质量门禁与 Discriminator

`quality_gates` 是命令级 gate。`verification` 是 runtime discriminator。它们互补:

- gate 检查“命令是否通过”。
- discriminator 检查“证据是否足够、范围是否正确、是否满足 contract、是否违反架构/规则”。

严格配置下,`judge.passed` 之后仍可能被 `discriminator.failed` 打回返工。这是预期行为,不是重复审核。

## 10. Run Manager、Supervisor 与 Autoresearch

当前 runtime tick 除了 watcher 推进,还会按配置/节流刷新若干控制面 projection:

- **Run Manager**:维护 run / attempt / workflow spine 投影,做可重试、可恢复的 deterministic
  run 管理。可选 resident agent 只能 emit 观察/建议事件,不直接改 kernel truth。
- **Supervisor**:读取 events、kanban、role_sessions、failure signals、automation 等输入,
  生成 supervisor projection,并可通过受控事件记录 decision、owner visible message、
  autoresearch invocation request。它不是第二控制面,不直接 kill worker 或手写 state。
- **Autoresearch invocation**:Supervisor/Run Manager 可以请求诊断或自修复候选。默认策略是
  `proposal_only`、`sandbox_required: true`、`requires_owner_approval_for_apply: true`,
  不允许直接 mainline apply。

## 11. 结束一轮长任务的签收口径

不要只看 agent 最后一段话。至少检查:

```bash
uv run zf kanban --board
uv run zf task trace <task_id>
uv run zf refs verify
uv run zf metrics snapshot
uv run zf doctor
```

签收条件:

- 目标 task/feature 到达 done。
- done 有 terminal evidence。
- 没有未处理 fatal/blocker 事件。
- git evidence 能定位 base/head/log/diff。
- 必要测试和 Web/API projection 已验证。
