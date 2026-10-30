> 状态: active

# 动态续行 checkpoint 校验

设计依据：194、195；dev 基线 d41ebdac。

## 范围

校验 checkpoint ref、摘要及其 Run/父 operation/代际绑定，缺失和失配 fail closed。

## 验收

1. 有效与缺失、损坏、跨 Run、过期输入 -> verify: 动态续行真实入口测试。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。
