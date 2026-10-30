> 状态: active

# Run 调度 owner 冻结

设计依据：194、195；dev 基线 d41ebdac。

## 范围

为已接纳的 Task Pipeline Run/generation 固定正常调度 owner；恢复漂移时拒绝或按明确兼容策略处理。

## 验收

1. 重启切换 owner -> verify: 双向漂移阻断，有派发证据的旧记录可恢复，未知旧记录保持阻塞。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。
