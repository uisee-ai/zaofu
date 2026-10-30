> 状态: done — fa4c3bee fix: harden dynamic workflow admission and dispatch

# Run 调度 owner 冻结

设计依据：194、195；dev 基线 d41ebdac。

## 范围

为已接纳的 Task Pipeline Run/generation 固定正常调度 owner；恢复漂移时拒绝或按明确兼容策略处理。

## 验收

1. 重启切换 owner -> verify: 双向漂移阻断，有派发证据的旧记录可恢复，未知旧记录保持阻塞。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。

## 验证

- 聚焦及 mock E2E：73 passed；变更后调度/恢复直接调用方：54 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed；`dev-verify.py run --base dev --skip-tier deterministic`：serial 10 passed；deterministic 全量回退未运行。
- Ruff 和 `git diff --check` 通过；未运行真实 Provider。
