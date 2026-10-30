> 状态: done — fa4c3bee fix: harden dynamic workflow admission and dispatch

# 动态续行父子 Task 归属

设计依据：194、195；dev 基线 d41ebdac。

## 范围

校验 action Task 在父 operation 的授权范围内，支持合法 goal 级父 operation。

## 验收

1. 同任务和合法集合通过，跨 Task 拒绝 -> verify: 续行入口回归测试。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。

## 验证

- 聚焦及 mock E2E：73 passed；变更后调度/恢复直接调用方：54 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed；`dev-verify.py run --base dev --skip-tier deterministic`：serial 10 passed；deterministic 全量回退未运行。
- Ruff 和 `git diff --check` 通过；未运行真实 Provider。
