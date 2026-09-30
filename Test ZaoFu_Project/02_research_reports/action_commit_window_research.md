# Controlled Action 提交窗口：子进程崩溃与同目录重启测试调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s4_action_crash.py`。已有真实 action 提交窗口检测入口；本报告的实施前假设不再等于待新建测试，生产重复副作用结论仍查问题台账。

研究日期：2026-09-14；源码基线：`142a62a`，同时保留当前工作区已有改动。
范围：真实 `ControlledActionService.execute → update-task → proposal resolved`。
本文只提出测试方法，未运行崩溃实验、未新增测试、未修改生产代码。
成功标准：定位真实窗口 → 用独立持久化观测证明命中 → 对照 baseline 与两次重启重放。
约束遵循根目录 [AGENTS.md](/home/uisee/zaofu_v7/AGENTS.md)：状态经 Store/EventWriter，使用配置中的 state dir。

## 1. 当前代码支持的结论

`ControlledActionService` 继承 proposal 执行包装，`update-task` 分派到真实 `_update_task`：[service 定义与分派](/home/uisee/zaofu_v7/src/zf/runtime/control_actions.py:114)。
有 `proposal_event_id` 时，`execute` 持有 proposal execution 文件锁，再读取 ledger 做 gate。
没有该字段会走普通 action 路径，因此夹具漏传字段就没有测试目标幂等边界。
证据：[execute 与 proposal gate](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:20)。

真实成功顺序如下；这些步骤分别执行，并没有在同一个持久化事务中提交：

1. `proposal_execution_gate` 验证 proposal/action/payload；已 resolved 则直接返回。
2. `ControlledActionOrchestrator.run` 写 `runtime.action.attempt.started`，调用真实 handler。
3. `_update_task` 执行 `TaskStore.update`，然后写 `task.updated`。
4. handler 写 `runtime.action.completed` 和 surface completion，attempt 包装再写 attempt completion。
5. proposal 包装最后单独写 resolved 事件，随后返回成功结果。

证据：[proposal 包装](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:98)、[task 更新](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_product.py:631)、[完成事件](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_emit.py:8)、[attempt 包装](/home/uisee/zaofu_v7/src/zf/runtime/action_orchestrator.py:46)。

legacy `kanban.agent.action.proposed` 对应 `kanban.agent.proposal.resolved`；新 `operator.action.proposed` 对应 `operator.action.resolved`。主用例选择 legacy 分支；新分支可用同一方法参数化。
证据：[类型常量](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:17)、[resolved 分支选择](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:130)。

gate 的重复执行证据是 resolved 事件，另对 `task.created.request.proposal_event_id` 有特判。
当前 gate 不把 `task.updated` 或 `runtime.action.attempt.completed` 当作已提交回执。
因此“写入完成但 resolved 未写”后再次执行可能重复更新，这是源码推断，尚非本次实测发现。
证据：[gate 去重条件](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:310)。

## 2. 现有 S4 矩阵的证明范围

现有 `_run_transaction` 使用真实 EventLog/TaskStore，但事务编排和固定事件 ID 由测试自建；
它没有调用 `ControlledActionService.execute`，不能替代本题真实入口的证明。
同进程恢复复用 `ExternalEffectSpy`，该 spy 的 `_results` 本来就按 key 阻止 callback 重复。
其“执行一次”证明包含测试替身提供的去重能力，不能推出生产 proposal gate 同样可靠。
证据：[现有事务与恢复](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_commit_matrix.py:43)、[spy 去重](/home/uisee/zaofu_v7/tests/testkit/external_sinks.py:36)。

新子进程矩阵明确排除了 `after_external_effect_before_result_event`，各次 child 又新建 spy；
当前子进程调用也没有显式 timeout。应新增真实 action 的有界覆盖，并保留原矩阵各自的用途。
证据：[child 初始化](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_commit_matrix.py:143)、[排除项与进程调用](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4_commit_matrix.py:207)。

## 3. 建议的四个真实 seam

所有包装只在测试子进程安装，保留并调用原始方法；生产代码不增加 failpoint。
绑定目标 state path、task ID、proposal ID，避免夹具初始化或其他 action 命中包装。
seed 完成后再安装包装。选择非终态 Task 的 `title: old → new`，避免引入终态归档窗口。
相同 title 仍产生 updates，非终态 `TaskStore.update` 仍执行保存，因此最终值相同不足以证明一次性。
证据：[updates 构造](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_helpers.py:293)、[真实 Store 保存](/home/uisee/zaofu_v7/src/zf/core/task/store.py:292)。

| failpoint | 测试包装的精确位置 | 故障后应观察到的窗口 |
| --- | --- | --- |
| `before_store_update` | 目标 `TaskStore.update` 调原方法之前 | title=old；更新计数 0；task.updated=0；resolved=0 |
| `after_store_update_before_task_updated` | `EventWriter.emit("task.updated")` 调原方法之前 | title=new；更新计数 1；task.updated=0；resolved=0 |
| `after_action_result_before_resolution` | 目标 resolved 的 `emit` 调原方法之前 | title=new；更新计数 1；task.updated=1；attempt.completed=1；resolved=0 |
| `after_resolution` | 目标 resolved 的真实 `emit` 返回之后 | title=new；更新计数 1；task.updated=1；resolved=1 |

第二个点必须在 Store 更新的观察回执写好后触发；第三个点必须确认 attempt completion 已在 ledger。
第四个点应检查原方法返回事件确为目标类型，并从 ledger 读到该事件，防止 schema 拒绝造成伪命中。
`EventWriter.emit` 最终经过 append、schema gate 和 projectors，返回前后的含义应保持完整。
证据：[EventWriter append 与 projectors](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:87)。

## 4. 独立持久化观察回执，不给被测系统添加幂等性

为每个 case 建立 `observer/effects.jsonl`；它是测试证据，不是 canonical runtime state。
包装 `TaskStore.update`：调用原方法 → 确认成功返回目标 Task → 追加完整 JSON 行 → flush/fsync。
每条记录保存 case、operation key、proposal/task ID、child PID、阶段及请求字段；每次成功都写一条。
不按 operation key 去重，不缓存结果，不读取 ledger 决定是否跳过原方法，不覆盖真实 action 返回值。
产品 gate 绝不读取该文件；父进程跨所有 child 统计同一 operation 的全部记录，重复行也照数。

这里的 effect 明确指成功的本地 `TaskStore.update`，不是远端 API 成功次数或存储设备写块次数。
计数同时对照 canonical Task 和 `task.updated`；它能够发现“title 一样，但再次执行了保存”。
若默认 projector 也更新该 Task，须用 baseline 验证归属，不能把观测到的额外调用悄悄过滤为一次。

写记录后先 `f.flush()`，再 `os.fsync(f.fileno())`，检查错误并关闭文件，才触发预定硬退出。
Python 官方明确要求缓冲文件先 flush 再 fsync：[Python 3.11 os.fsync](https://docs.python.org/3.11/library/os.html#os.fsync)。
POSIX 对 fsync 的同步要求依赖实现与存储语义：[POSIX fsync（Issue 6）](https://pubs.opengroup.org/onlinepubs/009695399/functions/fsync.html)。
回执在此只表示已持久化的测试观察；POSIX 没有替应用定义 action receipt 或跨文件 exactly-once 协议。
Store 写入与 observer 追加之间仍有间隙；本矩阵只在 observer 完成后注入故障，不声称覆盖任意时刻掉进该间隙。
所以回执存在可以支持“原方法已返回”，任意崩溃后的回执缺失不能普遍证明“原方法未执行”。

## 5. 子进程、命中证据与同目录重启

父进程使用独立 Python 解释器运行 child；每个 case 有自己的 project root 和配置 state dir。
首次 seed 经 TaskStore/EventWriter 完成；重启复用同一 project root、state dir、proposal 身份与签名配置。
重启不重建夹具、不清空 ledger，不复用内存 spy/Store/service；创建新对象后再调用真实 execute。
这里的 replay 是“重新提交同一 proposal 的 action”，不是自行编写 event-fold 后直接改 Store。
可以保留新的请求 attempt ID，但 proposal ID、revision、语义 payload 和 digest 必须保持一致。
相关 digest 排除 transport 字段：[proposal 语义身份](/home/uisee/zaofu_v7/src/zf/runtime/kanban_proposals.py:26)。

命中点写独立 `fault-hit.json`，记录 case/failpoint/PID/目标身份，flush/fsync 完成后直接 `os._exit(86)`。
无需先抛异常再在外层退出，避免栈展开先释放锁、执行 finally，改变要模拟的崩溃窗口。
父进程同时要求退出码 86、精确 witness 和对应窗口快照；单独非零退出码不能证明命中了 failpoint。
Python `_exit` 不运行 cleanup 或刷新 stdio；POSIX `_exit` 也规定不运行 atexit、进程终止关闭文件描述符。
官方来源：[Python os._exit](https://docs.python.org/3.11/library/os.html#os._exit)、[POSIX.1-2024 _exit](https://pubs.opengroup.org/onlinepubs/9799919799/functions/_exit.html)。

给 baseline/fault/replay 每个 child 显式 timeout，例如 30 秒；此预算是测试选择，不是平台保证。
`subprocess.run(timeout=...)` 超时会杀死并等待直接 child，然后抛 `TimeoutExpired`；创建进程阶段不保证可中断。
`Popen.communicate(timeout=...)` 超时不会自动杀 child，调用者须 kill 后再次 communicate 回收输出。
若以后启动后代进程，应单独隔离 session/process group 并负责清理；本最小 action 用例无需 provider/tmux/Web。
官方来源：[Python subprocess.run](https://docs.python.org/3.11/library/subprocess.html#subprocess.run)、[communicate](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen.communicate)。

不要在重启前删除 `.lock` 来“解锁”；本仓锁文件有意保留，锁的同步对象是打开的文件描述符。
应由重启是否在超时内真实完成来验证锁恢复；证据：[FileLock](/home/uisee/zaofu_v7/src/zf/core/state/locks.py:19)。

## 6. baseline / fault / replay 对比与判据

每个 case 固定四个阶段：无故障 baseline、故障 child、同目录 replay-1、再次全新进程 replay-2。
baseline 至少验证一次更新、一次 task.updated、一次 resolved、正确 title；再验证重复提交不增加更新。
fault 先验证第 3 节对应中间状态，再允许 replay；故障 witness 缺失、签名/配置/导入失败属于测试基础设施失败。
replay-1/2 后严格要求该 operation 累计本地更新次数为 1；replay-2 相对 replay-1 的新增次数为 0。
并检查最终 title、resolved 绑定 proposal/revision/digest、task.updated 数量及完成事件的因果关系。
对比保留事件顺序和重复数量；可归一化随机事件 ID/时间，但必须保留对应关系，禁止 set 去重后比较。
允许 retry 的 attempt 审计与 baseline 不同，但要单列并解释，不能只比最终 title 或把所有事件整体忽略。
事件从 active 加 archives 读取：[EventLog.read_all](/home/uisee/zaofu_v7/src/zf/core/events/log.py:316)。

基于当前源码，四点恢复后的更新累计次数预计为 `1 / 2 / 2 / 1`；这是待实测假设，不是通过报告。
中间两点若确实出现 2 次，保存原始 observer、完整 ledger、witness、快照和各 child 的退出码/stderr。
记录已知问题时可使用严格 xfail，但只匹配“已核实窗口后发现重复 effect”的专用异常；
timeout、窗口不命中、fixture 失败和任意其他 AssertionError 都必须保持普通失败，不能被 xfail 吞掉。

## 7. 与旧研究、Issue 的关系及结论边界

本研究细化了 [persistence_crash_research.md](<./persistence_crash_research.md>) 的真实 seam、同目录重启、独立效果计数建议。
旧文“按幂等键最多重放一次”的措辞应拆成调用尝试数与实际效果数，并明确谁提供去重；key 本身没有该保证。
旧 [ZF-TEST-007](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:764>) 是 dispatch 前状态先写，
[ZF-TEST-002](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1288>) 是 dispatch 后上下文缺失，
[ZF-TEST-013](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1013>) 是归档拼接后重复事件。
它们与本题可能属于同类提交窗口问题，甚至可能与后续补充 Issue 重合；须按真实 seam、触发条件、失败不变量去重。
本次未复现，不能直接新增“已确认缺陷”，也不能把旧 Issue 的实验结果当作本 action 已失败的证据。

本方法只证明同一主机上的进程硬退出、同目录重启和具体 proposal 的本地 mutation 边界。
`EventLog.append` 关闭文本文件但没有逐条 fsync；atomic Store 写入 fsync 临时文件后 replace，没有父目录同步。
证据：[EventLog.append](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)、[atomic_write_text](/home/uisee/zaofu_v7/src/zf/core/state/atomic_io.py:15)。
POSIX 说明目录操作原子性与持久性有别：[POSIX.1-2024 文件系统持久性说明](https://pubs.opengroup.org/onlinepubs/9799919799/xrat/V4_xbd_chap01.html)。
因此不得把测试通过扩展为断电、内核崩溃、NFS、磁盘故障或 GitHub/飞书等远端副作用的 exactly-once 证明。
