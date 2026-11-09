> 状态: done — fa4c3bee fix: harden dynamic workflow admission and dispatch

# 动态续行 checkpoint 校验

设计依据：194、195；dev 基线 d41ebdac。

## 范围

校验 checkpoint ref、摘要及其 Run/父 operation/代际绑定，缺失和失配 fail closed。

## 验收

1. 有效与缺失、损坏、跨 Run、过期输入 -> verify: 动态续行真实入口测试。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。

## 验证

- 聚焦及 mock E2E：73 passed；变更后调度/恢复直接调用方：54 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed；`dev-verify.py run --base dev --skip-tier deterministic`：serial 10 passed；deterministic 全量回退未运行。
- Ruff 和 `git diff --check` 通过；未运行真实 Provider。
