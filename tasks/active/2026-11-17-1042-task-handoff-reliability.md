# 任务接力可靠性：现场归属、阶段投影与受控输入续读

> 状态: active
> 来源: 2026-11-17 用户明确要求生成 Spec 后在新 worktree 实施；ZF-SPEC-002 r2。

Spec: [任务接力可靠性](../../docs/spec/task-handoff-reliability.spec.md)（按约定本地忽略）。
基线: `0625d001`；独立分支 `wip/codex-20261117-task-handoff-reliability`。

## 获准范围

- HR-01/02：统一有效 Task 工作区识别；修复维护保存按角色取目录及恢复硬编码 workdir 根目录。
- HR-03/04：现有接力摘要按当前流程和执行解释阶段，补齐成果、输入与现场的可见关联。
- HR-05/06：核验既有任务输入范围与去重；沿已有显式、同会话受控续读做可解释试点，保留完整输入回退和独立消费证据。
- HR-07 通用可写子流程为条件性后续，本批不启用。无推送、合并或部署授权。

## 验收

- 自定义 state/workdir 根目录、角色复用、维护保存与恢复 -> verify: 真实 Git fixture 保持当前任务的提交、暂存/未暂存及未跟踪成果；失效/冲突绑定明确失败，不落入无关角色目录。
- 实现、验证、返工、验收及旧结果迟到 -> verify: 当前 route/operation 的阶段和下一责任正确，旧流程兼容，重启投影可重建。
- 已有任务详情展示交接依据 -> verify: 当前身份、代码目标、现场与输入状态可关联，未知/部分/失败如实呈现；浏览器执行原有业务断言。
- 任务必读范围、重复别名与来源 -> verify: 现有覆盖不削弱，重复正文去重保留所有来源；冻结输入不被热改。
- 显式同会话续读及完整回退 -> verify: 实际 CLI 输入/输出证明重复交付减少；换会话、压缩、目标/权限变化、基线损坏、截断和停用不借历史回执跳过必要内容。
- 影响闭包 -> verify: dev-verify plan/run、机械 sentinel、相关 deterministic mock E2E；完整记录实际运行层级和限制，不把模拟 provider 视为真实模型验收。

## 实施与证据记录

实现说明：[任务接力可靠性](../../docs/impl/task-handoff-reliability.md)。

已修复 Task 工作区解析、当前 pipeline 阶段/接纳记录、详情交接依据和实际 attempt 输入入口；显式续读新增原因、跳过量和来源/正文计数。HR-05 的输入范围、别名授权、覆盖核验与冻结机制经源码和回归核对已具备，保留原实现；不制造重复功能。通用 Context Delivery 的 shadow delta 不转为默认实际增量。

## 验证记录

- 第一组回归在基线复现 8 个失败：错误角色目录、自定义根目录遗漏、角色复用和失效绑定不拒绝；修复后通过。
- 37 个文件的影响闭包：584 passed；最后调整投影/目录身份后的对应子闭包：165 passed。
- 输入交付附加合同：73 passed；指令合同：8 passed；机械 sentinel：53 passed；现有 Flow deterministic mock E2E：10 passed。
- `uv run python scripts/dev-verify.py plan --base dev` 完成。规划器将 316 个关联文件升级为全量；按当前 AGENTS.md 不以文件数触发 full pytest，执行下列显式影响闭包替代；`run --base dev --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-handoff-dev-verify.json` 的 Web typecheck/unit、sentinel 和 Flow mock tiers 全通过。没有把未跑全量/serial 标为通过。
- Web `npm run build`（含 TypeScript 与 bundle budget）通过。Docker Chromium 149.0.7827.22：原 `task-inputs.spec.ts` 8 passed，新增 `task-handoff.spec.ts` 1 passed；API 使用 fixture，后端 detail/投影和 Web API 合同单独核验。初次容器 UID 错误及夹具缺少历史 route 字段已修正重跑，失败记录保留。
- 实际 CLI 对照：完整首屏 8191 B；同会话完整输出已观察后的续读 435 B、跳过 10000 字符；独立拼接重建正文完整，关闭续读重新从 0 开始。不是 token/费用或真实 Provider 业务效果证明。
- 本地证据：`docs/records/task-handoff-reliability/` 的日志、路径清单、CLI 对照和 provenance；浏览器保存 trace、截图和 WebM，记录镜像 ID、浏览器/Playwright 版本与最终实现提交。
- 未运行 full pytest、host 或 real_provider 层级。HR-06 真实应用成本/恢复轮数及模型质量对照保留为真实 Provider 评估条件，未扩大本次确定性试点承诺；HR-07 在出现具体可写子流程复用需求后再确认。

影响闭包命令（非全量）：

```bash

uv run pytest \
  tests/test_task_workspace_recovery.py \
  tests/test_maintenance_worktree_checkpoint.py \
  tests/test_context_recovery_continuity.py \
  tests/test_task_pipeline_recovery_briefing.py \
  tests/test_task_pipeline_worker_liveness.py \
  tests/test_orchestrator_lifecycle_handlers.py \
  tests/test_lifecycle_fanout_child_queries.py \
  tests/test_runtime_maintenance.py \
  tests/test_control_actions_recovery.py \
  tests/test_spawn_coordinator.py \
  tests/test_recovery_briefing.py \
  tests/test_recovery_briefing_extended.py \
  tests/test_recovery_checkpoint_binding.py \
  tests/test_task_workspace_binding.py \
  tests/test_workdirs.py \
  tests/test_task_pipeline_capsule.py \
  tests/test_handoff_pipeline.py \
  tests/test_handoff_summary_projection.py \
  tests/test_task_pipeline_projection.py \
  tests/test_task_run_panel_projection.py \
  tests/test_web_server.py \
  tests/test_artifact_required_read.py \
  tests/test_artifact_required_resume.py \
  tests/test_artifact_read_capability.py \
  tests/test_artifact_input_recovery.py \
  tests/test_artifact_output_identity.py \
  tests/test_task_scoped_plan_inputs.py \
  tests/test_plan_input_review.py \
  tests/test_event_contracts.py \
  tests/test_events_log.py \
  tests/test_sidecar_refs.py \
  tests/test_workflow_operation.py \
  tests/test_runtime_orchestrator.py \
  tests/test_role_lifecycle_runtime.py \
  tests/test_idle_context_recycle.py \
  tests/e2e/test_design199_recovery_mock_e2e.py \
  tests/e2e/test_stale_contract_handoff_mock_e2e.py \
  -q --no-cov -n 4 --dist loadfile -m "not host and not real_provider and not serial and not perf"
```
