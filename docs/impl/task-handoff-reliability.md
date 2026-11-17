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
Task Pipeline 的接力摘要、任务详情 route 当前阶段和 run header 使用同一投影；
当前活跃 operation 决定责任，阶段间等待、阻塞和终态沿现有 reconciler 解释。
当前 Run 没有匹配的已接纳 generation 时显示未知，不借旧流程 `dev.build.done → static_gate` 映射猜测。
旧流程保留原有映射。

已接纳结果与阶段完成分开呈现：只有当前代次的 settled operation 列入接纳记录，显示阶段、尝试代次和 verdict。
旧完成事件及 Agent 自述仍可在历史中查看，不独立成为当前流程的完成证据。
旧 resume packet 的 gate 缺口不移植到 Task Pipeline；当前输入缺口使用既有 `current_task_input_recovery`。

任务详情新增可折叠“交接依据与恢复条件”：当前运行/尝试、代码目标、任务现场、输入绑定、读取缺口和现场保存。
从该区域可进入既有 Evidence / Activity，再返回 Summary。查看不发送控制动作；部分输入失败不遮蔽其他状态。
Evidence 优先使用当前 pipeline attempt，避免把 dispatch ID 当成输入 attempt ID。
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
- `web/tests/task-handoff.spec.ts` 覆盖现有 TaskDetail 组件的 Summary → Evidence → Summary → Activity 旅程，
  并保留 `task-inputs.spec.ts` 的分页、权限、过期、部分/未知状态断言。浏览器 API 使用 fixture；后端数据合同独立测试。

这些是确定性与本地浏览器层级，不是实际模型记忆、真实 Provider 业务质量或 token/费用收益证明。
完整实际应用任务的成本/恢复轮数对照仍需显式真实 Provider 评估；本批不宣称已取得该收益。
本地执行记录、浏览器 trace/截图/WebM 和源码绑定收据存放在 `docs/records/task-handoff-reliability/`，不作为 canonical 运行状态。
