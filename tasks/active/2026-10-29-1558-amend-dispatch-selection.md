> 状态: active

# Writer 准入消费 canonical amendment 的增量范围

现场新 Plan 已通过独立 Critic 和跨矩阵检查，但 task_map.ready evt-643079cc89f4 在 writer 准入报 conflicting base_commit。完整36任务历史基线应保留，只有两个新 gap 需派发；合法 amend.resume_scope=gap_tasks_only / gap_task_ids 未被 loader 消费，导致把全部历史任务当成一个批次。

- 复现真实 git 双历史基线 -> verify: RED 精确为 conflicting base_commit。
- 没有事件任务选择时消费 canonical amend 的显式 gap 范围 -> verify: 只返回 gap，对已有历史基线不做改写。
- 保留显式事件选择、完整map的基线一致性校验 -> verify: 旧选集不被扩大；空/缺失gap列表失败关闭；无amend的混合基线仍拒绝。
- 关联问题：Planner 把旧P16B替代关系清空，而TaskStore旧任务尚in_progress；属于计划语义遗漏，须通过新Planner修订明确supersedes后再审批，不通过runtime猜测任务名字补状态。

验证：初始204 passed；追加loader/schema/Task Pipeline/候选任务/8槽位与stale-contract mock E2E见监工日志。sentinel 50 passed, 1 skipped；Flow smoke 10 passed。dev-verify 对大caller保守选191文件扩成全套，本补丁按仓库规则采用显式调用链闭包，未跑全量。真实 evt-643079cc89f4 只读load已选择两个P16B后继，dispatch_base_commit=4c4abcb73a88e105cdae6083ee590aec1367511e。部署后等待包含正确替代关系的新Plan实际准入。
