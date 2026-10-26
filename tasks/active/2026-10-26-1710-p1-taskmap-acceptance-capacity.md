# P1 Task Map 负向验收与并行容量可观测性

> 状态: active

## 问题
Task Map 虽有 TDD/SDD 入口，但 unavailable、retry、幂等、部分接入等业务负向路径没有独立验收；capacity=8 也没有解释为何实际 active worker 只有 3 个。

## 范围
- 生成/校验任务 contract 时要求外部依赖任务声明负向验收命令和 owner。
- Projection 明确 ready、blocked_by、active、capacity_available 的差异。
- Verify rejected 时展示具体缺口和下一步 stage。

## 验收
- step -> verify: 缺失 negative-path acceptance 的外部依赖任务被 plan gate 拒绝。
- step -> verify: projection 可解释 active=3/capacity=8 的原因。
- step -> verify: rejected task 的语义缺口可在 dashboard/read model 查询。
