> 状态: done — d8ef5b08 fix: harden operator access and PRD evidence recovery

# 184 · P1：PRD 评估失联恢复

基线：`8e06db7f`，基于当前 dev 在独立 worktree 复验。

## 授权与范围

用户已授权“先生成 backlog，再实施”，本记录先候选后迁入 tasks/active。

允许修改：src/zf/runtime/channel_prd_evaluation_reactor.py；评估恢复模块、受控动作与周期 sweep；相关 tests/，以及相关 `docs/impl/` 说明。不修改实际运行状态。

实施说明：[边界修复](../../docs/impl/review-184-boundary-hardening.md)。

## 发现证据

dispatched 持久化后依赖 daemon 线程；失联重放跳过，显式 retry 对无终态请求返回 existing。

## 验收

1. 执行丢失与请求重放 -> verify: 失联请求产生明确失败终态、受控 retry 创建新请求
2. 存活执行与重复请求 -> verify: 不重复调用 provider；完成/失败终态保持幂等
3. 真实周期入口接线 -> verify: caller-level 测试及无真实 provider 的 mock E2E 通过

## 验证与收尾

先复现当前 HEAD，再实施；执行 dev-verify plan/run、影响闭包测试和适用 sentinel/mock E2E。实现 commit 后记录 hash，再迁入 tasks/done。仅本地提交，不 push/merge。

## 基线复现

新增缺陷测试在 `8e06db7f` 得到 16 failed / 4 passed；四项均复现，随后才实施。

## 验证记录

工作目录 `/home/min/workspace/public/184`；使用主 checkout 已安装的依赖，`PYTHONPATH=src` 确保加载本 worktree。

199 passed（Channel/PRD/API 聚焦）：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest -q --no-cov tests/test_operator_websocket_security.py tests/test_channel_prd_evaluation_recovery.py tests/test_channel_prd_quality.py tests/test_sidecar_refs.py tests/test_channel_prd_document.py tests/test_channel_prd_file.py tests/test_channel_synthesis_repair.py tests/test_channel_synthesis_recovery.py tests/test_channel_synthesis_routing.py tests/test_channel_discussion_driver.py tests/test_channel_discussion_revision_currentness.py tests/test_channel_discussion_reactor_liveness.py tests/test_channel_evaluation_feedback.py tests/test_output_evaluation.py tests/test_web_channel_delivery.py tests/test_channel_prd_evaluation_reactor.py tests/test_channel_prd_task_contract.py --tb=short
```

204 passed（共享契约与直接消费者，含两个 mock E2E）：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest -q --no-cov tests/test_artifact_read_ledger.py tests/test_artifact_read_capability.py tests/test_artifact_manifest.py tests/test_stage_reports.py tests/test_result_submit.py tests/test_workflow_operation.py tests/test_workflow_operation_resume.py tests/test_feishu_projection_sidecar.py tests/test_events_log.py tests/test_event_writer.py tests/test_state_locks.py tests/test_runtime_orchestrator.py tests/test_web_terminal_routes.py tests/test_channel_templates.py tests/e2e/test_channel_prd_evaluation_recovery_mock_e2e.py tests/e2e/test_stale_contract_handoff_mock_e2e.py --tb=short
```

155 passed（Web/API 直接调用方回归）：`tests/test_web_server.py tests/test_channel_router.py`。

147 passed（最终 sidecar 调整后的影响复核，与前组有重叠）：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest -q --no-cov tests/test_sidecar_refs.py tests/test_artifact_read_ledger.py tests/test_workflow_operation.py tests/test_channel_prd_context.py tests/test_event_writer_projectors.py tests/test_channel_templates.py tests/test_channel_discussion_driver.py tests/test_channel_synthesis_repair.py tests/test_web_channel_delivery.py tests/test_operator_websocket_security.py tests/e2e/test_channel_prd_evaluation_recovery_mock_e2e.py --tb=short
```

规划器的 410 文件候选超出当前缺陷的聚焦闭包，按 AGENTS.md 不执行全量 pytest；deterministic/serial 由上列聚焦回归替代（包含 state_locks）。未修改测试选择器。

```bash
python3 scripts/dev-verify.py plan --base dev
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run --base dev --skip-tier deterministic --skip-tier serial --keep-going --receipt /tmp/zf-184-verification-20261022/automatic.json
```

自动层结果：Web typecheck + test:unit 通过；premerge sentinel 51 passed；flow smoke 10 passed。真实 provider 和浏览器 E2E 未执行，WebSocket 使用真实 ASGI 路由与模拟终端验证。

## 完成结果

实现 commit：`d8ef5b08 fix: harden operator access and PRD evidence recovery`。四项任务均已通过验收；验证命令与结果集中记录于 PRD 评估恢复任务。
