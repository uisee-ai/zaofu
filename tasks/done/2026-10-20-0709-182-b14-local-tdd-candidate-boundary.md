# 182-B14 · 本片 TDD 与最终候选覆盖边界

> 状态: done；实现 `c199520 feat: strengthen plan execution consistency review` 与 `37a7b20 fix: audit evidence paths across every verification tier`。
> 来源: 最新54任务候选的正式Critic缺陷 PC-F3；用户于2026-10-20明确要求在新worktree生成backlog并执行修复。

## 范围

本片步骤仅运行当前或已完成前置的命令；保留未来 mandatory candidate 验收。G2 最终读取及权限用例独立于局部 smoke，覆盖承诺的完整旅程、标签和动作。

通用语义方法归 skills/提示/评审工件；Kernel仅做机械校验，不硬编码洛书业务规则。旧运行态只读，候选修订由正式Planner产出。

## 验收

- 复现并修订 → verify: 旧调拨第3步未来G3命令先被识别；新本片步骤无未来命令依赖，候选IC01–IC07及E18–E23不再继承局部排除，正式 Critic 通过。
- 回归与证据 → verify: 实际角色消费当前方法；保留来源与旧新映射，不能把规划用例当作产品已执行TDD。

## 授权边界

本次授权覆盖这五项缺陷的规划修复；受控代际与预算记录见 [B17](2026-10-20-0709-182-b17-controlled-replan-verification.md)。不自动批准产品Impl、数据库操作、部署或push。

## 当前结果

本片命令和命令内部动作无未来依赖，IC01–IC07、C01 六标签和 E18–E23
等最终候选范围独立于局部 smoke；全部 2,015 个命令收据绑定核对无错配。
正式 Critic `evt-4d9584e71768` 通过；方法及角色消费验证 20 passed、1 skipped。
本片 RED/GREEN 是规划责任，尚未执行洛书产品命令。证据及审查复用边界见
[验收记录](../../docs/records/2026-10-20-0847-taskmap-repair-verification.md#最终正式验收)。
