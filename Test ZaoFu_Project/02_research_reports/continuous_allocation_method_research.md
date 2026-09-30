# K552-F2 连续 Python 分配追踪方法调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/allocation_segments.py`。K601/K603 分段准入方法已存在；K552-F2 的32 Task/1200秒连续长样本仍开放，不用短校准替代。

> 调研日期：2026-09-25  
> 范围：只核对 Memray 官方文档、Memray v1.20.0 官方源码和本地 CPython；不修改 `src/`、测试、`pyproject.toml`/`uv.lock`，不安装共享依赖，也没有运行 32 Task/1200 秒工作循环。  
> 结论性质：方法选择与最小校准方案，不是性能实测，也不关闭 K552-F2/PERF-05。

## 1. 结论先行

Memray 可以提供比当前 `tracemalloc` 更丰富的“分配—释放生命周期”离线证据，但没有证据表明它在 ZaoFu 的 32 Task/1200 秒场景中开销更低。若要验证，首选在**一个连续 Tracker 周期**内写入 `FileFormat.ALL_ALLOCATIONS`，运行结束后用 `FileReader` 读取 allocation records、memory snapshots 和 temporal allocation records；不要以分段启动/停止 Tracker 或只读取最终 leak 汇总来代替连续追踪。

`FileFormat.AGGREGATED_ALLOCATIONS` 虽然文件较小，但官方明确它丢失每次释放的时间信息，因此不能证明多个工作循环检查点之间的保留/释放，也不能发现 temporary allocations。`SocketReader.get_current_snapshot()` 可以做在线当前堆快照，但官方 API 没有把快照和业务 checkpoint 建立持久化、序号水位或 flush/barrier 的接口；它不应直接作为本项目可审计的主证据。

结论是“值得做一个短、隔离、同源校准”，而不是现在替换测试、改生产缓存或重新跑 32 Task。若校准不能在既有业务断言、1200 秒预算、RSS/FD 和证据完整性约束内完成，则拒绝将 Memray 纳入 K552-F2 的正式长样本。

## 2. 当前环境与版本事实

- 本地 `uv run python` 为 **Python 3.11.15**；`importlib.util.find_spec("memray")` 返回空，命令行没有 `memray`。因此本调研没有把未安装包当成本地实测结果。
- Memray 官方 GitHub `releases/latest` 当前为 **v1.20.0**（发布日期 2026-08-07）：<https://github.com/bloomberg/memray/releases/latest>。
- 如需校准，应使用隔离命令（示例）`uv run --with memray==1.20.0 python ...`，不改仓库依赖锁文件；实际是否能安装和运行必须另行记录。

## 3. 官方 API/源码核对

### 3.1 Tracker 的连续性与开关

官方 API 说明 Tracker 是上下文管理器：进入 `with` 后开始追踪，退出时停止并 flush/close 输出；整个进程同一时刻只能有一个活动 Tracker，重新启用需要新的 Tracker 实例。参数默认 `native_traces=False`、`trace_python_allocators=False`；后者为 `True` 才把 Python allocator 作为独立分配来源记录。来源：<https://bloomberg.github.io/memray/api.html>。

v1.20.0 源码在 `src/memray/_memray.pyx` 将这些参数传给 `NativeTracker.createTracker(...)`，并对活动 Tracker 做单实例检查（源码：<https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyx>，约 787–885 行）。这支持“一次 Tracker 覆盖整个工作循环”的方法，但不支持在一次运行中无损地反复启停同一个 Tracker。

`follow_fork` 默认关闭；v1.20.0 源码拒绝在非文件 destination 下使用 `follow_fork`。若工作循环跨进程，不能假定子进程会自动出现在主进程证据中；应把进程边界作为单独的校准前置条件。来源同上约 821–824 行及 API 页。

### 3.2 ALL_ALLOCATIONS 与 AGGREGATED_ALLOCATIONS

官方 API 对两种文件格式的定义如下：

| 格式 | 能证明什么 | 不能证明什么 |
|---|---|---|
| `ALL_ALLOCATIONS`（默认） | 记录每一次分配；可支持 temporary allocations 与 stats reporter | 分配频率高时文件可能很大；仍须验证运行/磁盘预算 |
| `AGGREGATED_ALLOCATIONS` | 按位置汇总峰值时仍存活和 tracking 结束时泄漏的计数/字节；文件小 | 释放发生的时间已丢失，不能找 temporary allocations 或 allocation history；若进程在 tracking 结束前被杀，官方说明不会留下有用的聚合信息 |

来源：<https://bloomberg.github.io/memray/api.html#memray.FileFormat.ALL_ALLOCATIONS>、<https://bloomberg.github.io/memray/api.html#memray.FileFormat.AGGREGATED_ALLOCATIONS>。

因此，对 K552-F2 的“每 Task/阶段是否持续保留”问题，只有 `ALL_ALLOCATIONS` 满足信息需求；AGGREGATED 只能作为经明确降级标记的文件大小对照，不能作为闭环证据。

### 3.3 FileReader：最终、生命周期和检查点信息的边界

v1.20.0 的 `FileReader` 源码提供以下接口（来源：<https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyx>）：

- `get_allocation_records()` 逐条读 `ALL_ALLOCATIONS` 的 allocation record，但跳过 memory records/snapshots；返回的 `AllocationRecord` 没有直接的时间戳。因此它不能单独把某条分配绑定到某个业务 checkpoint（约 1414–1438 行）。
- `get_leaked_allocation_records()` 汇总 tracking 结束时仍未释放的记录；它是最终态视图，不是每个中间 checkpoint 的快照（约 1290–1301 行）。
- `get_temporal_allocation_records()` 需要 `ALL_ALLOCATIONS`，读取分配与 memory records，并生成 `TemporalAllocationRecord`；每条记录有 `Interval` 列表，字段包括 `allocated_before_snapshot`、`deallocated_before_snapshot`、`n_allocations`、`n_bytes`。AGGREGATED 输入会抛 `NotImplementedError`（约 1317–1363 行；类型定义见 <https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyi> 约 99–139 行）。
- `get_memory_snapshots()` 返回 `(ms_since_epoch, rss, heap)`，其中时间字段是 Unix epoch 的毫秒值，不是 `time.monotonic_ns()`；必须保留 sidecar 的 wall-clock/epoch 映射再做对齐。读取器初始化时根据文件中的时间估计快照数；超过 `max_memory_records`（默认 10000）会以 stride 抽样，降低可用的时间分辨率（约 1086–1155、1442–1444 行）。源码签名把该参数声明为 Cython `int`，因此不能传 `None` 来表达“无限制”；正式校准必须显式记录一个足够大的整数和实际快照数，不能假定每个业务 checkpoint 都有一帧精确对应。

可审计的解释是：测试自身同时记录 wall-clock 和单调时钟；以 wall-clock 转换到 epoch 毫秒。普通最近邻只能提供诊断近似，不能证明恰好在 checkpoint 已释放。若要验证阶段释放，应在短校准中记录业务动作完成后、下一释放/保留动作前的稳定窗口，并要求真实 memory snapshot 严格位于该窗口内部，再以 temporal interval 交叉检查；窗口外最近邻不能替代。没有窗口内帧就报告 `unresolved`，不能插值后宣称已证明释放。窗口期间 Tracker 不停，观测等待计入总预算；跨线程变化仍要另行限定，不能因为主测试线程暂停就称整个进程静止。

### 3.4 SocketReader 与在线快照

`SocketDestination` 让 Tracker 通过 socket 发送记录；官方文档说明创建 destination 时会建立服务器并等待 reader 连接。`SocketReader.get_current_snapshot(merge_threads=...)` 返回当前仍在用的 `AllocationRecord`（v1.20.0 源码约 1649–1731 行；类型定义约 216–235 行，来源均为 <https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyx> 与 <https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyi>）。

这适合短校准探测“在线读取是否可工作”，但有三个限制：

1. 读取在后台线程中进行，API 没有业务 checkpoint barrier、flush 或快照水位证明；一次返回不能证明恰好覆盖该 Task 的终态。
2. socket 记录不会自动形成可重放的文件；若测试另写快照 sidecar，必须自行原子写入、记录时间/PID/Tracker 元数据和失败状态。
3. 断开或进程异常终止时没有像完整 FileDestination 产物一样的统一结束/压缩验收。

所以在线 socket 只能是诊断辅助，不是 K552-F2 的主验证路线。

## 4. 开销、输出大小与解释风险

Memray 官方 Performance 页明确指出开销来自 Python profile hook 与每次 allocation/deallocation 的登记；开启 `--trace-python-allocators` 会显著增加开销，因为它覆盖大量短命小对象。官方页面的公开基准环境是 Memray **1.5.0、Python 3.10.9、特定 Arch Linux/NVMe/CPU**，并明确结果只提供近似感受、会因机器不同而变化：<https://bloomberg.github.io/memray/performance.html>。因此不能把网页上的任何数字换算为 ZaoFu 32 Task 的性能结论。

最小开关建议是先 `native_traces=False`（默认），只在需要原生栈归因时另做一组短对照；`trace_python_allocators=True` 是本问题更完整的覆盖，但应把其开销单独计入校准，而不能与当前默认 tracemalloc 结果直接横比。

ALL 文件大小取决于 allocation 频率、栈信息和运行时长，官方只给出“可能很大”的警告；校准应记录文件字节数、磁盘剩余、Tracker 运行时间、RSS、FD 以及退出时压缩时间。不能以报告 HTML 能打开作为文件完整性的唯一证明；必须验证 FileReader 可读、header/stats 完整、记录计数与结束状态一致。

已核对的 Tracker/FileDestination 公共参数没有 capture 字节上限选项；若需要上限，应由独立有界诊断进程的文件/RSS/时间监控在达到阈值时标记失败并退出，不能无声截断 capture 后按通过处理。阈值必须在开始前写明，不能根据结果调大。默认 FileDestination 使用共享可写 mmap，结束后 LZ4 压缩；文件映射/缓冲也可能影响 RSS，观测结果不能直接当生产对象字节数。来源：<https://bloomberg.github.io/memray/api.html>、<https://bloomberg.github.io/memray/run.html>。

Memray flame graph 的官方说明还明确指出横轴不表示时间，只是把同一峰值时刻的栈叠加展示：<https://bloomberg.github.io/memray/flamegraph.html>。因此 flame graph、live TUI 或最终 leak 报告都不能单独证明“每个 Task 之后都释放”。应直接解析 FileReader 的 raw/temporal 记录。

## 5. 当前 Python 版本的对象生命周期限制

Memray Tracker 的 `track_object_lifetimes=True` 在 v1.20.0 源码中要求 Python **3.13.3 或更高**；当前工作区是 Python 3.11.15，不能把该功能作为 K552-F2 的证据。来源：<https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyx> 约 795–800 行。

这也意味着现有测试的 weakref/GC、runtime/log/index 引用释放和磁盘内容不变断言仍需保留；Memray temporal allocation 不能替代它们。

CPython 官方 `tracemalloc` 文档说明 `stop()` 会清除全部已收集 traces、`take_snapshot()` 不包括启动前分配，故启停追踪不是无损减负方案。`reset_peak()` 不清除 traces，但只重置峰值，不减免 allocation hook 成本。当前已有 `tracemalloc.start(1)` 使用最低有效栈帧数；不能通过降低到零帧减负。官方没有在这些 API 中提供同等完整性下的采样分配模式。来源：<https://docs.python.org/3.11/library/tracemalloc.html>。

Memray 的分配位置不是对象所有权，更不是业务持久化正确性。需要继续独立验证已知 runtime/log/index 的 weakref 释放、测试副本与缓存分量，以及释放实验前后受保护 state 文件的 SHA 不变；只有这些组合证据才能说明“指定对象释放且未改 durable truth”，不能从 allocation 总量下降推出同一结论。

## 6. 最小可验证校准方案（不跑 32 Task）

### 6.1 固定实验边界

1. 使用一个临时目录和隔离 `uv run --with memray==1.20.0` 环境；不修改仓库依赖、不改生产逻辑、不改现有 1200 秒业务预算。
2. 复用一个已存在的短 mock work-loop 样本（例如 2–8 Task），保证业务流程、state/ledger 断言和清理路径与现有测试相同；该校准不计作 32 Task/PERF-05 通过。
3. 单一 Tracker 覆盖整个样本：`FileDestination`、`file_format=ALL_ALLOCATIONS`、`native_traces=False`；分别校准 `trace_python_allocators=False` 与 `True`，不要同时改变其它变量。
4. 在每个既有业务检查点写入测试 sidecar：单调 `time.monotonic_ns()`、wall-clock、Task/phase 标识、PID/线程信息；sidecar 原子写入，失败或半截文件必须 fail-closed。

### 6.2 结束后解析与证据

1. 关闭 Tracker 后验证 capture 文件可由 `FileReader` 打开，读取 metadata、`get_memory_snapshots()`、`get_temporal_allocation_records()`；记录 header 的格式、native/python allocator 开关、记录数、文件字节数、实际快照数及是否触发 `max_memory_records` stride。
2. 用 snapshot 的 `ms_since_epoch` 与 sidecar 中定义的稳定阶段窗口对齐；只选择严格位于窗口内的样本。没有样本、wall-clock 回拨或窗口内存在未限定的生命周期变更时记录 `unresolved`，不把最近邻/插值算作释放证据。
3. 对每一阶段汇总 temporal intervals：分配发生在 checkpoint 前、释放发生在 checkpoint 前、仍存活到最终退出。把 `get_leaked_allocation_records()` 仅作为最终汇总交叉检查，不能替代中间阶段。
4. 保留现有 RSS/FD/socket/锁/state-tree/业务终态断言，并增加 capture/sidecar SHA、大小、结束标志和解析异常记录；任何来源漂移、解析不完整或清理失败均为校准失败。

### 6.3 接受或拒绝条件

只有同时满足以下条件，才可提出把 Memray 作为长样本的可选追踪器：

- 同一短样本的业务结果、Task/attempt/operation/terminal 证据与无 Memray 对照一致；
- Tracker 从头到尾只启停一次，未切段，且每个检查点均有可解释的 snapshot 对齐或明确报告 unresolved；
- `ALL_ALLOCATIONS` 文件、sidecar 和解析结果可复读，大小/磁盘/退出压缩时间有界；
- 额外耗时、RSS、FD 和临时目录增长不使既有 1200 秒、资源与清理断言失真；
- `trace_python_allocators=True` 的成本经过同样短样本实测；不能以官方旧基准或宣传语代替；
- 若出现 capture 损坏、时间对齐无界、输出增长无界、子进程未覆盖、业务断言改变或任何需要改生产逻辑的情况，立即拒绝该方案并保留 tracemalloc/现有传感器路线。

## 7. 对 K552-F2 的明确建议

当前不修改 `tests/conformance/test_zaofu_testkit_s4_workloop_host.py`、`tests/testkit/workloop_resources.py` 或任何 `src/`。先执行上述隔离短校准；只有校准产生足够强的 temporal/文件证据并满足拒绝条件检查，才在新的 need-to-do 中提出一个可审查的长样本接线步骤。无论校准结果如何，都不能关闭“32 Task 持续分配/1200 秒”或完整 PERF-05，不能用 Memray 的最终 leak 汇总、live TUI 或 flame graph 代替既有业务和生命周期断言。

## 8. 一手来源清单

1. Memray API（Tracker、FileFormat、FileDestination、SocketDestination）：<https://bloomberg.github.io/memray/api.html>
2. Memray Performance（开销来源与基准环境）：<https://bloomberg.github.io/memray/performance.html>
3. Memray Live Reporting（在线 heap/current snapshot 的展示边界）：<https://bloomberg.github.io/memray/live.html>
4. Memray Flame Graph Reporter（横轴不代表时间、峰值叠加）：<https://bloomberg.github.io/memray/flamegraph.html>
5. Memray v1.20.0 `_memray.pyx`（Tracker、FileReader、SocketReader 实现）：<https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyx>
6. Memray v1.20.0 `_memray.pyi`（`Interval`、`TemporalAllocationRecord`、Reader API 类型）：<https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray.pyi>
7. Memray GitHub latest release（版本事实）：<https://github.com/bloomberg/memray/releases/latest>
8. CPython 3.11 `tracemalloc` 官方文档（现有 Python 分配追踪的基线 API）：<https://docs.python.org/3.11/library/tracemalloc.html>

## 9. K567 实测补充（2026-09-25，不改变上述验收条件）

本节是初次诊断历史，当前结果以第10节K567-B为准；不再将“解析未证明”当作当前阻断。

临时 Memray 1.20.0 已用于一个既有2 Task工作循环，详见 [steps.md 的 K567 done](../03_plans_and_steps/steps.md)。
业务JUnit通过（35.138s），捕获289374208 bytes，只有一个捕获文件；命令虽然启用
`follow_fork`，不能据此宣称覆盖exec后或tmux启动的全部worker。测试内allocation分支关闭，
故本次未验证终态weakref/GC、durable SHA及temporal稳定窗口，不是等价校准通过。

两次stats解析因会话句柄处理错误而重复启动，后来主动停止；另一个30s有界FileReader
诊断exit124且stdout未刷新，解析卡点仍未知。文件SHA可读取，但摘要一致不能证明格式
完整或结束标志正确。当前不进入32 Task；需要继续完成第6节的校准，不新增第二套验收
口径。276 MiB仅是观察值，不能事后当作超预算，也不能外推32 Task存储量。未修改测试、
生产或锁文件，不把仪器诊断当作ZaoFu生产缺陷。

## 10. K567-B 读取路径诊断和连续释放校准（2026-09-25）

继续定向对照官方v1.20.0源码：

- [`source.cpp::FileSource::findReadableSize`](https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray/source.cpp)
  只对未压缩文件逐字节逆向seek/peek去掉尾部零区；LZ4路径不调用该函数。
- [`sink.cpp::FileSink::~FileSink`](https://raw.githubusercontent.com/bloomberg/memray/v1.20.0/src/memray/_memray/sink.cpp)
  释放映射/关闭文件，可选压缩，不truncate预分配尾部。不能从“尾零存在”单独判进程
  被杀或捕获损坏。当前默认mmap sink的文件增长策略会预留零填充空间。

即时输出的60s诊断证明导入仅0.08164s，FileReader构造阶段超时；12s系统调用追踪显示
文件末尾反复读取零字节，与上述分支匹配。原289374208-byte捕获尾零3031690 bytes。
没有裁剪原件；使用临时LZ4 4.4.4创建完全无损副本，先解压全量SHA验证，再运行原生
FileReader。完整读取和temporal解析31.851s/exit0，峰值RSS263308KiB；得到70335564
records、3664 snapshots、144863 sites/267966 intervals。副本保留原件全部字节，不能
把这次成功当成重新追踪、改变历史报告或补造源码绑定。

单Tracker短校准将两个真实bytearray置于不同分配函数，以独立weakref见证外层对象
持有/释放。四个60ms稳定窗内各6帧严格匹配epoch-ms区间；1MiB对象释放时其位置分配
为0，而故意保留的2MiB对象仍为2097153 bytes，全部释放后均为0。错误声明已释放与
无窗口内帧两个负例均拒绝。26帧/512 records/4411-byte捕获，0.286691s/exit0。
该校准限定为两处大分配的语义控制，不是任意Python对象泄漏证明。

默认压缩的既有真实2 Task探针JUnit1 passed/35.036s，100997587-byte捕获直接经
FileReader/temporal解析28.435s/exit0、峰值RSS261480KiB；69879153 records、3639帧。
这证明当前实际工作负载的捕获可读，不证明长样本预算可接受。两个工作循环探针都没有
开启测试内allocation分支；后续必须把方法接到原四阶段GC/weakref及durable SHA断言，
做执行时来源绑定，才可跑32 Task。不能再次把仅启外部Memray且关闭生命周期分支的
2 Task结果当作接线验收。完整证据路径、摘要及待办见[steps.md](../03_plans_and_steps/steps.md)的K567-B。

校准使用运行时`FileFormat.ALL_ALLOCATIONS`枚举比较（本安装实际值0），不依赖`.pyi`
中写出的数值1。默认解析仍要设置明确记录数上限，保留缺帧拒绝和原1200秒业务预算。

## 11. K567-C 生命周期接线与FD时点修正（2026-09-25）

tests/testkit/memray_observer.py已按上述方法接入现有工作循环，显式开启才使用Memray，
不改共享依赖。当前checkpoint已经调用统一观察函数，旧摘要中的漏调用猜测未复现。
新增PID、版本、trace模式、窗口清单摘要不变和可能降采样拒绝；解析子进程固定
120秒墙钟、90秒CPU与1GiB地址空间。2GiB捕获限制目前在checkpoint/finish检查，
不是独立运行期磁盘硬守卫，正式长测前需补齐该保护。

真实首轮FD失败经过独立最小Tracker核对：启动前4FD，追踪中多出capture、status和
pipe两端共4FD；关闭后仍有pipe两端2FD。不能把追踪中+4直接判成ZaoFu资源泄漏，
也不能据此提高最终FD限额。原+8运行期门槛保持，原最终+2及socket检查改到Tracker
关闭后，ExitStack在异常路径也保证关闭/检查先于TemporaryDirectory删除。

正式来源绑定2Task结果在`/tmp/zf-k567-workloop-memray-lifecycle-20260925-r2/run`：
1 passed/63.672694s，8个严格窗口，四阶段GC/weakref及5个durable文件SHA通过。
FD12→16→14，9个worker PID和state清理。捕获77940937 bytes，解析3340帧、
48669 sites、56633214 records；分配总量与weakref/持久化真值分开解释，父进程
观察不代表所有worker覆盖或零泄漏。原tracemalloc对照1 passed/50.084832s，
与确定性闭包并行运行，故不能用墙钟差异证明Memray更快。

显式真实观察器校准1 passed：2MiB故意保留在GC后仍可见，释放后下降，错误释放声明
被拒绝；使用新的观察器而非仅复跑旧临时脚本。sensor/runner自动影响闭包73 passed。
完整证据和K567-F1 fixed见steps/Issue；4a/4b完成，32Task/1200秒与PERF-05仍open。

4c前置核对：现有command_runner/runner已经用Popen直接持有子进程并在超时后清理其
独立进程组，尚没有捕获大小/磁盘余量传感器。当前解释器未导出os.pidfd_open与
signal.pidfd_send_signal，不可照搬其他环境的pidfd示例。进程内线程只记错误并在下一
checkpoint抛错，既非独立观察，也不能中止卡住的调用，因此本次线程候选撤回，源码
恢复并SHA核对到K567-C通过版本。下一步应在直接拥有的Popen生命周期内验证外部
观测、超限中止与清理，不能以守卫自称成功或单次小样本替代长测保护。

## 12. K567-D 外部Guard与32Task规模限制（2026-09-25）

核对本地Python3.11 Popen.send_signal：它在发送前poll且用Popen内部waitpid锁避免已
回收PID重用；调用方不得自行按台账PID发信号或另行waitpid。本次复用run_pytest直接
持有的子pytest，父进程按CaptureBudget观测文件/磁盘/时间，超限先SIGINT请求finally，
超过宽限才使用既有进程组清理。未接外层命令包装，也未依赖分配中的后台线程。
policy/result摘要接正式报告及失败包，子测试吞中断exit0不能绕过Guard拒绝。

真实64MiB负例首次证明pytest中断会缺失call properties，因此独立lifecycle sidecar
原子保留相同属性，且不伪造缺失的pytest阶段。第二轮保留非绿，6PID/tmux/state清理、
最终FD12→14，证明现有工作循环的真实中断路径；K567-F2标fixed。

32Task单轮事先固定capture2GiB/free4GiB/poll0.5秒/外层1320秒/清理60秒。业务1200秒
与RSS/FD/锁/候选标准保持。243.242410秒运行以harness_failure结束，424次检查发现
原始预分配文件2351398912 bytes超过2GiB，剩余磁盘34283311104 bytes充足。此时
7done/1in_progress/24backlog，零candidate；没有终态GC或最终解析。31PID/root清理，
最终FD12→14。关闭后压缩771215623 bytes不撤销运行时超预算；0.5秒采样不是硬配额，
观察值可能因文件预分配瞬间跨阈值。完整路径/摘要在steps K567-D。

当前结论：方法在短样本保持连续信息与释放语义，但在既定长测容量预算下不成立。
不能用压缩后小于上限替换运行时限制，不能按7Task外推通过，也不立即提高阈值再试。
后续只针对同等信息量的有界存储/解析路径做有限复核；无等价路径时保留F2缺项并推进
其余主干。新Guard和报告链联调87+121项分别通过，不等于PERF-05通过。

## 13. K567-D 后三条有界连续追踪路线复核（2026-09-25）

### 13.1 决策与现有证据

**停止继续扩展当前 Memray 长样本方法，转推进其他可执行的非降级主干；K552-F2、
32 Task 持续分配和完整 PERF-05 继续开放。**这不是证明所有 profiler 都不可能满足
目标，而是在固定的三个候选、现有公共 API 和既有预算内，没有找到足以批准下一轮
长测的等价路线。本节没有实现新的 profiler、修改生产逻辑或运行长测试。

独立回读 `/tmp/zf-k567-mainline-memray-20260925-r1/run/capture-guard.json`：
固定 `max_capture_bytes=2147483648`，424 次检查，峰值原始文件 2351398912 bytes，
`violation.kind=budget`、`errors=[capture_size]`，由已持有子进程的 Guard 请求中断；
`testkit-run.json` 为 `harness_failure`、243.242410 秒。当前 `stat` 的关闭后捕获为
771215623 bytes。后一个数值不能撤销前一个运行时违规。7 done/1 in_progress、未到
GC 终态的业务事实见第 12 节；本次只读核对，不把旧样本重述成新验收。

保持：业务 1200 秒、运行期 capture 2GiB、parser 120 秒墙钟/90 秒 CPU/1GiB AS；
不启停 tracer、不丢 raw records、不更改生产/锁文件。本次只比较以下三条候选。

### 13.2 候选一：公共 FileDestination（含 buffered）——不作为下一轮长测方案

默认 FileSink 在运行期写原始文件：`writeAll()` 空间不足时调用 `grow()`，后者按
`(current_size + needed) * 1.1` 向上页对齐并 `posix_fallocate`；写入窗口为共享 mmap。
这解释为何 0.5 秒抽样看到一次跃过 2GiB，也解释文件尺寸不等于有效记录字节数。
不能仅从 2351398912 bytes 断言有效 records 已超过 2GiB，更不能据此外推最终记录量。
来源：[sink.cpp 98–124、151–239](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.cpp#L98)。

压缩不是连续进行：FileSink 析构先 unmap/close，再 `compressFile()`；压缩函数读取
整个原件，写入 `<filename>.lz4.tmp`，成功后 rename 回原路径。在压缩期间原件与
压缩临时文件会同时存在，所以还要考虑磁盘总占用。`compress_on_exit=True` 无法把
运行期原件变小，`False` 更不会解决这个预算。
来源：[sink.cpp 61–92](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.cpp#L61)、
[263–278](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.cpp#L263)。

公共 `FileDestination(buffered=True)` 确实存在，不应忽略：它使用 1MiB 内存缓冲、
普通 `write()`，可避免 mmap 的约 10% 增长预留及尾部 padding；但记录流没有变少，
仍然直到析构才压缩。它解决的是 I/O 兼容性/预留方式，而非任意长记录流的 2GiB
有界存储。源码也没有运行中压缩 destination、轮转或最大 capture 字节参数。
来源：[公共选择入口 _memray.pyx 763–785](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray.pyx#L763)、
[sink.h 80–101](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.h#L80)、
[sink.cpp 431–503](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.cpp#L431)。

header 也要求可 seek：初始化写 header；结束时写 trailer，再 seek 到起点回写最终
统计；这不适合直接把普通 capture 路径换成压缩管道。默认 mmap 路径还要求文件支持
mmap/fallocate。来源：[tracking_api.cpp 875–878、929–933](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/tracking_api.cpp#L875)、
[record_writer.cpp 489–543](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/record_writer.cpp#L489)。

**拒绝依据：**尚无 buffered 路线完整 32 Task 的原始记录量/运行时间/解析预算证据；
它至多避免预留空间，不提供满足最终目标的新有界机制。不能只切换 buffered 后
重复长测求绿，也不能把关闭后压缩尺寸或去尾零当运行期合规。保留 buffered 作为
官方 I/O 能力事实，不将其升级为已证明的容量解决方案。

### 13.3 候选二：SocketDestination 原始流——不采用自建回放/压缩路线

SocketSink 是 `PIPE_BUF` 缓冲加阻塞 `send()`，数据满时 drain，结束时 flush/close；
`seek()` 恒返回 false，且不能克隆为子进程 sink。它的字节流理论上可由独立进程
完整接收并无损压缩，因此不能笼统断言“socket 根本无法保存”。但这不是当前公共
API 已提供、已验证的 replay capture 路线。
来源：[sink.cpp 294–369](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/sink.cpp#L294)。

公共 SocketReader 后台逐条消费 allocation 更新当前聚合；对 `MEMORY_RECORD`
直接跳过，当前快照只返回聚合。它既不暴露 raw record 导出，也不暴露对应 capture
中的 epoch 时间水位；`is_active` 只表示 reader 未停止。源码把 EOF 与 ERROR 都转成
停止状态，不能把 `is_active=False` 独立解释为完整成功关闭。
来源：[socket_reader_thread.cpp 8–54、79–93](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/socket_reader_thread.cpp#L8)、
[公共 SocketReader 1649–1731](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray.pyx#L1649)。

原始 socket 流仍包含 writer 记录的 memory records，不能把“SocketReader 丢弃它们”
误说成“线上从未发送时间”。但是最终 header 回写因 `seek=false` 失败；Tracker
析构仍写 trailer 后尝试回写，未把失败变成可供外部接收器使用的最终统计证明。
来源：[record_writer.cpp 264–267](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/record_writer.cpp#L264)、
[489–501](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/record_writer.cpp#L489)、
[tracking_api.cpp 929–933](https://github.com/bloomberg/memray/blob/v1.20.0/src/memray/_memray/tracking_api.cpp#L929)。

**缺失的必要证据：**公共端到端工具能保留所有原始字节、验证 trailer/成功结束，
无需私自修 header 就能用官方 Reader 正确复放 temporal；能保留严格窗口的时间序号；
断连/磁盘满/压缩失败可 fail-closed；背压不使业务超时；完整解析仍满足 120/90 秒及
1GiB AS。即使解决压缩存储，完整记录数也不会减少，不能因此保证 parser 合规。

**拒绝依据：**在已检查的公共 API 中没有同时覆盖上述合同的现成路线。自行实现
socket 接收器、完整性协议或 header 修复会变成另一个测试基础设施项目；本阶段
不实现自定义 Memray fork/FUSE/轮转，也不以聚合快照/随机采样代替 raw 历史。

### 13.4 候选三：CPython 3.11 连续 tracemalloc——保留基线，不宣称有新减负接口

针对“是否存在等价但更低开销的公共入口”，本次核对安装版本对应官方源码
`v3.11.15/Modules/_tracemalloc.c`，而非更高 Python 版本功能：

- `start(max_nframe)` 只接受至少 1 帧，安装 RAW/MEM/OBJ allocator hooks；当前
  `start(1)` 已是帧数下限。[956–1016](https://github.com/python/cpython/blob/v3.11.15/Modules/_tracemalloc.c#L956)。
- `get_traced_memory()` 只读取当前与峰值累计量，`reset_peak()` 只改峰值字段，均未
  移除每次分配/释放 hook 或 traceback/hash 表维护。
  [1630–1667](https://github.com/python/cpython/blob/v3.11.15/Modules/_tracemalloc.c#L1630)。
- `stop()` 卸载 hooks 并 `tracemalloc_clear_traces()`；`clear_traces()` 清除 trace/
  domain/traceback 表。不能用这些入口实现无历史丢失的分段跟踪。
  [845–859](https://github.com/python/cpython/blob/v3.11.15/Modules/_tracemalloc.c#L845)、
  [1020–1039](https://github.com/python/cpython/blob/v3.11.15/Modules/_tracemalloc.c#L1020)。
- 模块公开方法表没有连续 raw event stream、在线压缩或等价无损采样开关。
  [1671–1684](https://github.com/python/cpython/blob/v3.11.15/Modules/_tracemalloc.c#L1671)。

这里必须区分合同：连续 tracemalloc 保留当前仍存活分配的位置、支持检查点 snapshot
比较，但**它不是 Memray ALL_ALLOCATIONS 的完整 alloc/free 事件日志**。减少 snapshot
频率仍可保持 tracemalloc 连续性，却不能解除 hook 主成本，也不会自动补齐所有中间
生命周期时序。已知长样本的 1200 秒失败不能用只读总量、缺少检查点或相同小样本
再跑一遍来关闭。没有找到值得现在重新执行 32 Task 的新等价低开销公共 API。

### 13.5 收口与重新进入条件

本节关闭“最多三候选的一手方法复核”子项，不关闭原验收。当前停止扩展 Memray
存储/解析方法，保留 K567-C 短样本通过与 K567-D 规模失败，推进其他非降级主干。
不修改 2GiB/1200 秒/parser 限额，不修生产缓存，不新增 profiler 项目。

只有出现以下**外部新证据之一**才重新进入：官方提供可连续无损压缩并公开验证完整
窗口回放的受支持接口；或当前版本已有公共路线的完整性/严格窗口/全量解析都在原
预算内有可复验依据。仅更换路径、buffered 开关、调小采样间隔或压缩后较小，不是
重跑触发条件。不是把该主项永久降级，而是在既定约束下保留缺项、停止无新方法的
重复长测。
