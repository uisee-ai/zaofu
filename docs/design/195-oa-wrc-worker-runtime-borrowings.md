# 195 OA/WRC Worker Runtime：借鉴 Agent Runtime 的执行合同与唤醒语义

状态：partial（设计与第一批机械合同已落地；真实 Provider、宿主进程取消和收益资格仍需独立 canary）

本文承接 194 的阶段模板、语义任务绑定和有界动态工作流，记录从 Kimi Code
`agent-core-v2` 的 Agent、AgentSwarm 和 Tower 路线中可移植到 ZaoFu 的部分。
它不把 Kimi 的 `.tower` 状态文件或模型控制循环引入 ZaoFu，也不改变
`AGENTS.md` 规定的 Kernel / agent / skill 权限边界。

## 设计结论

Kimi Code 有两条不同的调度路线：普通 `Agent` 是主模型选择 Agent tool 后，
由子 Agent runtime 创建、运行和回收；Tower 是显式的 mission、worktree、
review、merge 协议。两者共同点是：子 Agent 启动前要经过 profile/model/owner
校验，worker 完成后通过结构化结果和通知回到控制面，resume 不能绕过 owner
和并发约束，`unknown` 不能当成 `idle`。

ZaoFu 的对应边界保持如下：

```text
Requirement -> FlowSpec -> Proposal -> Plan / Task Map
                                      |
                         OA semantic decision
                                      |
                 admitted execution plan / delegation
                                      |
              Kernel + WRC validate and materialize worker
                                      |
        worker briefing -> provider turn -> typed result/evidence
                                      |
                    EventLog -> EventWatcher -> next route
                                      |
                 Verify / rework / integration / delivery
```

OA 可以选择语义路线、profile、角色偏好和 bounded action，但不能直接写
TaskStore、伪造完成或绕过 WRC 启动进程。WRC/Kernel 负责容量、租约、scope、
generation、worktree、生命周期、证据和外部副作用。

## Kimi Code 路线对账

普通 Agent 的有效路径是：

```text
主 Agent loop
  -> Agent tool
  -> planSpawn(profile allowlist / model / thinking / owner)
  -> spawn child scope + prompt prefix + permission/tools
  -> AgentTaskService(register / timeout / detached)
  -> child runAgentTurn(tool calls)
  -> mirror summary / usage / stopReason
  -> foreground result 或 background notification + resume hint
```

AgentSwarm 只解决同构任务的批量队列、并发、rate-limit 退避和 retry，不能替代
Task Map 的依赖图或 ZaoFu 的结果准入。

Tower 的有效路径是：

```text
/tower on -> TowerInit
          -> TowerPlan(mission / scope / dependency / task / context)
          -> TowerSpawn(branch / worktree / roster / briefing)
          -> tower-worker(background)
          -> TowerSend(in-turn steer 或 idle inbox)
          -> TowerComplete(task reconciliation / blocker / commit)
          -> independent TowerReview(exact branch tip)
          -> TowerMerge(worker idle / deps / scope / review / current tip gate)
```

Tower 的 mission 分解仍由主模型决定，协议 store 只负责确定性约束。因此它对
ZaoFu 的主要借鉴是 worker 生命周期和交付合同，而不是新增一个模型 scheduler。

## Worker Execution Envelope

每个 blocking Task Pipeline stage 在 `task.pipeline.stage.dispatched` 和 worker
briefing 中携带同一个 `worker-execution-envelope.v1`。该 envelope 是 operation
identity 的派发快照，字段包括：

- `identity`：Run、Task、stage、operation、dispatch、attempt、generation、placement；
- `routing`：role、backend、model、thinking effort、execution profile 和 digest；
- `workspace`：workdir、branch、base commit、target ref、workspace generation；
- `result_contract`：output profile、revision、成功/失败事件和 evidence 要求；
- `lifecycle`：是否允许 resume，以及 `unknown_is_not_idle=true`；
- `wake_contract`：完成、失败、stuck、respawn failure 和 idle probe 的事件路由。

Envelope 的目的不是保存 CoT，而是让 WRC 和 worker 对“谁、在哪个版本、用什么
profile、产出什么结果、失败后如何唤醒”使用同一个不可变输入。后续 role/profile
变化不能修改已派发 operation；必须通过新 generation 或受控 rework。

## 唤醒与异常语义

Kimi 的 background notification、TowerSend 和 `TaskTerminatedNotice` 映射到
ZaoFu 的 EventLog/EventWatcher：

```text
stage dispatched
  -> worker busy + heartbeat
  -> typed result / worker.stuck / respawn.failed
  -> EventLog
  -> EventWatcher wake
  -> WRC 重新计算 ready / blocked / retry / rework
```

`worker.probe.idle` 只表示“存在 idle worker 且有 ready backlog”，不直接改变任务
状态；真正派发仍由 Kernel/WRC 完成。worker runtime 无法权威观测时必须保持
`unknown`，不得满足 merge、integration 或 delivery 的 idle 条件。

## CoT 与证据边界

Kimi 将 thinking delta 标记为 hidden/encrypted/reasoningKey，并且 parent 只接收
child 的 summary、usage 和 stop reason。ZaoFu 沿用同一边界：

```text
provider transcript / CoT -> sidecar 或诊断投影
typed worker result       -> result admission
EventLog / TaskStore      -> canonical runtime truth
```

CoT 可以帮助 OA 做诊断，但不能单独证明 task 完成、Verify 通过、branch 可合并或
delivery 可执行。完成必须依赖 typed result、evidence refs、current generation、
exact target 和既有 gate。

## 非目标与 rollout

本设计不引入 Provider 原生 sub-agent、第二个 Task schema、第二个 scheduler、
模型学习型 router 或 raw CoT canonical store。当前默认路线继续由现有 Run Manager
推进；`normal_frontier_owner=wrc` 仍需要真正的 WRC consumer 和独立验证。

第一批实现只冻结 worker envelope，并验证现有 push wake、resume 和 unknown gate
的接线。后续真实 canary 要分别比较 shadow/blocking 的完成率、遗漏、错误路由、
重试、token/费用、尾延迟、人工介入、重复副作用和最终集成成功率；mock 通过不
代表真实 Provider 资格。

Task Pipeline 新 generation 在 admission 事件中固定 `frontier_owner`；运行和恢复前核对当前配置。历史 generation 若无该字段，按同代派发事件确定 owner；更早没有 owner 字段的派发归属于当时的 Run Manager 路线。无可用派发证据时保持阻塞，不能猜测或静默迁移。WRC 派发遵守全局暂停，暂停期间仍保留 legacy dispatch fence。

## 交付与验证入口

- `zf-prd-v5.yaml`：当前 v5-production PRD 配置；以 `5.0.0` 标识本次配置修订，继续使用
  `prd-flow-v4-task-pipeline` 的运行时 profile，使用 Codex backend、8 个 Impl lane
  和 8 个 Verify lane，固定 blocking。C 路线将
  `normal_frontier_owner` 设为 `wrc`：WRC facade 消费已接纳的 Task Pipeline
  ready-set，Run Manager 只保留异常恢复 owner；未接纳或非 blocking 输入
  fail-closed，不回落 legacy dispatcher。这个配置切换只对新 Run/generation
  生效，不能在活跃 Run 中途切换 owner。Shadow 观察必须使用独立 canary
  controller，不能通过生产配置环境变量覆盖。
- `src/zf/runtime/worker_execution_envelope.py`：envelope 构造、digest 和机械校验；
- `src/zf/runtime/task_pipeline_dispatch.py`：将 envelope 接入实际 stage dispatch；
- `src/zf/runtime/task_pipeline_briefing.py`：把同一 envelope 交给 worker；
- `src/zf/runtime/wrc_frontier_consumer.py`：C 路线的唯一正常前沿消费入口，复用
  现有 deterministic reconciler，不创建第二套 scheduler。

### C 路线已闭合与资格边界

已实现并通过 deterministic mock E2E 的闭合包括：WRC owner fence、WRC consumer
接线、operation/stage/envelope 的 `frontier_owner` 审计字段、重复唤醒幂等以及
非 blocking/未 admission 输入的 fail-closed。worker 的 typed result、lease、
generation 和 wake/recovery 仍沿用 194/195 的既有合同，异常继续交给 Run Manager
恢复。

这不等同于真实 Provider 资格。真实 Claude/Codex 进程的 spawn、取消延迟、宿主资源
和网络失败需要独立 tier；cold-start provider 未认证时只能记录为 qualification
HOLD，不能把 mock 结果升级为生产 E2E 证明。
- `tests/test_worker_execution_envelope.py`：schema、digest、wake/lifecycle 边界；
- `tests/test_task_pipeline_capsule.py`、`tests/test_task_pipeline_briefing.py`：真实
  dispatch/briefing caller 回归。

真实 Provider E2E、物理进程取消确认和跨路线收益仍以 194 的资格条件为准，不能由
本批 deterministic/mock 测试替代。
