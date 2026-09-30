# 持久化崩溃窗口与可复现 failpoint 调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s4_persistence.py`。已有进程级持久化检测；真实掉电/文件系统损坏方法完成与实机验收仍分开。

本文面向 ZaoFu TestKit 的 S4（恢复、并发和提交边界）测试设计。只引用
POSIX、Linux man-pages 与 Python 官方文档；不修改 ZaoFu 生产代码。

## 1. 规范层保证及其边界

### `rename`/`os.replace` 是命名空间原子操作

Python 官方文档说明，Unix 上 `os.rename` 和 `os.replace` 成功时是原子操作，
`os.replace` 会在目标是文件时原子替换目标（[Python `os` 文档，
rename/replace](https://docs.python.org/3/library/os.html#os.replace)）。
Linux `rename(2)` 进一步规定：如果目标存在，替换不会出现“目标路径短暂不存在”
的观察窗口；如果调用失败，旧目标仍会保留（[Linux `rename(2)`，
description](https://man7.org/linux/man-pages/man2/rename.2.html#DESCRIPTION)）。

这只证明同一文件系统上的**可见命名空间切换**。它不等于突然断电后的持久化：
数据文件和父目录的目录项仍可能尚未刷到稳定存储。因此测试应把
“读者不会看到半个目标文件”与“断电后目标一定存在”分成两个判据，不能混为一谈。

### `fsync` 文件不等于 `fsync` 父目录

Linux `fsync(2)` 说明，`fsync(fd)` 会把文件数据和关联元数据刷到设备，保证系统
崩溃/重启后可重新读取；但它**不保证包含该文件的目录项已经落盘**，要获得该保证
还需要对目录文件描述符执行 `fsync`（[Linux `fsync(2)`，
description](https://man7.org/linux/man-pages/man2/fsync.2.html#DESCRIPTION)）。
Python 官方文档同样要求：对缓冲文件对象先 `flush()`，再对 `fileno()` 调用
`os.fsync()`，否则 Python 的用户态缓冲区可能仍未写入内核（[Python `os.fsync`,
3.11 文档](https://docs.python.org/3.11/library/os.html#os.fsync)）。

因此，典型的可持久化替换序列应当显式测试：

1. 在目标目录创建唯一临时文件；
2. 写完整内容，`flush` + `os.fsync(temp_fd)`；
3. `os.replace(temp, target)`；
4. 打开父目录并 `os.fsync(dir_fd)`（Unix）；
5. 重启后验证目标内容和目录项。

如果实现只完成第 2、3 步，测试报告应标记为“进程崩溃下的原子可见性通过，
突然断电持久性未被证明”，而不是直接宣称 durable commit。

### 追加写与 `O_APPEND`

POSIX `write()` 规定，设置 `O_APPEND` 时，每次写入前把偏移移到 EOF，且改变偏移
与写入之间不得插入其他文件修改（[POSIX `write()`，O_APPEND](https://pubs.opengroup.org/onlinepubs/9699919799.2018edition/functions/write.html)）。
Linux `open(2)` 将其描述为一个原子步骤，但明确警告 NFS 上多进程追加可能损坏，
因为 NFS 客户端只能模拟 append（[Linux `open(2)`，O_APPEND](https://man7.org/linux/man-pages/man2/open.2.html#DESCRIPTION)）。
Linux `write(2)` 还指出，成功写入可能少于请求字节数，调用方必须处理部分写入
（[Linux `write(2)`](https://man7.org/linux/man-pages/man2/write.2.html#RETURN_VALUE)）。

ZaoFu 的 `EventLog.append` 通过锁把轮转和追加串行化，但使用 Python 文本
`open(..., "a")` 写入后没有在每条事件上 `fsync`。所以现有测试可以证明：

- 同一主机、锁生效时，多进程追加不会互相覆盖；
- 读者只消费完整换行，尾部半行会留到下一次读取；
- 进程级崩溃后，已由内核写出的完整行可被重放。

但不能仅凭这些测试证明每个已返回的 `append` 在断电后必然存在。若未来需要该
保证，应增加明确的 durability 模式和目录 `fsync`，并单独设计真实电源故障测试；
当前 TestKit 不应偷偷把进程崩溃测试称为电源故障证明。

## 2. ZaoFu 当前实现对应的风险点

基于当前源码（`src/zf/core/state/atomic_io.py`、`rotation.py`、
`events/log.py`）：

* `atomic_write_text` 使用同目录临时文件、`flush` + `os.fsync`，随后
  `os.replace`；这提供进程内的“旧文件或新文件”可见性，但没有父目录
  `fsync`，所以目录项持久性应作为未证明能力记录。
* `rotate_if_needed` 的无冲突分支执行 `active.rename(archive_path)`，命名空间切换
  是原子的；同日冲突分支却是“读 archive + 读 active → 覆盖写 archive →
  `active.unlink()`”，中间没有事务性标记、临时文件替换或目录 `fsync`。
* `EventLog._rotate_by_size_if_needed` 用递增序号选择 archive 后执行
  `self.path.rename(archive_path)`。选择序号、重命名和其他进程之间的并发/崩溃点
  必须通过真实子进程测试，而不能只调用一个测试自有的假事务函数。

同日冲突分支尤其需要覆盖以下结果：写入 archive 后在 unlink 前崩溃时，重启读取
active+archive 可能看到重复事件；unlink 后在目录项落盘前崩溃时，可能出现旧 active
和新 archive 的不同组合。若测试确认了重复或丢失，应作为 ZaoFu issue 记录，不能
在测试中“去重后算通过”。

## 3. 可复现的 failpoint 设计

### 命名点

failpoint 必须位于真实生产调用的前后，而不是只存在于测试 helper：

| 名称 | 真实位置 | 重启后的主要判据 |
| --- | --- | --- |
| `before_event_append` | `EventLog.append` 写入前 | 没有新事件，状态不提前推进 |
| `after_event_append_before_projection` | 事件写入返回后、投影前 | 事件可重放，投影最终收敛一次 |
| `before_store_atomic_replace` | 临时文件完成后、`os.replace` 前 | 旧快照仍完整，无半文件 |
| `after_store_atomic_replace` | `os.replace` 返回后、调用者返回前 | 新快照完整，重复恢复不再写副作用 |
| `after_artifact_write_before_digest_event` | artifact fsync 后、digest 事件前 | artifact 可读但 gate 不得误完成 |
| `after_external_effect_before_result_event` | 外部 API 成功后、结果事件前 | 重启后按幂等键最多重放一次 |
| `before_archive_rename` | active 与 archive rename 前 | active 保留且可继续追加 |
| `after_archive_rename_before_directory_fsync` | rename 后、目录同步前 | archive/active 至少一个完整可解析；持久性结论单列 |
| `after_archive_concat_before_active_unlink` | 冲突 archive 覆盖后、unlink 前 | 不得静默重复或丢事件；若不能保证则 fail + issue |
| `after_lock_acquire` | 持有 `locked_path` 后 | 硬退出不留下永久锁，后续进程可取得锁 |

### 注入和进程控制

1. 每个 failpoint 启动**新进程**，环境变量指定唯一 `run_id`、state dir 和
   failpoint 名称；不要在 pytest 进程中直接 `os._exit`，否则会污染测试收集器。
2. 子进程在命中点先写一个原子 `ready` 标记（唯一临时文件再 `os.replace`），
   父进程等待该标记后才发送 `SIGKILL` 或受控异常。没有 ready 标记时应判为
   harness failure，而不是产品通过/失败。
3. 给每个子进程显式超时；超时先终止进程组，再回收 leader 和残留子进程，
   `finally` 中清理 ready、临时文件、锁和端口。
4. 重启使用同一 state dir、同一 project identity 和同一 event signer；读取
   active 加所有 archives，禁止只看 active 文件。
5. 将无故障 baseline 与故障后 snapshot 比较：事件集合/顺序、canonical state、
   artifact digest、外部副作用计数分别判断；不要只比较一个最终状态字符串。

### 每个 case 的固定 oracle

* **原子性**：目标 JSON 能完整解析，不能出现截断或空文件；临时文件不能成为
  canonical 文件。
* **追加完整性**：每一行都是完整 JSON/schema；同一 `event_id` 不得出现两次，
  除非该 case 明确记录为已知 issue。
* **重放收敛**：从故障后的 ledger 重启并 replay，结果应与无故障 baseline 等价。
* **状态权威**：Task/Feature 等状态只能由合法事件投影产生；旧 attempt/generation
  不能覆盖新状态。
* **副作用边界**：每个外部幂等键的成功调用次数 `<= 1`；未知或未授权 sink
  调用次数为 0。
* **可诊断性**：保存 failpoint、信号、退出码、PID、事件计数、archive inventory、
  snapshots 和日志，供 issue 重现；环境启动失败单独分类为 harness failure。

## 4. 建议的测试矩阵与判定

### A. 原子替换矩阵

对 `atomic_write_text` 和所有使用它的 Store，各执行：无目标、覆盖旧目标、并发
读取、rename 前崩溃、rename 后崩溃。进程崩溃层要求“旧或新完整”；目录持久性层
若未实现目录 `fsync`，结果记为 `unproven`，不能伪装成 pass。

### B. EventLog 追加/轮转矩阵

对时间轮转、按大小轮转、archive 不存在、同日 archive 已存在、多进程追加、读者
并发读取分别在 rename 前、rename 后、concat 后 unlink 前注入硬退出。通过条件为
重启后所有完整事件恰好一次、顺序可解释、无非法 JSON；任何重复/丢失都输出最小
复现并登记 issue。

### C. 锁和恢复矩阵

在获得锁、写事件、投影、释放锁各点硬退出，重新启动两个竞争者。通过条件为没有
永久锁、没有重复 dispatch、旧 worker 结果不能覆盖当前 attempt；超时或清理失败
必须归类为测试基础设施失败。

## 5. 对 TestKit 实现的范围建议

优先实现命名 failpoint、ready 握手、进程组终止、统一 snapshot/oracle 和 archive
inventory 采集；这些都可由 pytest + 标准库完成，不需要 Chaos Toolkit。只有当
ZaoFu 部署成跨节点服务、需要模拟网络分区或数据库故障时，才在发布演练层引入
系统级 chaos 工具。当前测试应诚实区分：

* **已证明**：同一主机进程崩溃、重启、重放和命名空间原子性；
* **待证明**：突然断电后的目录项持久性、NFS 追加语义、真实磁盘/文件系统故障；
* **失败即 issue**：重复事件、丢事件、错误状态推进、重复外部副作用。

## 参考来源

1. [Python `os.rename`/`os.replace` 文档](https://docs.python.org/3/library/os.html#os.replace)
2. [Python 3.11 `os.fsync` 文档](https://docs.python.org/3.11/library/os.html#os.fsync)
3. [Linux man-pages `rename(2)`](https://man7.org/linux/man-pages/man2/rename.2.html)
4. [Linux man-pages `fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html)
5. [Linux man-pages `open(2)`](https://man7.org/linux/man-pages/man2/open.2.html)
6. [Linux man-pages `write(2)`](https://man7.org/linux/man-pages/man2/write.2.html)
7. [POSIX `write()`（2018 edition）](https://pubs.opengroup.org/onlinepubs/9699919799.2018edition/functions/write.html)
