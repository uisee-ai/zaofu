# 恢复效果跟踪与失联唤醒

> 状态: active

复用既有 RM、Supervisor 和 WRC 的受理、派发和终局回执，使恢复决定有人消费并能观察业务效果。

## 验收

- 复现已受理后无效果、已有后继仍执行、重复 tick 与 resident 失联 -> verify: 可恢复的缺口按原路径有界唤醒，进行中的工作和真实等待各自有可核对的责任人与证据。

依据为 8×8 原 Run 的七票保留、技术恢复和最终交付复盘，以及 [199 恢复职责](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。
