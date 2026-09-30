# TestKit runner 并发证据与 claim 文件调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/runner.py`。已有独占 claim 与独立输出合同；合作 writer 的边界不等同抵御任意恶意进程。

调研日期：2026-09-12。范围：Python 官方文档、POSIX.1-2024 规范、Linux man-pages；目标是核对并发进程创建唯一证据/claim 文件、TOCTOU 风险、原子替换和 writer 协调语义。本文只记录证据与回归建议，不修改 runner 实现。

## 结论

唯一 claim 必须通过一次系统调用完成“检查不存在并创建”：`os.open(path, O_CREAT | O_EXCL | O_WRONLY, 0o600)` 或 Python 内置 `open(path, "x")`。POSIX 要求同目录、同名、同样带 `O_CREAT|O_EXCL` 的并发 `open()` 之间该检查与创建具有原子性；竞争者得到 `EEXIST`/`FileExistsError`。先调用 `exists()`，再调用 `open("w")` 是典型 TOCTOU，竞争者可以在两调用之间创建文件，而后者会截断或覆盖它。

写入完整证据应使用“唯一临时文件 -> flush/fsync（若需要崩溃持久性）-> `os.replace`”流程。`os.replace`/POSIX `rename` 对同一文件系统中的目标替换是原子的，读者不会观察到半写文件，但目标已存在时会被替换，多个成功 writer 的结果是未定义顺序的 last-writer-wins；原子替换不等于并发更新合并，也不自动保证断电后目录项持久化。

## 官方语义

| 主题 | 规范要求与边界 |
| --- | --- |
| 独占创建 | POSIX `open()` 明确规定：`O_CREAT|O_EXCL` 下，存在检查与创建相对同目录同名并发 `open()` 必须原子；已存在（包括符号链接）则失败 `EEXIST`。[POSIX open](https://pubs.opengroup.org/onlinepubs/9799919799/functions/open.html) |
| Python 独占模式 | 内置 `open(..., mode="x")` 表示 exclusive creation，路径已存在抛 `FileExistsError`；可直接表达 claim 创建而无需手写 `exists()` 预检。[Python `open`](https://docs.python.org/3/library/functions.html#open) |
| Python 低层标志 | `os.open` 返回文件描述符并接受 `O_CREAT`、`O_EXCL` 等平台标志；文件描述符默认 non-inheritable。具体标志语义由 Unix `open(2)` 定义。[Python `os.open`](https://docs.python.org/3/library/os.html#os.open) |
| TOCTOU 临时名 | `tempfile.mktemp()` 先返回不存在的名字，文档警告其他进程可在返回与后续创建之间抢占；`mkstemp()` 立即创建并假设平台正确实现 `O_EXCL` 时无 creation race。[Python `mktemp` 警告](https://docs.python.org/3/library/tempfile.html#tempfile.mktemp)、[Python `mkstemp`](https://docs.python.org/3/library/tempfile.html#tempfile.mkstemp) |
| 原子替换 | `os.replace(src, dst)` 成功时是原子操作（POSIX 要求），同文件系统限制；若目标是文件会静默替换。[Python `os.replace`](https://docs.python.org/3/library/os.html#os.replace) |
| rename 可见性 | Linux `rename(2)` 说明目标存在时原子替换，观察者不会看到目标短暂缺失；操作期间可能同时存在 old/new 两个名字。[Linux `rename(2)`](https://man7.org/linux/man-pages/man2/rename.2.html)、[POSIX `rename`](https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html) |
| 崩溃持久性 | Python `os.fsync(fd)` 会刷新文件数据和元数据；Linux `fsync(2)` 特别指出仅 fsync 文件不一定保证目录项落盘，需按要求对目录 fd 再 fsync。原子可见性与断电后持久性是两项不同保证。[Python `os.fsync`](https://docs.python.org/3/library/os.html#os.fsync)、[Linux `fsync(2)`](https://man7.org/linux/man-pages/man2/fsync.2.html) |
| Linux 匿名临时 inode | Linux `O_TMPFILE` 在目录所在文件系统创建无名 inode，写完后可用 `linkat` 命名；避免半成品路径暴露，但只在支持该扩展的文件系统可用。[Linux `open(2)`, `O_TMPFILE`](https://man7.org/linux/man-pages/man2/open.2.html#O_TMPFILE)、[Python `TemporaryFile`](https://docs.python.org/3/library/tempfile.html#tempfile.TemporaryFile) |
| NFS 边界 | Linux `open(2)` 警告：不支持 `O_EXCL` 的 NFS 环境，依赖它做锁会有 race；需要可移植锁时可在同文件系统创建唯一名并用 `link(2)` 抢占锁名。[Linux `open(2)`, `O_EXCL`](https://man7.org/linux/man-pages/man2/open.2.html#O_EXCL) |
| 进程/线程协调 | `multiprocessing.Lock` 可在进程或线程间阻塞同步；POSIX advisory `fcntl` 锁只约束合作进程。若多个 writer 共享一个追加或 read-modify-write 文件，必须约定锁、单次 `O_APPEND` 写入或改用每 writer 独立文件。[Python `multiprocessing.Lock`](https://docs.python.org/3/library/multiprocessing.html#multiprocessing.Lock)、[Linux record locking](https://man7.org/linux/man-pages/man2/fcntl_locking.2.html)、[POSIX `write`](https://pubs.opengroup.org/onlinepubs/9699919799/functions/write.html) |

## 当前 `tests/testkit/runner.py` 现状

- `run_pytest` 先创建输出目录，再以 `os.open(claim_path, O_CREAT | O_EXCL | O_WRONLY, 0o600)` 创建 `.testkit-run.claim`；捕获 `FileExistsError` 并报告“output directory is already claimed”。这一点已经把同一路径的合作进程竞争收敛为恰好一个赢家，当前没有需要修改的 claim 创建缺陷。
- claim 内容通过一次 `os.write` 写入 `stage=...` 后关闭 fd。内容很短且 fd 只属于赢家；若未来写入可变长度内容，应检查 `os.write` 返回值或循环写完，以免把部分写误当成完整 claim。
- 随后对 `pytest-testkit.json`、JUnit、stdout、stderr、`testkit-run.json` 使用 `path.exists()`/`is_symlink()` 预检。对遵守 claim 的 runner，这能阻止复用目录；但它不是对不合作 writer 的原子占位。其他进程若在预检后创建/替换同一路径，`stdout_path.open("w")` 或 stderr 的 `open("w")` 仍可能截断该文件。若这些路径要成为独立并发安全资源，应让创建本身带独占语义，或依赖已成功持有的目录 claim 作为明确的合作边界。
- `tests/testkit/evidence.py::write_json_atomic` 使用 `.{name}.{pid}.{uuid}.tmp` 临时名，写完后 `os.replace`。不同 writer 的临时名通常不碰撞，读者不会看到半个 JSON；但 `os.replace` 会覆盖既有目标，竞争 writer 之间没有“只允许一个版本”的判据。临时名使用普通 `Path.write_text`，若面对不受信任目录/攻击者，`tempfile.mkstemp`（或 Linux `O_TMPFILE`）能把命名与创建合并，避免名字抢占。
- `tests/test_testkit_runner.py::test_runner_claims_output_before_two_concurrent_runs_can_start` 已用同进程两个线程竞争同一输出目录，并断言一个 `FileExistsError` 及 claim 存在；它验证了当前 `O_EXCL` 路径，但线程并不能覆盖独立进程/不同解释器的调度与文件系统行为。`tests/test_testkit_s1.py::test_atomic_evidence_writes_are_collision_free_under_same_process_concurrency` 验证两个唯一临时文件可并发替换且最终 JSON 存在，但只证明可见性/无临时残留，不证明 writer 顺序或跨进程锁。

## 确定性回归建议

1. **跨进程独占 claim。** 使用 `multiprocessing`（固定进程数，例如 8）和共享 barrier，同时对同一路径执行 `open(path, "x")` 或 `os.open(O_CREAT|O_EXCL)`；断言恰好一个成功、其余全部 `FileExistsError`/`EEXIST`，最终内容是一个完整 claim。不要在被测创建前调用 `exists()`，否则测试无法证明原子性。
2. **TOCTOU 反例。** 在临时测试 helper 中故意把 `exists()` 与 `open("w")` 用 barrier 分开，让竞争进程先创建目标；旧实现应出现覆盖/多成功。修复契约应改为 `"x"`/`O_EXCL`，断言 loser 得到可识别冲突并保留 winner 的原始字节。
3. **跨进程原子替换可见性。** 每个进程用 `mkstemp(dir=target.parent)` 创建独立临时文件，写入带 writer id 的完整 JSON，关闭并 `os.replace` 到同一目标；读者循环读取并解析 JSON，断言从不读到空文件、截断 JSON 或半条记录，最终值必须等于某一个完整 writer。该测试验证可见性；不应把最终 writer 顺序当作稳定契约。
4. **目标已存在时的 no-overwrite 需求。** 若 claim/receipt 要求“首个成功者唯一”，单独测试目标已存在时 `open("x")`/`O_EXCL` 失败且原字节不变；不要用 `os.replace` 代替该断言，因为 replace 的设计就是覆盖。
5. **共享追加文件。** 固定 N 个进程各写 M 条记录；若契约要求每条记录不交错，可用一次 `os.write` 配合 `O_APPEND` 并验证总行数与每行完整。若是多次写或 read-modify-write，则用 `multiprocessing.Lock`/合作的 `fcntl` 锁，并在测试中断言无丢失更新。跨文件系统/NFS 场景应作为显式平台 tier，不把本地 ext4 结果外推。
6. **崩溃持久性（按需）。** 若证据必须在断电后保留，写完临时文件后 `flush`/`os.fsync(file_fd)`，`os.replace` 后对父目录 fd `fsync`；测试应在支持的 Linux 文件系统 tier 中验证重启/故障注入语义。普通并发回归无需把“进程退出后可读”误称为断电保证。

## 判定

当前 runner 已正确使用 `O_CREAT|O_EXCL` 建立目录级 claim；基于现有测试与代码审查，没有需要立即修改的源码或测试。后续若要扩大安全边界，优先把“合作 runner 由 claim 串行化”写入契约，并增加独立进程测试；只有在允许不合作 writer 或要求崩溃持久性时，才分别引入独占打开/锁和 fsync 目录语义。
