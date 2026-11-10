# P2：真实 Plan Critic 的 Owner 检查点缺少候选 Plan 引用

> 状态: done

## 复现与证据

基线 `367b6586`，真实 Kanban UI 冷启动运行：
`/tmp/zf-prd-ui-real-kanban-20261110-120918/`。
Task `TASK-F6C7D4`，Run `prepare-TASK-F6C7D4-d364956070123b21`。
Planner 已生成 132 行候选完整 Plan；Critic 返回 blocked / needs_rework，要求回答 OWNER-GIT-01。
`approval.requested` 事件 `evt-c02571abe9bf`（12:40:02 UTC）包含 Owner 问题，
但 `plan_artifact_ref`、digest、task_map_ref、source_index_ref 都为空，content profile 为 derived_plan。
浏览器 `evidence/owner-checkpoint-before.png` 显示“完整计划不可用”，Approve decisions 禁用。
Owner 可通过 Reject 附带澄清，已由真实 UI 提交，并触发同 Run 自动 replan，未批准实施。

## 建议边界

调查 semantic result → fanout synthesis → owner checkpoint 的候选引用传递。
现有 `plan_preview_refs()` 只能读取 payload 和 previous_plan_candidate_refs；
不要通过猜文件名、任意扫描目录或把未审查候选自动升级为完整已通过 Plan 来修复。

## 验收

- 使用真实 synth adapter 产生带 Owner 问题的 rejected result → verify: 检查点携带已准入同版候选的 refs/digests。
- UI 打开检查点 → verify: 能读取实际候选全文，明确显示待决及未通过 Critic，不能冒充最终审批通过。
- 引用缺失或 digest 不符 → verify: 保持不可用，不读取其他 Run 的候选。
- UI Owner 答复 → verify: 原 Run 受控 replan，未生成第二次 invoke、未派发 writer。

## 本轮实施

实现与交互分析：[PRD UI 恢复](../../docs/impl/prd-ui-recovery.md)。
验证与保留失败：[验证记录](../../docs/records/2026-11-10-prd-ui-recovery-validation.md)。

实现提交：`9af9e1f3 fix: restore PRD workflow config handoff and review recovery`。

验证：定向回归 65 passed；调用方 80 passed；串行 10 passed；哨兵 53 passed；
两条 warm-runtime Docker 完整旅程均通过，Owner/Channel 交互验证通过。
广域回归 4850 passed / 9 skipped / 2 failed；两项失败在 dev 基线复现，详情见验证记录。
