> 状态: done — 52ccf954 feat: apply OA delegation and feedback in task pipelines

# OA + WRC 委派、证据回流与受控调整闭环

用户已批准上一轮建议的 1–4 项；基线 dev c6a92a2f。独立 worktree 实施，不修改 loshu-fms 活跃 Run 的冻结配置或运行状态，不自动合并 dev。

## 目标和边界

OA 负责语义委派和调整建议，WRC 负责确定性校验、派发与状态。沿用现有 execution plan、checkpoint、事件账本、审批和 Task contract；不引入第二任务图或逐任务全局 LLM 屏障。正常结果在已配置聚合/关闭检查点批量回读，异常通过现有 semantic_failure/Run Manager 路径处理。

## 已批准 backlog

- [x] B1：接通当前 generation 的 OA delegation 到 v4 实际角色选择；明确 OA 偏好、会话亲和性、忙碌/能力不匹配回退；记录分配依据。
  - step -> verify: OA 指定第二角色时真实 runtime 派发第二角色；忙碌时可用兼容角色继续；旧版本/损坏工件不被静默采用；并行槽、能力和依赖约束保持。
- [x] B2：Impl/Verify/Judge 结果回流到 OA 证据读取，按 Run、任务及 generation 隔离，避免旧 attempt、跨 Run 和 OA 自己的结果污染。
  - step -> verify: checkpoint required reads 包含当前执行结果；其他 Run、旧 generation 和被替代 attempt 不作为当前结果；重启重放不重复创建同一检查点。
- [x] B3：验证并修复 OA 受控调整路径：精确目标返工/回计划，拒绝过期决策，保留运行中及已完成任务和审批边界。
  - step -> verify: canonical OA decision -> admitted controlled action -> WRC；过期 attempt/generation 被拒绝；重放无重复派发；局部计划修订走既有评审与接纳链。
- [x] B4：入口级确定性 mock E2E 与影响闭包验证，证明决策影响真实派发而非只写 artifact；记录验证范围与真实 Provider 层区别。
  - step -> verify: 定向回归、dev-verify plan/run、premerge sentinel、相关 mock E2E 通过；不把 mock 描述为真实 Provider 验证。

## 验证与收尾

先复现 delegation 未消费，再实施；新模块不超过 1000 行。更新相关实现/手册说明，记录命令、结果、限制和提交。实现提交后补写短 hash，归档到 tasks/done。保留可审查 worktree，不 push。

## 实施及验证证据

- B1：基线回归实际复现 OA 指定 `dev-2` 却选择 `dev-1`；修复后 `admitted_pipeline_delegations` 接入 runtime 与只读队列投影，实际 operation/派发事件保存 `orchestration_assignment`。
- B2：当前 generation/operation 的 Impl、Verify 与 Judge 结果进入聚合/关闭检查点 required reads；排除跨 Run、无 Run 身份、旧 attempt，保留局部修订中未替换任务的证据。
- B3：已应用且匹配当前 settled Impl 的精确 OA rework 转为 v4 下一 operation；不调用 legacy 直接派发。受限于原返工上限，过期/活跃 successor 不重派。回计划保持原 Planner/审批边界。补修恢复请求错误回退到其他 Run 的 Plan。
- B4：`tests/e2e/test_oa_wrc_dispatch_mock_e2e.py` 的 1/8 lane 两场景通过：真实 Orchestrator `_dispatch_ready`、隔离 Git worktree、实际 briefing/operation/TaskStore、记录型 transport；重建 WRC 后不重复派发。`tests/test_orchestrator_agent_results.py` 验证三阶段证据实际进入必读清单。

验证命令与结果：

1. `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_task_pipeline_orchestration.py --tests tests/test_orchestrator_agent_results.py --tests tests/test_task_pipeline_oa_rework.py --tests tests/e2e/test_oa_wrc_dispatch_mock_e2e.py`：完成影响闭包规划。
2. 同参数 `dev-verify.py run --workers 4` 的旧文件数阈值把 81 个文件扩大到全量；按 AGENTS.md 停止该扩大执行。随后调用同一 planner/run_plan，保留全部 81 个 selected_tests，将 `pytest tests` 替换为逐项文件列表，4 worker 分片；没有删减任何所选文件，也未修改仓库验证选择器。
3. 确定性影响闭包：1343 passed / 8 skipped；串行状态锁测试：10 passed。
4. `bash scripts/dev-premerge-gate.sh`：manual docs currentness ok，51 passed。
5. `bash scripts/run-flow-smoke.sh`：10 passed。
6. 本批文件 `ruff check` 与 `git diff --check`：通过。

本地完整回执：`/tmp/zf-oa-wrc-20261025T234500Z/verification.json`；日志 `impact-verification.log`；执行范围修正脚本 `run-impact.py`。以上为定向闭包与 mock E2E，不是全量测试或真实 Provider E2E。

部署边界：未修改 loshu-fms 活跃 Run 的配置/事件，未重启服务、未合并 dev、未 push。需在运行配置中显式启用 blocking pre_impl 等相应 checkpoint 后才能采用 OA 决策。图变化/读者重绑定仍走计划修订，不能绕过审批或失败证据。
