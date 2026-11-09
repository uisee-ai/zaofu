> 状态: done — dcc0fcc7 fix: reuse run scope within recovery projection batches

# 恢复扫描按调用复用运行归属索引

用户授权持续监工并修复。dev da6d8fde 实际Watcher120秒采样2190有效/74错误，稳态1457中run_scope315。176514事件实际升级终态入口+reader恢复检查只读profile23.162秒，运行别名重建21次17.346秒；0升级终态输出，当前并非真正需要21份不同快照。记录LS-v1 docs/records/2026-11-09-recovery-scope-profile/。

- [x] 实际入口复现重复重建 -> verify: 多升级及多reader失败保持隔离，默认解析与预计算完整别名完全相同。
- [x] 调用内惰性复用 -> verify: 每个入口最多重建一次；无候选不建；下一调用读取新事件；保留单run旧数据回退、多run歧义、冲突锚点与晚到别名。
- [x] 完整冻结回放与闭包 -> verify: 原/新升级事件语义、reader checkpoint逐项精确，定向/自动计划/哨兵/mock通过；不声明full。
- [x] 提交合并加载记录 -> verify: reader身份/pane/PID保持，监工继续；不push。

只改索引使用方式，不新增恢复政策、跨调用缓存、截断事件或直接改canonical状态。总CPU问题仍开放，不能用局部提速代替整体证明。

8项新回归先7RED/1GREEN（含2个实际入口重复计算回归）；实施后31首轮、401影响闭包、53哨兵、10mock通过。planner反向198文件因数量建议全量，但本次只有两个恢复入口与向后兼容解析参数，按AGENTS选明确调用/运行隔离/事件终态闭包；run中显式skip deterministic/serial并记录，未运行full pytest。完整5段176568事件及TaskStore/session冻结回放，两入口语义输出精确相同，别名重建21→2；无profiler 8.730459→2.952513秒，仅局部提速。

实现dcc0fcc7→dev1291ad93；合后31定向+53哨兵通过。00:19 UTC受控加载Watcher3792796/Web3793029，Web health/UI200，FMS24 reader operation/attempt/pane%1753/PID3768891保持，未push。整体CPU后续采样，不提前关闭。
