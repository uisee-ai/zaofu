# ZF-SPEC-001：连续执行首批实现

本记录对应用户批准的 `docs/spec/zaofu-continuity-and-handoff.spec.md` r1，
SHA256 `f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
实施基线为 dev `0625d001`；完整覆盖与限制见
[验收对账](../../reports/continuity-handoff-20261114-review.md)。
本批是增量修复，**不表示 18 项规格或 Datal 产品验收全部完成**。

后续实现：[不可变交接与目标回执](../../reports/continuity-handoff-20261115-review.md)。
`task_pipeline_dispatch` 在冻结请求前写入 `task-handoff.v1` 并注册必读来源；
`artifact handoff-accept` 复用 operation 凭据与读取账本，事件保存接收事实；
`call_result_admission` 对新交接要求当前回执，Task Detail 展示独立接收状态。
该链路不改变 Task 完成、暂停或权限归属，也不替代旧 writer 隔离。

## 入口、责任与行为

| 改动 | 实际生产 / 消费入口 | 不授予的资格 |
| --- | --- | --- |
| 运行版本概况 | `load_receipt` 已有启动回执 → `runtime_version_projection` → `metric_views.pulse` / legacy pulse → `PulseBand` | HEAD 相同不证明脏代码相同、进程仍活着或每个 worker 已加载；任务采用明确显示 unverified |
| 当前执行的进展 | `task_evidence_signals` → Run Manager context；`CallResultAdmissionService` 补齐 kernel origin、attempt/dispatch | activity、input observation、work record、verification、admitted result 分开；准入不等于业务通过 |
| 有限输入恢复 | `build_input_recovery` → CLI required-read 分页及 next_action | 历史交付不证明本轮记忆；恢复文本不授予新权限，也不撤销有效原权限 |
| 输入与任务切分方法 | `zf-plan-execution-readiness` → 新增参考方法，三个 skill 副本同步 | 不用字符门槛裁决任务语义，不擅自把 required 改 optional |
| Setup 准备 | `TaskWorkspaceManager.prepare` / `WorkdirManager` → `run_project_setup` | 同一 readiness marker 的 installer 互斥不等于全部执行者/共享环境互斥 |
| 正确工作区检查点 | maintenance controlled action / checkpoint → `checkpoint_root` → 当前 operation immutable request + kernel placement | snapshot 不证明源进程停止；身份损坏时不回落到旧 role checkout |
| 保守清理 | task cleanup、role retire、`zf stop` → `workspace_retention_reason` | 未知 ignored 文件、外层成果、stop 时未合入的提交保留；不据此宣称全资源图已实现 |
| 回合结束接续 | `Orchestrator.run_once` → `run_replay_sweep`，先回放结果，再 `reconcile_completed_turns` | 当前只自动处理有确切 turn identity 的 Codex operation-v2 sent attempt |
| 同 attempt 发送 | lifecycle nudge → `DispatchContext.continuation` → authority bridge → 原 transport | 不创建新 attempt、不接管 lease、不重启缺失 worker，不绕开预算/暂停/owner/operation |
| 接续的发送边界 | `claim_completion_nudge` 在发送前持久 claim；进展投影交给 Run Manager | unknown 不是失败，也不是未发送；不盲重发；已送达不是成果已验收 |
| 工作记录推进 | `progress_nudge_state` → 校验当前 work record 的身份、正文 digest、证据 refs | 同一记录换发布事件不算新进展；记录只允许提醒，不允许结算 |
| 旧 writer 退出 | `_activate_task_stage_binding` → terminate → is_alive 确认 | 仍活着/观察失败则 `waiting_for_worker_exit`；此 adapter 观察不证明脱离父进程的外部 writer 已消失 |
| 大 Plan 阅读 | `PlanMarkdownDocument` → 共享 Markdown AST，首批 4 页，靠近底部增量 2 页，手动继续及目录定位 | 全文下载/原文仍保留；当前后端仍取得完整正文，不宣称网络按章节读取已实现 |

## 接续事件与兼容性

新增非唤醒事件 `worker.continuation.claimed`，通过 EventWriter 写入，包含当前
task、attempt、dispatch、输入读取进度及恢复责任。它是发送尝试前的持久事实，
不是第二份 Task 状态。existing `worker.stuck.recovered` 留下 prompt_injected/error；
对这条续接路径明确写 kernel origin。Run Manager 可见 unknown/delivered 与下一核查动作。

中断发生在 claim 与发送之间时保留 unknown；发生在发送后而回执前也保留 unknown。
两种情况都不能用重发猜测外部效果。新的读取覆盖或同身份的新工作记录可推进提醒边界；
重复报告、旧 attempt、暂停、未决审批/求助及在途工具不推进。下游独立结果准入照常生效。
没有新增 token/费用限制，没有新的语义裁判或第二套任务 schema。

`workflow.call.result.admitted` 增加 origin 与从已验证 envelope 提取的 attempt/dispatch，
保留原事件语义和所有既有字段；旧缺身份事实不能充当新 attempt 的新鲜进展。
metric facts 缓存版本升级以保留启动回执引用，旧缓存可重建。

## 验证与未完成范围

采用影响闭包 pytest、sentinel、Flow mock E2E、前端 build/typecheck/unit 和 Docker Playwright。
模拟 worker 的并行区间由真实子进程记录；它不证明真实模型吞吐。故障测试包括：
失去回合通知、控制器重建、发送中断、暂停/审批/求助/长工具、旧身份、重复工作记录、
setup 并发/输入改变/失败失效、错误 checkpoint 路径、ignored 成果及旧 writer 拒绝退出。

完整命令、结果及 207–209 / F01–F18 矩阵保存在验收报告。尚需完成便携交接接收协议、
受控输入修订全过程、跨 Provider 真实接力、在用环境/资源持有者闭环、宿主级无人监工故障、
全量人类材料目录/后端按需读取以及 Datal 原始要求的产品资格。原 Datal 暂停保持。
