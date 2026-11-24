# 任务接力可靠性：现场、当前责任与受控续读

本批沿 [Task Pipeline 上下文发布与恢复](task-pipeline-capsule-recovery.md) 增强已有入口。
产品范围为本地 `docs/spec/task-handoff-reliability.spec.md`（ZF-SPEC-002 r2）；
Spec 按仓库约定忽略，不作为运行时配置。通用可写子 SOP 不在本批范围。

## 任务现场归属

`task_workspace_binding.resolve_task_workspace` 是只读解析器，由维护保存、角色恢复目录及恢复 Git 上下文消费。
它先核对 canonical Task 的 assignee、Run、active dispatch，再核对已接纳的流程代次、配置工作区根目录、
目录存在性与任务工作区元数据。新格式派发缺失元数据时拒绝使用；无当前身份字段的历史记录保留兼容。
已识别为 Task Pipeline、但绑定无效的任务明确报错，不能回退到角色目录或主 checkout。
尚未进入 Task Pipeline 的旧流程继续使用原有角色目录规则。

根目录沿 `WorkdirManager` 的配置解析，因此支持自定义 `project.state_dir` 和 `runtime.workdirs.root`。
角色复用时只查当前分配给该角色的活跃任务；已结束 operation 的旧任务归属沿现有生命周期查询排除，
多个当前任务仍产生歧义时拒绝猜测。恢复执行不能复用已终止尝试；维护现场证据允许保留终止现场。

现场快照仍为有界 Git 证据：提交基准、暂存/未暂存 patch 与未跟踪内容；不复制忽略文件、Git 历史和运行时目录。
两次采集不同、超限或不支持的文件类型仍返回不可用并保留原目录。
快照的 `provider_quiescence` 仍为 `unverified`；目录解析和证据保存不授予 lease，也不代替旧 Provider 停止确认。
暂停、取消、互斥、结果接纳和恢复动作继续由原控制路径处理。

## 当前接力责任

`handoff_pipeline` 复用 `read_task_pipeline_projection`，不创建第二调度状态机。
Task Pipeline 的接力摘要、任务状态行和运行详情使用同一投影；
当前活跃 operation 决定责任，阶段间等待、阻塞和终态沿现有 reconciler 解释。
当前 Run 没有匹配的已接纳 generation 时显示未知，不借旧流程 `dev.build.done → static_gate` 映射猜测。
旧流程保留原有映射。

已接纳结果与阶段完成分开呈现：只有当前代次的 settled operation 列入接纳记录，显示阶段、尝试代次和 verdict。
旧完成事件及 Agent 自述仍可在历史中查看，不独立成为当前流程的完成证据。
旧 resume packet 的 gate 缺口不移植到 Task Pipeline；当前输入缺口使用既有 `current_task_input_recovery`。

从任务属性栏打开“查看任务事件”，展开该任务的运行详情即可检查交接依据与恢复条件。
其中列出当前运行和尝试、代码目标、任务现场、输入绑定、读取缺口与现场保存情况。
“查看输入与成果”按当前 pipeline attempt 读取输入。“查看交接历史”展开执行路线与事件。个别输入读取失败时，其余交接信息仍然可读。
展示历史已读时仍明确“当前会话保留未知”，不将保存、交付、读取、接纳和业务完成混为一谈。

## 显式续读试点

通用 `context_delivery` 仍是完整交付与影子比较。实际增量路径复用既有 Artifact CLI 的 `--required --resume`，
不新增全局开关，不修改活跃尝试的冻结输入，也不改变读取授权。

调用者仅在当前仍能使用已有材料时显式请求续读。运行时继续核验当前 Codex session、operation/attempt/dispatch、
输入 bundle 与摘要、角色/用途、每个别名的独立回执、完整工具输出和已知压缩边界。
只有每个别名都连续覆盖的前缀可跳过；未知 Provider、会话替换、证据缺失/损坏、截断或压缩后无新交付均从完整当前必要输入开始。
当前权限撤销、输入失效等仍先由原授权门禁拒绝，不能通过回退绕过它们。

显式续读的第一页增加兼容字段 `continuation`：

| 字段 | 含义 |
| --- | --- |
| `mode` | 实际 `resume` 或完整读取 `full` |
| `reason` | `observed_prefix` / `observed_complete`，或无法复用的机械原因 |
| `skipped_chars` | 本次跳过的正文字符数，不是 token 或账单费用 |
| `required_occurrences` / `distinct_bodies` | 必读来源数量与去重正文数量；别名授权和回执仍逐项保留 |
| `current_memory` | 固定 `unknown`，历史工具输出不能证明模型仍记得材料 |

回退原因包括 `unsupported_identity_or_provider`、`session_unbound`、`continuation_boundary_unavailable`、
`read_ledger_invalid`、`compatible_receipts_missing`、`current_session_delivery_unverified`、
`delivery_predates_compaction`、`coverage_gap` 和 `evidence_invalid`。
不使用 `--resume` 时保持原完整读取输出。新增元数据仍计入既有页面字节上限。

## 核验与限度

- `tests/test_task_workspace_recovery.py` 以真实 Git worktree 覆盖自定义根目录、角色复用、错误绑定、目录缺失、
  staged/unstaged/untracked 现场、维护保存、生命周期目录入口和重启重读；既有快照往返测试保留。
- `tests/test_handoff_pipeline.py` 通过持久 EventLog、TaskStore、原 pipeline projection 与实际 Web detail 入口，
  核验阶段责任、返工代次、迟到旧结果、等待、终态和重新绑定；不会修改 canonical 状态。
- `tests/test_artifact_required_resume.py` 从实际 CLI 与确定性 transcript fixture 核验跳过量、回退、关闭续读、
  多页、独立别名回执和正文重建；`test_task_scoped_plan_inputs.py` / `test_plan_input_review.py` 保持输入覆盖与冻结边界。
- `web/tests/task-handoff.spec.ts` 从任务详情进入该任务的事件页，检查交接依据、输入权限和重新展开时的最新绑定，
  并保留 `task-inputs.spec.ts` 的分页、权限、过期、部分/未知状态断言。浏览器 API 使用 fixture；后端数据合同独立测试。

这些是确定性与本地浏览器层级，不是实际模型记忆、真实 Provider 业务质量或 token/费用收益证明。
完整实际应用任务的成本/恢复轮数对照仍需显式真实 Provider 评估；本批不宣称已取得该收益。
本地执行记录、浏览器 trace/截图/WebM 和源码绑定收据存放在 `docs/records/task-handoff-reliability/`，不作为 canonical 运行状态。


## 与 R16 无人恢复实现集成

集成保留必读页的next_action与显式续读continuation，接力摘要同时保留认证receipt、当前pipeline和input_recovery。
现场解析统一为resolve_task_workspace，维护保存、重启目录及恢复Git采集均复用该入口；删除提前返回的第二解析器。
现代派发除了检查配置根目录和meta.json，还必须绑定canonical attempt/operation及不可变request，由既有
_operation_workdir核对Kernel dispatch、lease、placement epoch及执行信封。当前派发若被不可信事件覆盖，
明确拒绝，不能退回早期目录；require_live拒绝已终止operation，维护快照仍可保存终止现场。
无现代身份字段的历史派发保留既有有限兼容，不因此授予lease或允许写入运行时真相。

两分支的测试夹具已补齐真实请求/attempt/envelope及配置工作区元数据，避免用简化fixture迫使生产门禁降级。
验证与归档见../../tasks/done/2026-11-17-1444-continuity-integration.md。R16真实资格仍绑定原源码1bff811d，
不能自动转移为本集成提交的真实Provider/无人资格；本集成另行验证受影响的恢复、交接与浏览器路径。

### 集成验证与基线对账（2026-11-17 UTC）

相对 dev 0625d001 的自动影响闭包得到 13718 passed、19 skipped、18 failed；
相同失败节点在原 dev 独立导出目录重放为 15 failed、3 passed。新增的三项差异已修正：
历史多任务角色不再被 Pipeline 歧义保护误拒；中断回收夹具使用真实身份；必读重试夹具先取消原调用并按 task 释放角色。
其余 15 项为合并前已有问题，未删测试、未放宽生产门禁；以下节点仍待独立修复：

- `tests/test_config_profiles.py::test_prod_controller_profiles_wire_yoke_and_enforcement`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[general-workflow-v3-claude.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[general-workflow-v3.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[issue-fanout-v3-claude.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[issue-fanout-v3-oa-pilot.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[issue-fanout-v3.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[prd-fanout-v3-claude.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[prd-fanout-v3-oa-pilot.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[prd-fanout-v3.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[prd-light-v3-claude.yaml]`
- `tests/test_controller_blocking_rollout.py::test_production_controllers_allow_long_horizon_with_cost_and_usage_guards[prd-light-v3.yaml]`
- `tests/test_feishu_channel_progress_card.py::test_confirm_click_is_wired_to_real_controlled_action`
- `tests/test_wake_surface.py::test_wake_list_changes_are_explicit`
- `tests/test_workflow_synthesis.py::test_consumer_lock_contention_defers_without_reporting_failure`
- `tests/test_writer_task_map_fallback.py::test_failed_and_downstream_scope_maps_unique_successor_and_keeps_audit`

相对 R16 归档 ec56e54b 的集成闭包为 4442 passed、7 skipped、1 failed；唯一失败是
测试收集早于最后的 reader 夹具修正，修正后相关 17 项全部通过。原自动收据仍保留 failed，
不将分次复验冒充一次全绿。最终七个相关测试文件复验 105 passed。
Web typecheck、unit、build、53 项 sentinel、10 项 flow mock 均通过。
Docker 内 12 项浏览器业务断言通过，使用已有镜像 mcp/playwright:latest，
镜像 sha256:8342e03ef3571d09afffcd00558b8067e66489a54e7f3f0d96a71a327fd45fe2，
Chromium 149.0.7827.22 / Playwright 1.59.1。API 为 fixture，实际组件、截图/WebM/trace 保留。
原始日志、基线对账与媒体保留于集成 worktree 的 reports/continuity-integration-20261117/；
此次未新增真实 Provider 验收，不能据此扩大 R16 的资格范围。

当前流程投影用 `operation_status` 区分预定、执行和暂停。已接纳结果携带阶段、语义裁决及结算时间。详情通过同一项目的 TaskStore、FeatureStore 与任务事件生成只读 properties 投影，包含上下游、子任务、来源和最近的阶段变化。宽屏属性栏与窄屏抽屉共用该投影；当前绑定决定执行者，任务对话固定在正文栏下方。页面行为见[看板任务详情规格](../specs/kanban-task-detail.md)。

任务阅读页的「Task conversation」使用项目和任务绑定的 zaofu agent 会话。调整验收条目通过确认后的 `update-task` 提交，部分合同更新保留现有验证方式与验证层级。任务更新回执和执行者送达回执各自关联对应动作。见[任务详情与任务对话](../specs/kanban-task-detail.md)。
