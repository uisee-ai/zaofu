# Task map 执行一致性修复与受控重规划

> 状态: active。承接[正式 Critic 发现](2026-10-20-0528-plan182-semantic-followup.md#正式结论与待授权续跑)，本轮执行 B12–B17。

## 用户授权

用户于 2026-10-20 在当前操作会话明确要求：

> 1.删除9f60 worktree 吧 2.创建新的worktree,切换到新的worktree 上面发现的taskmap 高缺陷生成backlog ,执行backlog 修复

该授权覆盖已列出的 PC-F1–PC-F4 高缺陷及相关 PC-F5 跨工件一致性修复，
以及原审批提案的一次受控新规划 generation。原 escalation 为
`evt-f7f337168b9a`，正式 Critic 为 `evt-45bb3c9bcea3`。
使用 `ControlledActionService.execute(stage-replan-new-generation)` 和真实授权引用，
保留既有 blocked 历史、重试上限及最终产品 Plan 人工审批门。
本轮不包含产品 Impl、数据库操作、部署、自动 Plan 批准或 push。

## 执行范围

| 任务 | 缺陷 | 验收重点 |
| --- | --- | --- |
| B12 | PC-F1 动作生产者错误 | 全量权限动作按实际命令分派，混合行拆子例，同步矩阵 |
| B13 | PC-F2 权限角色及状态混用 | 活动作业完成与已完成结果修正分离，最小主体允许/拒绝一致 |
| B14 | PC-F3 本片与最终验证错位 | 调拨 TDD 不执行未来 G3；最终 G2 覆盖全部承诺集合 |
| B15 | PC-F4 修正事务向量不完整 | 时间、设备、需求、物料、节点及失败回滚完整，独立后续动作不变 |
| B16 | PC-F5 决定快照过期 | 全部 Q 的责任、范围、最迟阶段和证据按当前登记一致 |
| B17 | 受控返工与交接 | 正式 Planner/Critic、同版可读工件、独立审计及只读 Preview |

激活文件为 `tasks/active/2026-10-20-0709-182-b12-*` 至 `b17-*`。
方法由 `skills/zf-plan-execution-readiness/references/execution-consistency.md`
提供，三份分发保持一致。Kernel 不新增洛书业务语义判断或第二份 task contract。

## 环境迁移

旧 worktree `9f60/zaofu` 的提交 `094bfca` 已在 dev `598d499` 中。
停止旧 Loshu 规划 runtime 和 8002 Dashboard，核对工作区无候选源码后，
归档并逐字节校验 171 个证据/本地状态文件，正常删除旧 worktree 和已合并分支。
归档 SHA-256：`7c354d39092a96e2ee8cf63a282abf5f816b6363a1ef2ff9cb329aed8db76f60`。

新 worktree 为 `/home/min/.codex/worktrees/taskmap-fixes-20261020/zaofu`，
分支为 `wip/codex-2026-10-20-taskmap-readiness`。
真实规划项目仍为 `/home/min/workspace/loshu-plan182`，状态目录由配置指定为
`.zf-plan182`。仅迁移配置中的两个 skill source 路径，保留原配置备份；
没有手工修改 canonical task/event/运行状态。

## 验证边界

方法资源接线的 RED 为 3 failed、2 passed；补充链接和资源后的聚焦验证
20 passed、1 skipped。`dev-verify.py run --base dev` 自动影响闭包
45 passed、1 skipped。以上证明角色可消费方法资源，不代表候选语义已经修复。

规划项目 cold-start 检查为 3/5：配置、角色文档和状态有效；
README 与 src/tests 尚不存在，符合此项目尚未批准产品实现的实际状态。
不能将规划用例计数、结构闭包或测试方法加载成功写成产品测试已通过。
正式 generation、Critic 结论和 Preview 验证将在实际执行后记录。
