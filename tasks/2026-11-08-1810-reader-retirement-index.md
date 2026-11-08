# Reader 恢复退休判定索引复用

> 状态: done — `8b86756c` fix: reuse reader retirement proofs within recovery snapshots

用户授权持续监工、修复阻塞并记录。LS-v1 当前约164564条事件，恢复checkpoint构建的只读cProfile为18.35秒，其中Reader替代证据7.64秒、活跃候选修复谱系3.92秒，反复构建相同事件索引。此耗时包含分析器开销，不等同单次实际Watcher周期。

- 保持旧失败的因果替代、全部受影响任务、新代次正式准入、同Run/计划代次及有序活跃修复判定；仅在单次不可变snapshot复用既有ReaderReplanIndex。
- 不跨tick缓存、不删历史、不放宽任何cap/审批/验收、不把一般新计划当旧失败已解决。
- step: 加入负向边界与索引/原路径等价回归 -> verify: 真正调用plan_reader_stage_replan和workflow_resume入口，覆盖替代准入、跨Run、未准入、前后事件顺序、活跃计划及快照变更。
- step: 冻结真实164564事件前缀回放 -> verify: checkpoint完整内容不变；无分析器同机前后耗时记录。
- step: 定向及影响闭包、哨兵、199 mock -> verify: 记录确切tier，无全套pytest或真实Provider E2E声明。
- step: commit/合入dev/受控重载 -> verify: 5个在途operation/attempt/pane保持、Web200、真实任务继续、记录修复与余留性能问题。

验证：6项新增回归（最初5项缺少索引参数失败，后增加启动歧义保护），既有跨Run/代次/准入边界带共享索引再跑。初轮定向65通过；最终253文件影响闭包3594通过7跳过（含199 mock），串行10，哨兵53。dev-verify因测试文件阈值建议全套，遵循仓库规则改为显式影响闭包；未执行全套pytest/真实Provider E2E。冻结164564事件与Task快照7条checkpoint完全一致，无分析器7.97→3.27秒。

实现说明：[Reader恢复投影事件索引](../docs/impl/reader-replan-projection-index.md)。现场证据在LS-v1 docs/records/2026-11-08-resume-latency-analysis/。实现合入dev `3ffbfdeb`；Watcher3093527/Web3094028加载该版本，5个在途operation/attempt/dispatch/pane完全保持，Web健康和UI200。合并后67项定向/199 mock通过，哨兵53通过。仅归档文档尚待本提交合入；未push。
