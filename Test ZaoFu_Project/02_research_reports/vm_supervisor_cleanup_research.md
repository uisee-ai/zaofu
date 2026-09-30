# K571-F3：隔离 VM owned-process supervisor 清理方法调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/isolated_vm_supervisor.py`。supervisor 及 test_isolated_vm_supervisor.py 已存在；owned child 清理不外推任意后代/cgroup 或 OS 断电保证。

> 日期：2026-09-25  
> 范围：只读核对 Python 3.11 官方文档/源码、Linux `PR_SET_PDEATHSIG(2const)` 手册和当前
> `tests/testkit/isolated_vm_supervisor.py` 草案；未运行 smoke、未启动 QEMU、未安装或下载任何内容。
> 目标仅是清理测试直接拥有的 QEMU，并保留失败证据；不是生产通用 supervisor 设计。

## 1. 结论

当前草案的“handler 抛异常 + `except`/`finally` 清理”可以覆盖**已进入主循环后收到的
SIGTERM/SIGINT**，但不能覆盖 SIGKILL，也不能覆盖 handler 在 `Popen`/报告写入的启动窗口
抛出异常。对单一、禁止 daemonize/spawn 的 QEMU，最小可信方向是：

1. 启动前先创建并锁定私有证据目录、写入 command digest/nonce；把 `Popen` 直接句柄纳入
   `try/finally` 的生命周期。
2. Python handler 只记录信号/设置中断标志，不在 handler 里调用清理或写复杂报告；主线程
   在可控点退出并由一个幂等 cleanup 状态机处理。
3. 清理只对 `Popen` 直接拥有的 QEMU 做 `poll → terminate(SIGTERM) → wait → kill(SIGKILL)
   → wait`；本场景不要调用共享的 `terminate_process_group`，因为其基于旧 PGID 的
   `killpg` 在 leader 已退出、PGID 被复用时可能作用于无关进程。
4. 可选地由极小 exec wrapper 在 `execve` QEMU 前设置 Linux `PR_SET_PDEATHSIG=SIGKILL`，
   并在设置前后检查预期父 PID；父测试进程 SIGKILL 时 QEMU 会被内核连带终止。但这只
   防止孤儿 QEMU，不会生成最终报告；缺失 final sidecar 必须按失败处理。

这条路线能把“owned QEMU 已停止”和“测试父进程突然死亡”分开留证，不能把 SIGTERM
清理证明扩大为 host power loss、guest panic 或物理耐久性证据。

## 2. 官方信号事实

### 2.1 Python handler 的执行与异常

Python 官方 `signal` 文档说明：Python signal handler 总是在主解释器的主线程执行，
即使信号由其他线程收到；只有主线程可安装 handler。纯 C 的长时间计算可能不响应
到 Python handler，handler 会等控制回到解释器后才执行。如果 handler 抛异常，异常会
在主线程“out of thin air”被抛出。[P1]

这解释了草案中的：

```python
def interrupt(signum, _frame):
    raise SupervisorInterrupted(...)
```

在 `while`/`sleep` 中通常会进入 `except`，但它不是异步安全清理机制：异常可以在任意
Python 字节码边界打断 `start()` 或 `_write_report()`，而不是只在主循环条件处发生。
第二次信号还可能在清理过程中再次进入 handler；cleanup 必须显式幂等/防再入。

`SIGKILL`、`SIGSTOP` 无法被捕获、阻塞或忽略。[P1][L2] 因此 `finally` 只能覆盖
正常返回、可捕获异常和可捕获 SIGTERM/SIGINT，不能承诺父测试被 SIGKILL 后写出 final
report；后者只能靠 child-side death signal、启动前 sidecar 和后续受控恢复审计。

### 2.2 启动窗口

草案的 `supervisor.start()` 位于 `try` 之前。其内部先 `Popen(...)`，再给
`self.process` 赋值并写 started report。若 SIGTERM 在子进程已创建、但赋值或报告写入
前到达，handler 抛出的异常会跳过外层 `try/finally`；父侧没有稳定的 `Popen` 句柄可以
清理。即使 `Popen` 自身报告错误，必须区分“创建失败无 child”和“child 已启动后
父侧初始化失败”，不能按一个路径处理。

最小边界：在进入可能被信号打断的启动段前写 `starting` sidecar；把 Popen 创建和
`try/finally` 放在同一生命周期；Popen 返回后立即记录 PID、进程 starttime/boot
identity、command digest 和私有 log 路径；报告写失败时仍走句柄清理。不要在 handler
里直接调用 `terminate_process_group`、`os.replace` 或递归清理。

## 3. Popen 句柄、PID/PGID 与竞态

Python 3.11 `subprocess.Popen.send_signal()` 官方源码先调用 `poll()`，并使用内部
`_waitpid_lock`，以降低“子进程已退出而 PID 被回收复用”的风险；源码也明确承认在
`returncode` 检查与最终 `os.kill()` 之间仍有竞态，并吞掉 `ProcessLookupError`。[P2]
`Popen.terminate()` 发送 SIGTERM，`Popen.kill()` 发送 SIGKILL；POSIX `returncode=-N`
表示被信号 N 终止。[P3]

当前 `terminate_process_group()` 的设计是给 `start_new_session=True` 的测试进程组
做 descendants 清理：它用 `os.killpg(process.pid, ...)`，并且刻意不因 leader
`poll()` 已有结果而提前返回。这对含 descendant 的通用测试有意义，但对本任务的
单一 QEMU 不必要且存在两个边界：

- leader 已退出后仍以旧数字 PID 作为 PGID；若该 PGID 在观察间隔内被无关进程组复用，
  `killpg` 可能越界影响别的进程。
- “PGID 不存在”不等于 QEMU 的句柄已回收；反之 leader 已退出但 descendants 仍在，
  也不能只看 `Popen.poll()` 判断整个资源是否消失。

因此 K571 只允许 QEMU 无 daemonize、无自建后台进程、无外部 helper 的启动形状，清理
优先使用同一 `Popen` 句柄和 `wait()`。若以后必须覆盖 QEMU descendants，需额外证明
每个 descendant 的 starttime/owned nonce，不能把共享 helper 无条件复用。

`start_new_session=True` 会在 child `exec` 前调用 `setsid()`；Python 官方建议用
`start_new_session`/`process_group` 代替多线程环境不安全的 `preexec_fn`。[P3] 本任务
不需要 `preexec_fn` 来做组清理。当前解释器没有 `os.pidfd_open`/`signal.pidfd_send_signal`，
所以不能声称 pidfd 已消除竞态；必须保守记录句柄、starttime 和有限竞态窗口。

报告里的 `Path(f"/proc/{pid}").exists()` 也不能单独证明“owned PID gone”：PID 可能
在检查前被复用。应以 `Popen.poll()/wait()` 的返回码为主要证据，把 proc starttime
只作为发送前的身份交叉检查；若身份不匹配，禁止再发信号并 fail-closed。

## 4. `PR_SET_PDEATHSIG`：用途与硬边界

Linux man-pages 定义 `PR_SET_PDEATHSIG`：设置调用进程在其**父线程**终止时收到的信号；
如果父线程/祖先 subreaper 已经终止到设置发生之前，则不会补发；该设置在 child 的
`fork()` 后被清除，但通常跨 `execve()` 保留；执行 set-user-ID/set-group-ID/带 file
capability binary 或改变相关 credentials 时也可能清除。[L1]

因此可选的安全形状是一个很小、已审计的 child-side exec wrapper：

```text
fork/exec wrapper
  expected_parent = 精确supervisor PID（在spawn前由父进程记录并传入）
  if getppid() != expected_parent: exit (父已死，不启动QEMU)
  prctl(PR_SET_PDEATHSIG, SIGKILL)
  if getppid() != expected_parent: self-kill/exit (父死亡竞态)
  execve(qemu-system-x86_64, exact_argv, exact_env)
```

要点和限制：

- 必须在 `execve` 前设置，并在设置后再次检查 `getppid()`；否则 parent 在两步之间死掉
  会留下没有 death signal 的 QEMU。
- `expected_parent` 不能在 wrapper 启动后直接用当前 `getppid()` 现算：父可能已经死掉并
  重归属给 PID 1/subreaper，这会错误地把新父当预期 supervisor。应使用 spawn 前父侧
  固定的 PID，配合已知单线程创建路径；如需更强身份用已有 nonce/starttime交叉核对。
- wrapper 应直接 `execve` QEMU，不 fork daemon；QEMU 自己再 fork 的 child 不会自动
  继承该设置。禁止 `-daemonize` 及未知 provider/helper。
- signal 只杀 child，不写 parent 侧 final report、不关闭/归档 QMP/serial、不保证
  overlay 元数据完成。父侧 recovery 必须看到缺失 final report 并判为 interrupted/
  non-green，再以存活的 process/overlay identity 受控接管。
- parent 的“死亡”按 Linux 语义是创建该 child 的父**线程**死亡；多线程 supervisor
  若只杀一个线程，可能触发 child death，而进程仍在运行。故 supervisor 只让一个明确
  主线程创建 wrapper，且不要把 PDEATHSIG 当进程级 lease。
- 这是 Linux-specific 防孤儿措施，不替代 Popen 的直接句柄清理、QMP close 或证据
  sidecar，也不证明宿主断电。

本轮不实现 wrapper；若以后启用，必须单独记录 wrapper binary/source SHA、expected
parent 前后值、exec 成功标记，并加“父 SIGKILL → QEMU 消失但 final report 缺失”的
负向验收。

## 5. 对当前草案的最小修改建议（仅建议，不是本轮代码变更）

### 5.1 Handler 与 cleanup 状态

把 handler 从“抛自定义异常”改为只写一个轻量状态（例如 signal number + monotonic
timestamp）；主线程循环在安全点把状态转为 `SupervisorInterrupted`，再进入唯一
cleanup。仅把 `start()` 移进 `try/finally` 仍不能完全解决“Popen 已生成 child、赋值前
raise”的窗口，故这里不推荐继续使用 raise 型 handler。让 cleanup 使用
`_cleaning/_cleaned` 两态，先占位再执行；二次进入不重复发信号，并且不能把尚在
`_cleaning` 的状态伪装成已有 cleaned 报告。只在完成 wait/留证后进入 `_cleaned`。

不要在 handler 中执行 Python 文件 I/O、`wait()`、`killpg`、`os.replace` 或依赖锁的
逻辑。这里不是声称 Python handler 完全不能做任何事，而是本测试只需记录标志，复杂
副作用延迟到主线程。

### 5.2 单一 owned QEMU 清理

固定启动约束：`start_new_session=True` 仅作为进程隔离，不调用共享
`terminate_process_group`；QEMU 参数禁止 daemonize/spawn。清理流程：

1. 读取 `Popen.poll()`；已结束则 `wait()` 获取 returncode，不再发信号。
2. 仍存活且 starttime/command identity 匹配时调用 `process.terminate()`，在固定短
   deadline 内 `wait()`。
3. 超时调用 `process.kill()`，再 `wait()`；记录每次动作、返回码和 elapsed。
4. 关闭父侧 QMP/serial 文件句柄；确认 socket 不再监听；原始 log/sidecar 保留。
5. `cleanup_ok` 必须由 `returncode`、owned identity、文件完整性共同决定，不能只看
   `/proc` 路径不存在。

子进程在启动后必须立刻写入 pid/starttime/command digest；在 Popen 赋值或 sidecar
   写入失败时，如果没有可信句柄/identity，判为 harness failure，不盲杀数字 PID。

### 5.3 证据与 `finally`

`finally` 的职责是“尽力清理且保留现场”，不是把任何异常变绿。报告至少区分：
`started`、`interrupted_sigterm`、`qemu_sigkill`、`normal_exit`、`startup_failed`、
`cleanup_failed`、`parent_died_no_final_report`。父进程收到 SIGKILL 后没有机会写
`parent_died_no_final_report`；恢复脚本只能依据存在的 `starting/started` sidecar、
PDEATHSIG/进程退出和缺失 final marker 推断 interrupted，并必须 fail-closed。

## 6. 最小 smoke 验收（未来才可执行）

本调研不运行 smoke。若用户后续批准当前测试自身的有限验证，应只使用 `/bin/sleep`
或等价无副作用 child，不启动 QEMU：

- SIGTERM 在主循环：收到 signal → 主线程标记 → finally 清理 → child returncode 可读，
  log/report 原子完整。
- SIGTERM 在启动/started-report 边界：要么拿到句柄并清理，要么明确
  `startup_failed`，不能留下未知 owned child。
- 只允许同一主线程调用该 Popen 的 poll/wait/send_signal，不让其他线程或
  `os.waitpid` 绕过对象回收 child；这配合不raise的handler缩小CPython所述竞态面。
- 第二个 SIGTERM/cleanup 重入：只生成一个清理动作序列和一个最终报告。
- SIGKILL 父 probe：若无 PDEATHSIG，明确证明 child 可能存活并列为失败；若未来启用
  wrapper，证明 child 消失但 final marker 缺失仍为 non-green。
- 模拟 PID/PGID 身份不匹配：不发送信号，保留证据并 fail-closed。

这些 smoke 只验证监督器的状态/清理合同，不能宣称 QEMU、guest panic、同 overlay
接管或 OS crash 已完成。

## 7. 失败停止条件

遇到以下任一项停止，不扩大为通用 supervisor：无法证明 QEMU 是当前 Popen 直接拥有；
Popen 句柄已失效但仍想按 PID/PGID 猜测；handler/cleanup 出现二次进入；父死后只有
“目录清空”而无 final/non-green 证据；QEMU daemonize/生成未知 descendants；需要
sudo、共享宿主目录可写挂载、bridge、全局 signal 或 killall；pid/starttime/command
digest 不一致；退出/清理超时；任何状态被直接写入 ZaoFu canonical ledger。保留现场，
不修生产代码，不把此类失败写成 ZaoFu 产品 bug，除非后续测试证明是产品路径本身。

## 8. 一手来源

- [P1] Python 3.11 `signal` 文档（handler 主线程、异常、SIGKILL、长 C 调用）：
  <https://docs.python.org/3.11/library/signal.html>
- [P2] CPython 3.11 `subprocess.py` `Popen.send_signal`（poll、waitpid lock、PID
  reuse race）：
  <https://github.com/python/cpython/blob/v3.11.15/Lib/subprocess.py#L2176-L2206>
- [P3] Python 3.11 `subprocess` 文档（Popen、`start_new_session`、`process_group`、
  `terminate`/`kill`/`wait`）：
  <https://docs.python.org/3.11/library/subprocess.html>
- [L1] Linux man-pages `PR_SET_PDEATHSIG(2const)`（父线程、fork/exec、credentials、
  已死亡竞态）：
  <https://man7.org/linux/man-pages/man2/PR_SET_PDEATHSIG.2const.html>
- [L2] Linux `signal(2)`（SIGKILL/SIGSTOP 不可捕获，signal handler 语义警告）：
  <https://man7.org/linux/man-pages/man2/signal.2.html>
- [R1] 当前候选实现（只读核对）：
  `tests/testkit/isolated_vm_supervisor.py`
- [R2] 当前共享清理实现（只读核对）：`tests/testkit/cleanup.py`
