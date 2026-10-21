# 恢复规划返修并完成新旧Plan对照

> 状态: active

用户批准执行下一步：查清返修中断、修复4类计划问题、独立Critic复核并更新与既有22任务
Plan的直接对照。保留Plan人工审批，不执行Impl，不修改原dev/8001/原cargo项目。

- [x] 复现中断。step -> verify: 历史快照复现running operation被附属失败收尾终止。
- [x] 修复通用运行时边界。step -> verify: RED为2个行为失败；GREEN影响闭包379 passed、哨兵51 passed、确定性Flow smoke 10 passed；历史快照的误收尾动作由1变0。
- [ ] 继续27任务草稿返修。step -> verify: 四类fix_items在正式产物中有逐项修订证据，未决CR/Q/D不擅自关闭。
- [ ] 独立Critic和完整范围复核。step -> verify: 新提交得到实际评审，记录未覆盖项，不能只凭关闭四个fix_items宣称全量通过。
- [ ] 新旧直接对照与待批交付。step -> verify: 逐项记录改善、保留问题、新增问题和UNKNOWN；正式待批Plan与原文绑定，未运行的产品测试为NOT_RUN。

运行时边界见[实现说明](../../docs/impl/failure-closeout-active-operation.md)。证据目录：
`/home/min/workspace/public/zaofu-evidence/20261021-plan-regen/repair/`。

dev-verify的全分支及增量计划均已保存。增量映射因run_manager共享调用面选出249个文件，
会扩成全量确定性套件；本轮只改运行时单一决策边界，采用上述显式影响闭包替代该广泛层，
自动执行contract/mock_e2e层并跳过deterministic/serial层。不宣称全套测试或产品验证通过。
旧返修operation已cancelled且recover无检查点，后续采用新请求承接冻结草稿，保留原失败。
