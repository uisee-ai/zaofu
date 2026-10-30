> 状态: done — fa4c3bee fix: harden dynamic workflow admission and dispatch

# 阶段快照大小一致性

设计依据：194、195；dev 基线 d41ebdac。

## 范围

统一阶段快照写入与读取的容量合同，在接纳前拒绝无法恢复的定义。

## 验收

1. 接近上限的快照可恢复，超限在接纳前失败 -> verify: 绑定和 caller 测试。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。

## 验证

- 聚焦及 mock E2E：73 passed；变更后调度/恢复直接调用方：54 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed；`dev-verify.py run --base dev --skip-tier deterministic`：serial 10 passed；deterministic 全量回退未运行。
- Ruff 和 `git diff --check` 通过；未运行真实 Provider。
