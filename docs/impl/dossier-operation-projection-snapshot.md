# Dossier operation 投影的单次快照

终态 owner 简报的调用链是 `tick_services` →
`materialize_terminal_goal_deliveries` → `build_goal_dossier` → `_operations`。
此修复仅改变最后一步的纯读投影成本，不修改交付准入、终态事件或状态机。

LS-v1 在 dev `b5c68f2c` 的 234632 事件终态前缀上，限时 180 秒的
cProfile 采样未完成简报：读账本 4.456 秒，builder 175.220 秒后仍在第 14 个
任务。已经重复解析角色会话 YAML 78 次（累计 71.462 秒），完整 workflow
reducer 调用 86 次（12.533 秒），每个 dispatch 再遍历完整事件。
采样开销包含在内，嵌套累计值不能相加，截断时活跃父调用的累计值不采用。

`operation_projection.project_task_operations_batch` 现在在一次调用中构造
dispatch 与 workflow operation 的有序事件索引，执行一次完整 workflow reducer，
读取一次角色会话。每次迭代只产出一个任务的投影，Dossier 随即按原身份去重；
不先保留全部任务投影。共享的是事件引用，不复制事件正文。单任务、单 dispatch
及单 workflow operation 的独立入口继续支持原来的 `events` 参数与读路径。

快照只存活于本次构建，不是跨 tick 缓存。下次构建重新读取角色会话并归约新事件。
TaskStore 的当前展示字段与 resume packet 查询仍走原有读取，不被替换为历史事实。
TaskStore.get 在终态索引缺失或损坏时可能执行 read-repair；生产之外的只读采样
使用写入 audit 护栏，遇到该行为必须记录失败，不能偷偷写索引。

## 保持的语义

- run scope、terminal 事件前缀、contract/claim/source digest 校验、redaction 与
  blocked/unknown 状态保持；不裁剪历史、不省略验收证据。
- 每任务的 dispatch 发现仍使用 `event.task_id`，包括空字符串的既有行为；
  dispatch 时间线仍使用 `payload.dispatch_id or payload.active_dispatch_id`。
  taskless 事件和共享 dispatch 的其他任务事件仍进入原时间线。
- workflow reducer 输入仍是完整 scoped events，保留 result admission 等关联事实；
  operation 时间线仍按原 `payload.operation_id` 精确匹配，不混入其他 operation。
- 角色会话按原顺序选第一个 matching dispatch；文件缺失、损坏或非字典仍返回空。
- 任务、dispatch、workflow operation 的顺序及 Dossier 最终身份去重保持。
  输出的可变 operation 字段独立复制，调用者修改某个投影不会污染另一个或输入事件。

## 验证边界

`tests/test_dossier_operation_snapshot.py` 对比完整独立投影及完整 Dossier，覆盖
共享 dispatch、taskless/active_dispatch_id、准入结果、空任务身份、缺失/损坏 YAML、
下一轮事件/角色变化、逐任务产出与输出隔离。调用次数只是性能回归指标，不能代替
完整内容对比。直接 Web/CLI、progress、owner delivery 与 workflow 合同另有闭包回归。

真实只读回放使用 `evt-0a9994064383` 的固定事件前缀；比对已有生产简报的 terminal、
source fingerprint、66 个任务顺序、92 个合同、26 条 claim、全部事件 ID、artifact
refs/digests、586 个 operation 的 ID 与完整时间线、delivery readiness。
运行设置 nice 19、180 秒墙钟、8 GiB 地址空间；禁止输出目录之外写入、子进程及网络。
实时 store/角色会话是当前 overlay，不宣称它们是冻结历史全状态。

第一轮完整优化构建为 141.892 秒；最终迭代版本为 137.859 秒（均无 cProfile），
最终峰值 RSS 为 4188692 KiB，11 项生产简报对账一致、0 次写入尝试。
原始 175.220 秒是带 profiler 的
未完成截断，二者不能计算严格加速比。仍存在长同步周期，本修复不宣称 Watcher 延迟
已根治。剩余 history、字段扫描、序列化及 Store 读取的精确耗时，需要下一次单独的
低开销分阶段测量；不据静态调用数量判断新的主瓶颈。

原始证据和实际命令：`/tmp/zf-dossier-profile-20261113/`；实现及最终验证结果由
本项 backlog 的归档记录绑定。
