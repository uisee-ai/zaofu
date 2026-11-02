> 状态: active

# OA 阻塞发现、分诊、修复与恢复闭环

用户批准将本次五项建议作为一批实施；基线 `dev@81b8161e`。证据是 LS 身份 `evt-2b5f04c00d3b`、设计 `evt-603616ca8229`，以及任务执行快照和用户已确认的 Keycloak 本地用户阶段决定。此前两项任务没有独立 Verify，不能把局部结果算作完成。

## 五项工作和验收

1. [x] **首次阻塞发现**：只对已准入、当前 Run/generation/operation 的有证据失败进行分诊，保留其他故障归属和防重放。step -> verify: 真实 Run Manager tick 对首次 Impl 失败发出一次语义检查请求；缺目标、结果、证据或发生后继切换时失败关闭。
2. [x] **OA 分类与排队**：诊断资源繁忙时不能让无关当前 OA 语义审查无限饥饿；过期诊断可结算。step -> verify: 已占用的 Autoresearch 不阻塞独立 OA 审查；当前有效/已锚定诊断、熔断和人工门保持保护；旧 operation key 的过期诊断产生失败记录。
3. [x] **授权范围内修复**：OA 决策指出精确任务、失败证据、既有成果和核验要求；Kernel/WRC 消费已接纳裁决，而不是直接写状态。step -> verify: 首次失败经 OA 真实结果接纳后产生同任务下一代 Impl 返工，错误代次/活跃后继不派发，原合同与其他任务不被改写。
4. [x] **合同与用户决定**：合同/依赖冲突经局部回计划处理，现有用户确认及原最终验收须有来源，可复用已完成代码；不能让样本冒充真实验收。step -> verify: OA 对 return_to_plan 沿既有重规划、Critic 和批准边界传递裁决与原引用；Orchestrator role skill 明确修复与验收责任，生产 fixture 禁止及 LDAP 本地用户方案不被代码隐式改写。
5. [x] **验收、恢复与状态说明**：仅独立 Verify 和受控 Candidate 接纳成功后释放依赖，已就绪未执行应能检查具体卡点。step -> verify: mock 端到端及恢复/状态测试证明定向下一代派发、未验证依赖不提前释放、重复事件幂等、旧诊断可解释；更新实现说明并记录真实 LS 尚未重启或验收的边界。

## 验证与交付

先保存已复现的红灯，再运行受影响模块/直接调用者/共享合同、Run Manager、OA、Task Pipeline 回归，pre-merge 哨兵与确定性 mock E2E。`dev-verify` 因广泛依赖选中全仓时，遵循 AGENTS.md 的普通修复范围：明确跳过 broad deterministic、执行定向闭包以及其余自动层；记录精确命令、结果和未覆盖的真实 Provider 部分。不直接更改当前运行的 LS 状态、任务图或启动/中止服务；本批完成后提交并归档，不自动合并或推送。

## 实施与验证记录

- 首次 Impl 失败的 `workflow.call.result.admitted` 已定位到当前 operation/generation；由正常 Run Manager tick 建立 OA semantic_failure 请求，重放不重复。修改前该入口测试失败，修复后通过；不对一般失败放宽重复失败门槛。
- 有另一个 Autoresearch 请求时保留无进展诊断，允许独立 OA 语义审查；明确用户决策关口不被绕过。带 operation key 的过期诊断原本静默卡住，补失败结算及回归。状态解释改为实际选中动作，列出占用诊断槽的 request IDs。
- OA 审查读取当前 Run 最新 goal 及来源；绑定 goal 变化后拒绝过期裁决，以新目标重新排队一次。测试覆盖跨 Run、仅状态变化、检查点构建期间目标变化和去重。LS 只读重放获取 `evt-0fb9e2b35adb`，确认本地用户和 LDAP 的用户决定均在目标中。
- 定向端到端：首次失败 → 实际 tick → 检查点 → OA 接纳结果 → Task Pipeline 下一代目标返工；下游任务仍等待前置。OA `return_to_plan` 测试继续证明经受控计划路径传递，原验收不被自动删除。
- 定向命令：`PYTHONPATH=src:. /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_orchestrator_agent_execution_review.py tests/test_orchestrator_agent_execution_policy.py tests/test_orchestrator_agent_semantic_failure.py tests/test_orchestrator_agent_operations.py tests/test_orchestrator_agent_contracts.py tests/test_orchestrator_agent_results.py tests/test_run_manager_rework_triage.py tests/test_run_manager.py tests/test_run_manager_router.py tests/test_run_continuation.py tests/test_recovery_case.py tests/test_recovery_case_carriers.py tests/test_task_pipeline_rework.py tests/test_task_pipeline_oa_rework.py tests/test_task_pipeline_reconciler.py tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py tests/e2e/test_oa_bounded_dispatch_mock_e2e.py -q --no-cov` → 440 passed。
- `/home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run --base dev --skip-tier deterministic --receipt /tmp/zf-oa-recovery-verification-final-20261102.json`：serial 10 passed、premerge 53 passed、flow smoke 10 passed。计划把 325 文件膨胀为全仓确定性层；依 AGENTS.md 保留以上直接调用者/共享合同闭包而跳过该全仓层。首次误用 `--skip-tier python-deterministic` 后发现全仓层启动，立即中断；未将其记为通过。
- `tests/test_skill_provenance.py` 16 passed / 1 skipped；三个 skill 正文一致，ruff、`git diff --check` 通过。
- 真实 LS 事件只读重放：身份审查 `passed / auto_decide`、下一操作为 OA 审查；历史 Autoresearch 7 个请求仍未结算，设计语义重规划及人工升级未被绕过。未运行真实 Provider E2E、未重启 LS 或触碰其 canonical 状态；本批完成不等于 LS 两项业务任务恢复或通过。
