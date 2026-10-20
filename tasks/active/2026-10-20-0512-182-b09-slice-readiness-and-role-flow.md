# 182-B09 · 切片执行准备与真实Plan角色图

> 状态: active
> 来源: 设计182及本轮用户批准的补充修复；沿用独立worktree，不自动批准产品Plan。

## 范围

- 大任务给片内TDD顺序/返工边界；保持事务单一owner，早期UI行为有本片可执行验证。
- Q项登记阶段、逻辑责任人与关闭证据，未知具名负责人显式待指定；不猜测业务决定。
- 核对当前PrdFlow角色、扫描并行、Planner/Critic串行及返工链，纠正PRD技能过时三角色说明。

## 验收

- 配置编译/实际事件对照 → verify: 2扫描+Planner+Critic；本项目另有非阻断的orchestrator shadow，Kernel不计Agent；图与当前路由一致。
- 新Plan复核 → verify: 近期阻塞决定与局部UI验证明确；不依赖未来片完成本片验收。
