# 182-B13 · 状态相关职责分离及权限断言一致

> 状态: done；实现 `ebacd0e fix: preserve directional permissions in task map reviews` 与 `2ea8371 fix: require coherent preconditions for negative test cases`。
> 来源: 最新54任务候选的正式Critic缺陷 PC-F2；用户于2026-10-20明确要求在新worktree生成backlog并执行修复。

## 范围

按动作、节点状态、最小职责、范围、继承来源拆开权限断言；分别验证活动作业完成和已完成结果修正。总表、明细和最终验收一致，不以管理员正例覆盖职责分离。

通用语义方法归 skills/提示/评审工件；Kernel仅做机械校验，不硬编码洛书业务规则。旧运行态只读，候选修订由正式Planner产出。

## 验收

- 复现并修订 → verify: 最小流程编辑者完成活动作业被拒、作业操作员不能越权修正；允许/拒绝和失败不变集可复核，正式 Critic 通过。
- 回归与证据 → verify: 实际角色消费当前方法；保留来源与旧新映射，不能把规划用例当作产品已执行TDD。

## 授权边界

本次授权覆盖这五项缺陷的规划修复；受控代际与预算记录见 [B17](2026-10-20-0709-182-b17-controlled-replan-verification.md)。不自动批准产品Impl、数据库操作、部署或push。

## 当前结果

活动作业完成、已完成结果修正和独立物料维护各自绑定最小主体与状态；
保留派生编辑能力方向，允许/拒绝均有独立前态和失败不变集。
最后无仓库负例与有仓库正例已分开建立并重置，禁止跨项目仓库回退。
正式 Critic `evt-4d9584e71768` 关闭最后夹具缺陷并通过，方法验证
20 passed、1 skipped。证据及审查边界见
[验收记录](../../docs/records/2026-10-20-0847-taskmap-repair-verification.md#最终正式验收)。
