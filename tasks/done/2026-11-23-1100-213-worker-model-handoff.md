# 单 Worker 规划执行实验

> 状态: done · `022ad4e6 feat: deliver scoped workflow inputs and milestone recovery`

环回 HTTP Mock 验证同一 attempt 返规、输入选择、预算、已产生但结果未知的调用及独立 Verify 约束。模型耗时和质量收益留给真实 Provider 对照。详见[实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。

依据 [设计规格](../../docs/design/specs/213-zaofu-antigravity-borrowings.md)。P2 在同一 Worker 目标与权限内显式选择规划、执行、返规或单模型兼容路径。

- 用 mock provider 模拟偏差、重复失败与越权 -> verify: 不新增业务执行权，保留独立 Verify，并可区分模拟与真实效果。
