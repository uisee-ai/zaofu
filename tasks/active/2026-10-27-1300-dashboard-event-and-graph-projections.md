# Dashboard 事件与 Graph 投影性能优化

> 状态: active

用户已批准上一轮建议 1–4，并要求实施后重启 8002，用 loshu-fms 与现有 8001 对比。
基于 `10e6812b` 创建独立 worktree；保留既有 EventLog、TaskStore、SQLite 只读投影边界。
设计参考 `docs/design/192-dashboard-cold-start-and-catchup.md`；新设计文件保持 Git 忽略。

## 范围与验收

1. Board 复用同一来源代际的事件处理结果，减少重复读取和逐任务筛选。
   -> verify: 空缓存和追加情况下少重复解码/扫描，任务卡完整输出与旧算法逐字段一致；
   payload 子串、大小写、嵌套 key、全局事件、closeout、坏行、签名和顺序不变；
   rotation/replacement/config 变化严格失效，有限内存与失败回退。
2. Board 卡片及附属资源独立准备，保留现有完整 Board API。
   -> verify: 卡片真实派生状态独立就绪，不把未知 metrics/权限置零；
   其他资源各自 ready/error 和项目隔离，完整页面最终与旧版一致；
   授权操作走既有受控路径，SSE、刷新、失败、archive 及历史不回退；
   来源损坏/不可读不得缓存为有效空结果，同 inode/size/mtime 的成本改写不得漏刷新。
3. Graph 首屏结构和 Plan/节点详情按需读取，保留完整 API。
   -> verify: 首屏确实避免构建未请求的完整 Plan，而非完整构建后删字段；
   同 project/request/run/revision/as_of/source generation 绑定全部详情；完整搜索仍可用，
   未核实的摘要不能用于审批，Plan 和动作仍经过原始 digest、路径、权限及受控动作校验；
   旧完整 Graph 及历史、轮转、外部文件更改均保持准确。
4. Graph 建立 event/operation 索引减少重复扫描。
   -> verify: 保留事件和因果链顺序、重复来源归属、最后十条 activity、不同 run 和历史游标隔离；
   原依赖发现重试、来源前后校验及完整 Graph 精度不变，逐字段对账。
5. 性能与真实 dashboard 对比。
   -> verify: 同一份隔离 loshu-fms 的冷/热、时间与源哈希、事件数量固定；
   区分 Board 任务首显、完整派生 ready、Graph 首屏、完整详情 ready、后台队列和新鲜度；
   记录 API、浏览器、后台阶段时间及 CPU/内存，核对关键指标和完整响应；
   实施完成后仅重启用户授权的 8002，在同一 loshu-fms 上与保留现状 8001 通过 Docker Playwright 对比，
   记录两端实际 commit、项目、指标差异、耗时、失败与限制。
6. 聚焦影响范围验证及交接。
   -> verify: 运行 dev-verify 规划和合适测试层、生产构建、Docker Playwright；
   完成后提交实现，记录 hash，归档任务并提交；清理临时副本/进程，保留用户要求的 8002。

## 设计边界

不变更 canonical 业务状态，不通过精简内容、绕过脱敏/digest 或将陈旧数据标记 ready 制造提速。
独立队列/worker 仅在诊断证明队列争用时评估，不为猜测性多 worker 增加竞争。
同一 generation 的增量构建、恢复与完整视图继续保持正确；失败不得发布混合状态。
旧 8001 与新 8002 共享 SQLite 时，投影 key、成本元数据和写锁必须兼容，不能互相覆盖或导致循环重建。

## 结果

已实施 Board 共享事件及独立卡片代际、Workflow Graph 分层读取与事件索引；保留旧完整接口、
签名校验、脱敏及旧新版 SQLite 共存。损坏或跨代来源不发布假空结果。

同一隔离 Loshu 快照（6886 文件、12596 事件）对账七类 API、Board 卡片和 Graph 结构无未解释
业务差异。单次冷页 Board 完整 9.81→8.87 秒，独立派生卡片 7.82 秒；Graph 完整画布基线
7.31 秒，新版结构首屏 3.06 秒，点击后完整图 7.00 秒。Board 和 Delivery 热 API 的绝对延迟
分别增加约 26 毫秒和 9 毫秒；有限样本不能解释为普遍提速。两轮模拟持续写入到最新 ready
为 1456→818 毫秒、710→929 毫秒，均到目标水位，第二轮有回退。完整原始结果保存在
`/tmp/zf-dashboard-projection-v2-eval-20261027T130534/`。

实施提交后，将重启用户授权的 8002，记录真实 8001/8002 结果并在归档时补齐。

最终代码验证：dev-verify 规划选中 343 个影响范围测试模块；逐模块运行确定性层
`-m 'not serial and not perf and not host and not real_provider' -n 8 --dist loadfile` 得
4294 passed、2 skipped；serial 层 10 passed，pre-merge sentinels 51 passed；Web typecheck、
unit、生产构建通过；Docker Playwright Board/Graph 组件回归 32 passed。此非全量、host 或真实 provider 测试。
