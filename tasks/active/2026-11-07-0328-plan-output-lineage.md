> 状态: active

# 直接 Planner 汇总的新输出身份与断点恢复

LS-v1 已批准候选 evt-0aa140c4b5f4 通过任务正文迁移复核，首次实施却产生 task.pipeline.stage.dispatch_deferred evt-1eefc38595c1。直接 fanout 聚合没有经过 synth 的新输出身份解绑，沿用旧输入 Package 377af273 / generation 61e6d163；34 项新 Task Map 被错误绑定旧矩阵。内核同时按全量 replan 重开两个已完成任务，计划只在说明中保留完成记录，没有机器可执行的范围声明。03:26 受控停止，保留所有原始产物，禁止重做规划。

1. 直接汇总与 synth 同等解绑新输出身份 -> verify: 实际 reader aggregate 入口与 task_map.ready 准入使用新包；同代际不同内容不能静默复用旧包。
2. 受控原地恢复 -> verify: 仅对已证实输入/输出混绑的 task_map.ready 清除旧绑定，仍走审批、全量矩阵/验收准入，复用已生成的完整候选。
3. 已完成任务保留 -> verify: 操作员根据原 task.done、归档合同和原候选 commit 核对，两项验收不变；通过明确的执行范围保留全图，仅派发剩余 32 项，拒绝伪造完成或跳过未完成任务。补充 Planner skill 的机器范围要求。
4. 回归、合入、恢复验证 -> verify: 当前 dev 复现、影响闭包、sentinel/mock；记录真正 worker 派发、选读范围与承接基线。


## 实现与验证

- 直接 reader aggregate 与 Synth 共享输出身份隔离；相同 generation 必须匹配冻结正文。
- 实际受控 stage-retrigger 绑定 owner 审核、旧包观察证据；完成保留验证 kernel done、集成摘要、归档 authority、无后续尝试及精确合同等价，保留完整图并生成剩余派发范围。
- 新增中断恢复测试发现 TaskStore.get 同日归档读取首条旧记录；改为最新记录，不删历史。
- 首轮调用链 351 passed；303 文件影响闭包 4,288 passed / 10 skipped。测试规划器按数量扩至全量的步骤已主动终止，遵循 AGENTS 改跑精确闭包；不宣称自动回执全绿。
- 新增恢复/Store/Skill 定向 40 passed / 1 skipped；Store 扩展闭包 578 文件、7,108 passed / 2 skipped（206.54s）。串行 10、sentinel 53、mock flow 10 已通过，最终版本合并哨兵复跑 53 passed。
- 真实 LS 产物副本预演通过 blocking 包准入，生成新 generation 6a4790a4、package a48ab08b；原数据未改，临时副本已清理。业务 34 项和所有验收矩阵保留。
- 部署真实 Worker 验证仍待完成，保持 active。
