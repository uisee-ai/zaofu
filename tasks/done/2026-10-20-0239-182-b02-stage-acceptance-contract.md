# 182-B02 P1 · 局部实现完成与候选验收的契约兼容

> 状态: done；实现：`9383191 fix: preserve bound candidate verification routes in readers`，前序实现见下。
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

- 实现：`e5cbc63 feat: scope completion evidence to acceptance stages`；
  `2353c80 fix: enforce verification stage authority at result admission`；
  `9383191 fix: preserve bound candidate verification routes in readers`。
- 实际 writer handoff 的合法未来 AC 先复现拒绝，再修复为 blocked；当前失败/缺证据仍拒绝，
  恢复重放保持 exactly-once，未放宽 frozen contract、attempt 或 target 绑定。
- 追加普通 Verify 7 个功能 RED、typed admission 7 个功能 RED，修复报告伪造 owner、
  未来 passed/waived 和 mandatory 缺失；真实 reader candidate 路由另先复现 2 failed 再修复。
- 最终影响闭包 867 passed，sentinel 51 passed，确定性三流 smoke 10 passed。
  没有关闭全局校验、降低 mandatory，或把局部通过算成产品通过。
- 详细版本/命令/RED-GREEN 见 `reports/plan182/tdd-evidence.json`、相关日志及
  [实施记录](../../docs/impl/plan182-review-and-evidence-closure.md)。
