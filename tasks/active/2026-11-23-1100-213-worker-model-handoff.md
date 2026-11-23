# 单 Worker 规划执行实验

> 状态: active

依据 [设计规格](../../docs/design/specs/213-zaofu-antigravity-borrowings.md)。P2 在同一 Worker 目标与权限内显式选择规划、执行、返规或单模型兼容路径。

- 用 mock provider 模拟偏差、重复失败与越权 -> verify: 不新增业务执行权，保留独立 Verify，并可区分模拟与真实效果。
