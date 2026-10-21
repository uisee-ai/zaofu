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

运行时修复提交：`5b6ebb2c fix: defer failure closeout while workflow operations are running`。
首次承接运行因宿主磁盘写满中断，保留2条不完整原始事件；通过CLI停止，未手改账本。
清理可重建缓存并加低空间保护后重试，37分钟生成待批Plan `evt-31991deb2ef0`（27任务/482 AC）。
独立Critic通过，但operator使用实际选择器复现`VC-postverify-receipts`仍被candidate Verify
要求，且它依赖本次Verify未来报告。本项不能算PC-04已关闭，Plan未批准、Impl未启动。

追加通用修复：Plan执行事实报告逐条展示实际AC/command选择结果，执行一致性技能要求
对照真实消费者，不以自定义阶段注释代替接线。CLI行为回归RED 1 failed/5 passed；增量
dev-verify GREEN 75 passed/1 skipped，哨兵51 passed，确定性Flow smoke 10 passed。
系统skill-creator quick_validate不支持仓库既有的stages/dependencies等frontmatter扩展；
未改动这些元数据，仓库原生技能解析、依赖物化、reference分发一致性测试通过。
完整分支plan与本轮增量run收据已保存；不重复触发已闭合的运行时广泛测试层。

追加发现、正式Plan冻结、验证与成本记录：
`/home/min/workspace/public/zaofu-evidence/20261021-plan-regen/repair-retry/`。
仍需据真实选择事实返修、独立复核、最终对照；中间Critic通过不能代替这些验收。
