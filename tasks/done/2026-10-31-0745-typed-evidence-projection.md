> 状态: done — e9e18f51 fix: preserve structured evidence in result projections

# 结构化证据引用提交失败

Owner 已授权监工记录并修复运行问题。dl 的 pre_impl Orchestrator 已完成独立检查并生成 adopt 决策，但语义结果提交在兼容投影使用 dict.fromkeys 去重时抛出 TypeError: unhashable type: 'dict'，结果没有入账。原 scratch 与必读证据保留，不能伪造完成或改写 Plan。

- 在隔离 worktree 复现真实类型 -> verify: 三类语义结果、交付工件合并与 public submit 共 5 个回归先失败。
- 保留结构化引用并按完整值稳定去重 -> verify: digest、嵌套 scope、同路径不同版本及原顺序均保留，不修改输入。
- 从真实 Orchestrator 提交入口验证 -> verify: 有效 read ledger 下结果被接纳、operation settled，hydration 保留证据描述符。
- 完成调用闭包和 mock E2E、sentinel 后提交归档、合并并清理本 worktree；用受控提交入口恢复原决策，再继续 Plan 审批与实施。

实现说明：[结构化证据兼容投影](../../docs/impl/typed-evidence-projection.md)。

验证：原版 5 failed；修复后新回归 5 passed，dev-verify 自动 50 文件调用闭包（包含 mock E2E）835 passed，sentinel 51 passed、flow smoke 7 passed；Ruff 与 diff check 通过。真实 dl 原 scratch 的 10 条结构化引用完整保留；只读复现证据 /tmp/zf-typed-evidence-live-repro-20261031.json。自动测试日志 /tmp/zf-typed-evidence-verification-20261031.log。未运行 full pytest 或新增真实 provider E2E。
