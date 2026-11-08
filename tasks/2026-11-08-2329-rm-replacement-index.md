> 状态: done — 60decac3 fix: index recovery replacement checks per snapshot

# Run Manager 失败替代核验复用单次快照索引

授权：持续监工、分析修复阻塞并记录。基线512b957a实际只读调用两次pending、两次active投影共33.809秒；175405事件中152次替代核验约21秒，重复建by_id7.597秒、list.index及事件相等比较10.341秒。TaskStore归档186次查询仅1.15秒，暂不引入跨调用缓存。首次监工脚本漏传state_dir导致TypeError，未进入投影或写canonical；修正后取得此数据。

- [x] 两个Run Manager入口复用已有ReaderReplanIndex -> verify: 单次快照使用，保留完整原事件、顺序、同run/源失败/原子全替代/WRC准入判断，不缓存跨调用。
- [x] 真实caller回归 -> verify: 多任务、旧失败因果、同任务新失败、异run、未准入、部分替代、追加事件立即生效；不再逐失败调用list.index。
- [x] 完整生产快照前后比较 -> verify: 原新pending/active完整输出精确一致，记录无profiler时间、事件摘要。
- [x] 入口/直接调用/共享合同、哨兵及mock E2E -> verify: 定向闭包通过；不冒充全套或真实provider E2E。
- [x] 提交、dev合并、受控加载、记录 -> verify: 在途worker operation/attempt/pane保持，原用户修改不动，任务留tasks，不push。

范围不含业务Owner决定、重排计划或放宽失败替代证据。整体Watcher CPU问题仍单独观察。记录：LS-v1 docs/records/2026-11-08-rm-replacement-index-profile/。

验证结果：14项新回归先2RED/12GREEN，实施后105首轮、172影响闭包、53哨兵、10 mock E2E通过。自动planner反向关联90文件触发全量建议，按AGENTS本次两模块与直接调用闭包执行，自动contract层执行、deterministic/serial/mock层显式skip（mock另行执行）；未跑full pytest或real-provider。首次planner输出为文本但监工尝试当JSON解析，已保留原文并按文本读取，无产品影响。

175478完整事件及TaskStore/session冻结回放，pending/active完整输出精确一致；各两次无profiler调用pending13.085→6.157秒、active6.543→0.799秒。临时快照已清理。自身worktree backlogs为空，无陈旧候选；主线其他候选保持。实现60decac3→dev合并fec34073；合后97定向+53哨兵通过，23:33–23:34受控加载Watcher3700801/Web3701247，health/UI200，FMS24 Impl和FMS12 Verify两项operation/attempt/dispatch/workdir/pane/PID完全保持，未push。
