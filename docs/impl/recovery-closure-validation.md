# 局部重规划的断点恢复闭环

2026-11-02 在隔离 worktree 验证，基线为 `451d0b30`。实际 DL 的恢复、审批与服务部署保持暂停。

## 复现与修复

Task Pipeline 可以保留未修订任务的旧代次。原冻结入口却按 `generation_id` 分组，仅检查最新组内的任务：基础任务从 g1 修订为 g2 并完成后，两个仍属 g1 的下游刚开始执行，便产生了 `candidate.ready`。这是提前进入全产品验证的 P1 缺口，不是当前 DL 已发生该事件的声明。

冻结入口改为检查同一 Run 的全部当前任务。先与事件账本投影核对任务成员和代次，再分别使用每个任务自己的代次校验集成收据、活动操作、attempt 与会话归档。所有收据必须属于同一候选分支，收据中的候选提交必须包含在最终候选历史中。最新 Plan/package 仍标识本次冻结；旧收据不改写为新代次，冻结的候选代次取最终集成收据的实际值。重复重放只产生一份冻结结果。

## 隔离验证边界

`tests/e2e/test_replan_checkpoint_closure_mock_e2e.py` 使用真实 Orchestrator、Git worktree、TaskStore、受控结果提交、必读账本和候选集成；Provider transport 与 OA/Planner/Verify 的语义产出由 mock 提供。Planner/Critic 交付已接纳 Package 是测试边界，未调用真实模型，也不证明真实长输入 Critic 能及时完成。

主场景实际执行以下链路：

1. 基础任务提交代码；全产品命令因两个依赖它的下游文件尚不存在而失败，Impl 提交 blocked。
2. OA 经结果准入提交 `return_to_plan`，仅产生一次重规划请求；此时没有新增实施调度。
3. 局部新合同以完整 `base_commit` 和 `source_refs: [git:<commit>]` 绑定已有代码。全产品验收及命令保留，责任移至 candidate_verify；全局 static 配置保持原值。
4. 新 worktree 从检查点开始，使用新 operation、合同快照、必读账本、自检和独立 Verify 结果；旧 worktree 和失败历史保留。
5. 基础任务集成后，两个旧代次下游实际收到不同角色的派发，且工作区均含基础代码；它们未完成时不得冻结。
6. 两个下游通过独立验证和串行集成后，三个任务全部 done，产生一次含三份收据的 `candidate.ready`；重启/重复 tick 不重复派发或冻结。

这里的 task done 和 candidate ready 不是最终产品验收、Judge 或 Goal 完成。

另两个反例证明：仅修改完整 Task Map 不会扩大 `failed_children_only` 的派发选择；其他变更任务必须显式纳入接纳范围。运行中 request 调用启动前的 `workflow-config-apply` 会被拒绝，配置文件不变；不能据此放宽该入口的状态检查。

## 对 DL 恢复输入的约束

恢复方案必须分别列出：诊断失败任务、实际修订任务、每个任务保留的代码检查点、仍沿用的合同代次，以及哪些证据需要在新身份下重新生成。Foundation 与 Assembly 同时修订时，Foundation-only 的选择不足以接纳全部变更。

本次 mock 证明 v4 的局部验证可以在保留全局 static 命令的情况下继续，因此删除全局检查不是普遍的恢复前提。实际 DL 若仍声明配置变更，须先修订/验证该声明的必要性和正式生效路径；不能把配置候选文件或重启当作运行中批准配置已更新。

Provider 非重试拒绝与产品语义失败分开处理。`tests/test_provider_turn_error.py` 覆盖拒绝保持 blocked、不重派、不产生 Verify 成功或 task done；`tests/test_workflow_operation_resume.py` 覆盖可恢复中断保留 request 身份及已完成兄弟任务。此次修复未改变这些边界。

## 复验

执行 `scripts/dev-verify.py plan/run --base dev`，显式纳入本 E2E、`tests/test_candidate_incremental.py`、`tests/test_task_pipeline_generation.py`、`tests/test_workflow_operation_resume.py`、`tests/test_provider_turn_error.py`、`tests/test_task_pipeline_targets.py`、`tests/test_task_pipeline_oa_rework.py` 和 `tests/test_task_pipeline_integration_evidence.py`；合并前执行 `bash scripts/dev-premerge-gate.sh`。

最终影响闭包另纳入 candidate reader、candidate verification stages 和 workflow projection：22 个测试文件、193 项通过，其中含本场景 3 项及八槽位 mock E2E。哨兵门 53 项通过，manual docs currentness 通过。首次结构检查发现模块超过 1000 行，去除重复参数与中间变量后复跑通过，未增加豁免。没有执行 full pytest、浏览器或真实 Provider 测试。
