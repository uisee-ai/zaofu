# 同任务可信 session 断点恢复

> 状态: active

## 问题与目标

LS 恢复中已结算 operation、活动 session 和工作树容易混淆；复用同一 Codex/Claude session 需要可核验的任务、合同、计划、基线、角色、provider/session 和执行状态。只恢复可证明的同一执行，不重复已完成工作；来源不完整或不一致时保守回退，未知外部副作用不得盲目重做。

## 验收

- 同 Run、任务、角色、session、合同/计划及工作区基线一致，且原 operation 仍可续接 -> verify: 实际恢复入口保留 provider session 与有效断点，不重复派发活动 operation。
- session 缺失、身份或合同/代码发生变化、活动租约冲突、原 operation 已结算 -> verify: 不误判为同一活动执行，保留日志/工作树并按现有受控生命周期处理。
- 未知工具结果或外部副作用 -> verify: 不以 session/heartbeat 推断成功，不自动重复不可证明安全的动作。
- 定向恢复与真实调用方、mock provider、事件和状态合同 -> verify: 回归通过，读取兼容真实 provider 路径，不声称真实 provider E2E。

## 实施进展（2026-11-02）

- 已修复 Claude 在 worktree 启动时检查主项目目录 session 的问题，按真实 cwd 查找；独立测试覆盖正确与错误目录。
- 已修复停机后 `fanout_synth` 被错误取消：受控重驱原 operation/request，Provider 重启后重发原 briefing 并重新记录 running；重发前检查操作状态，恢复提示要求核对未知工具结果。
- 当前仅覆盖受控停止后的 reader / fanout synth operation 和其原有请求重驱；活跃租约、已结算同级及幂等重驱有确定性测试。现有会话定位不能证明任意任务的计划、合同和工作树基线仍匹配。
- 控制面自身的目标交付对未知副作用保守处理，有 `tests/test_goal_delivery_recovery.py` 覆盖；但 Provider 内任意工具调用是否完成无法由恢复提示机械保证。不得将提示词当作无重复副作用证明。
- 未完成：为各类可恢复写操作建立冻结合同/计划/工作区身份和 Provider 工具调用终结证据，无法证明时阻止自动重发并给出受控人工核对路径；再补跨变更、活跃租约、未知工具结果和真实调用方组合测试。真实 Provider E2E 尚未运行。

## 本次验证发现的既有问题

- `scripts/dev-verify.py run --base dev --workers 4 --keep-going`：14,066 通过、20 跳过、7 失败；串行层 10 通过。`test_feishu_channel_progress_card.py::test_confirm_click_is_wired_to_real_controlled_action` 在原始 dev 基线同样失败，需另行排查飞书受控动作的确认行为。
- `test_channel_group_product_mock_e2e.py::test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation` 在原始 dev 也失败：mock 流程启动检查依赖未登录的 Claude。需使 mock 环境不依赖真实 Provider 登录，或明确分离真实 Provider 层。
- 新 worktree 中四项 `test_export_public.py` 因未被 Git 跟踪的 `tools/export-public.sh` 缺失而失败；`test_prompt_source_closure.py` 因未跟踪的 `prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md` 缺失而失败。需修复新 checkout 的测试依赖闭包，不把本地忽略文件当成可分发输入。
