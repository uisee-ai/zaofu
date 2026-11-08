# Supervisor 快照内重复历史扫描收敛

> 状态: active

来源：本轮用户授权继续监工 LS-v1、发现问题后修复并记录。当前 dev 35fcbc5b；真实 LS-v1 read-oriented profile 见 docs/records/2026-11-08-control-plane-cost/（业务项目目录）。控制面单进程 CPU 约93.6%，138014 条历史事件的带 profiler 快照 69.46 秒；新派发故障 reducer 仅0.037秒。正常 RM tick 在本轮诊断入口部署前后都约27秒；不将 profiling 耗时等同正常运行耗时，不断言当前 worker 已被阻塞。

## 范围

1. 自动化日报/周报/监视摘要统计任务阶段时，先按 task_id 建立本次调用的事件分组，再沿用现有 derive_phase 和代次隔离规则，避免每个任务对不相关历史做范围筛选。
2. 失败信号采集在同一次调用内复用 fanout identity 投影，不为每条 fanout 事件重新重放全部历史；保持 unknown/current、supersession、终态及语义成功的现有判定。
3. 仅调用内复用，不增加持久缓存、跨tick复用或canonical状态，不截断完整历史，不改变诊断路由/任务权限/执行图。
4. 在真实 LS-v1 的冻结输入上比较等价结果和调用代价；合并 dev 后受控部署，保留正在运行的 task/attempt/pane，并记录实际观察。

## 验收

- 历史混有跨任务、全局、旧代次、当前代次和Verify事件 -> verify: 看板列结果与原完整扫描一致，并证明处理与任务有关的阶段事件。
- 同一 fanout 多事件、unknown legacy、同逻辑后继取代旧实例 -> verify: 与旧逐次 fanout_current_status 完整结果一致；identity projection 每次采集至多构造一次。
- 两次调用间新事件/新代次/任务状态变更 -> verify: 不复用旧调用结果，无全局缓存导致陈旧。
- entrypoint -> verify: project_automations 及 collect_failure_signals/Supervisor 实际调用链覆盖，不只验证未使用的库。
- 聚焦影响闭包、sentinel和相关mock -> verify: 记录命令、退出码；不以profile采样替代行为回归。
- 真实冻结输入基线/修复重放 -> verify: 业务输出全等（去除生成时间），记录耗时与边界；运行时恢复验收不伪造worker终态。

实现说明：[Supervisor 投影开销](../../docs/impl/supervisor-projection-cost.md)。

## 完成记录

已完成代码与验证，待实现提交和部署归档：

- RED：2 条新回归在旧HEAD失败；fanout 原35次构建而要求1次。首版测试错误使用普通 child.failed 期待 timeout 信号，改用已有 fanout.timed_out 合同后确认纯性能断言失败。
- 聚焦：5 文件43 passed。
- dev-verify plan 选中221文件并因数量>80建议完整suite；遵循仓库影响闭包策略，仅执行这221文件确定性层：3102 passed / 10 skipped，111.34秒。没有执行完整pytest或真实provider E2E。
- dev-verify run --base dev --skip-tier deterministic：串行10、sentinel53、mock flow10通过。
- 真实138164事件冻结输入：看板3组及所有fanout failure signal完整输出全等；单次无profiler样本两热点总计17.204秒→0.331秒（并发工作负载中采样，不是全watcher SLA）。精确代码SHA和输入摘要见业务项目 control-plane-cost/fixed-comparison.json。
- 监工回放脚本首次误用Task.model_dump（Task实际为dataclass），脚本失败后临时目录已清理；改用asdict后完成等价回放。此为operator探针错误，不算runtime故障。
