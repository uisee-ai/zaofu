# Run Manager 区分活动与任务交付证据

> 状态: done；实现 `4749850a fix: expose supervision evidence and unblock diagnosis`

## 证据

LS 两项任务长时间保持 heartbeat 和输入读取，但直至最终 blocked 才显现缺口；`supervisor_inspection.py::_stale_active_run_attention_items` 在近期 heartbeat 时不会产生 stale 判断。现有 OA 方法与 Task Pipeline summary 已分别说明任务状态、执行状态和就绪容量；本项不重复实现这两种投影，不以计时阈值擅自终止活动操作。

## 验收

1. 汇总 in-flight Task 有明确 task 身份的派发、活动、结果/进展信号 -> verify: 最近 heartbeat 与交付信号分别保留事件 ID 和时间；其他 task、未知身份不能冒充当前任务，缺失值明确为 unknown。
2. Run Manager 真实 projection 与 resident briefing 消费这些事实 -> verify: 有 heartbeat 无结果、独立结果、任务间隔离、运行中和已完成场景回归；读取或自报不视为独立验收。
3. 无新自动停止、超时、费用或并发限制 -> verify: 不更改配置与状态转移；实际 caller 测试及恢复合同闭包通过。

## 逐项实现核查（`dev@2a26c171`）

| 项目 | 当前实现与处理 |
| --- | --- |
| 首次 Impl 失败审查、attempt/role 丢失 | `orchestrator_agent_execution_review.py`、`orchestrator_agent_operations.py` 已接入；LS 恢复记录有重新检查点和升级事件。回归由现有测试覆盖，不重复实现。 |
| 裸人工升级挡住诊断 | 已有部分迁移例外；缺 no-progress 分支，单列同批任务修复。 |
| Task 与 execution 状态 | `task_pipeline_dispatch_summary.py` 和 CLI/API 已分列 task_status、pipeline_stage、execution_status；Kanban 生命周期列不应解释为 pipeline，补 resident 观察方法和本项事实，不改 canonical 任务状态。 |
| heartbeat 与实际结果 | Supervisor 的 stale 条件有近期 heartbeat 即返回；RM 既有 no-progress 属重复失败动作，不能证明长运行有效交付。本项添加任务级分离观察，不创建新终止/计费策略。 |
| 全局输入放大 | 版本绑定的 `plan_input_selection`、required-read alias 已实现；LS R2 未采用已准入的该选择，见 LS 任务耗时复盘。不能改在途合同或以现有功能推断新计划已审。 |
| 依赖图、共享工具 owner | `zf-plan-execution-readiness` 和 OA bounded-execution 已要求核对真实依赖及所有权；LS 的图和 runner 修复仍须受控计划、Candidate 集成和独立 Verify。 |
| 用户认证阶段决定 | Run Manager 传入最新目标、拒绝旧目标裁决的机制已有；LS 冻结合同尚未吸收 `evt-0fb9e2b35adb`，属于项目新计划工作，不能伪造生效。 |
| resident 重载和递归 | `bac1a330` 修复重载启动；`2a26c171` 修复标准实验内层 resident。历史问题不重复实现，不扩展声称至 legacy bypass。 |
| 诊断排队和陈旧性 | 已有 accepted/started/terminal 生命周期和部分后续进展替代判定；LS 旧 stage-replan 来源经通用触发包装，现有证据不足证明哪些可跳过。必须先对账来源链，禁止凭时间判过期或伪造终态。 |
| 费用和性能 | `workflow_budget_status.py` 对 unpriced USD 用 null；不能宣布免费。LS watcher CPU 热点尚无调用栈，沿用既有 `backlogs/2026-10-25-0845-runtime-wake-backlog-latency.md` 做可重复性能归因，不预设优化。 |

LS 原始证据：`docs/records/2026-11-02-ls-controlled-recovery.md`、`2026-11-02-identity-design-duration-review.md`、`2026-11-02-identity-design-complete-requirement-mapping.md`（位于 LS 项目）。本核查不等于 LS 当前 Plan 已批准、两项业务任务已完成或真实服务验收通过。

## 验证记录

- Run Manager、continuation、resident、Supervisor 和 mock E2E 定向闭包 387 passed；看板及 Task Pipeline 投影 20 passed。
- `dev-verify.py run --base dev --skip-tier deterministic`：serial 10、premerge sentinel 53、flow smoke 10 passed。规划器保守选择 349 文件的全量层，按 AGENTS 的影响范围规则以以上定向闭包替代；未运行 full pytest 或真实 provider E2E。
- 对 LS 历史事件只读重算：两项任务各有独立派发、heartbeat 与 `dev.blocked` 引用；不视为新业务结果或独立验收。
