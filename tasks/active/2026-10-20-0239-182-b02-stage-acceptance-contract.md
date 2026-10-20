# 182-B02 P1 · 局部实现完成与候选验收的契约兼容

> 状态: active
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B02。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：F04、D02；所有者：Runtime/契约；依赖：D02 确认。
- 先用混合 task_verify/candidate_verify/human AC 经实际 snapshot 与 handoff 路径复现 → verify: 明确哪些入口要求 self-check、哪些合法结果被拒绝。
- 对齐模板、schema、校验、角色提示和 Verify 消费语义 → verify: 本片局部通过可完成自身阶段，后续 AC 仍保持未完成且不可计入产品通过。
- 保留失败边界 → verify: 本阶段失败/缺证据仍拒绝；不能借另一阶段归属绕过本片验证。
- 覆盖单一 owner 的旧合同、可选 self-check、严格 typed result 及恢复重放 → verify: 原行为兼容，陈旧代际和错误目标仍拒绝。
- 覆盖最后候选检查 → verify: 缺少完整 AC 证据时，局部任务通过不能推导产品完成。
- 测试入口：`test_impl_self_check.py`、`test_writer_contract_handoff.py`、`test_typed_result_admission.py` 及相关 task pipeline 调用者。
- 不以关闭全局校验、把 mandatory 改成 false 或伪填 passed 作为修复。

## 实施记录

- 采用设计 182 推荐方向；公共契约修改先由实际 handoff 回归确定。
- 行为改动先补失败回归，再实现并记录聚焦验证；方法变更验证注入及示例。
- 完成状态以实际证据为准；产品 Plan 复审不授权产品 writer。
