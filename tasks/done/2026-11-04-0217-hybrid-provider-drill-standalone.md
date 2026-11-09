# 修复独立运行的混合 Provider 验证脚本

> 状态: done · 实施: `186783c3` fix: make hybrid provider drill evidence explicit

真实验证发现 `tests/e2e/generic_workflow_real_provider_drill.py` 复用 pytest 的模拟
场景，但独立运行时未装配 autouse 模拟控制面，试图启动没有 session.yaml 的
真实控制面，在真实 Provider 调用前失败。

1. 在脚本的混合模拟范围内显式装配隔离控制面 launcher -> verify: 不依赖 pytest
   fixture，直接调用场景能继续，脚本退出时恢复原 launcher。
2. 清晰标记控制面为 mock，Provider 校验保持真实并要求显式 `--confirm-real` ->
   verify: JSON 输出无真实控制面误导；原验证合同、临时目录清理与 signal 测试通过。
3. 用确定性测试及一次独立真实 Provider 运行验证 -> verify: 从脚本入口走到
   实际 Codex Verify 并完成，报告真实和模拟范围。

## 实测补充

启动后真实 Verify 拒绝原合成报告：报告只声称两个来源支持结论，没有具体问题、
答案、来源引用或证据内容。保留拒绝并改进合成证据、将两条原始记录纳入读取合同；
不得要求模型伪造通过。输出 schema 约束 verdict、action 与 claim 状态，但允许
有效拒绝和 gap，失败时保留模型结果及用量摘要以便审查。

## 验证记录

- `tests/test_multiflow_runner_cleanup.py` 与
  `tests/e2e/test_generic_workflow_complex_mock_e2e.py` 共 12 项通过；补充
  证据源回归 1 项通过。
- 脚本入口真实 Codex `gpt-6.1-sol`：旧空洞报告被拒绝并保存诊断；
  加入两份可追溯合成记录后，真实 Verify 通过，18,661 输入 / 840 输出 token。
  控制面与 replan 是模拟，只证明一次真实 Verify，未宣称真实产品端到端。
