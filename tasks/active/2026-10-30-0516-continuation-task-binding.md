> 状态: active

# 动态续行父子 Task 归属

设计依据：194、195；dev 基线 d41ebdac。

## 范围

校验 action Task 在父 operation 的授权范围内，支持合法 goal 级父 operation。

## 验收

1. 同任务和合法集合通过，跨 Task 拒绝 -> verify: 续行入口回归测试。
2. 状态、事件和恢复链 -> verify: 受影响 caller、共享合同和确定性 mock 测试。
