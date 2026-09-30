# subprocess 超时与 POSIX 进程组清理调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/cleanup.py`。当前 terminate_process_group 已去除组长退出即早退并按进程组存活升级 SIGKILL；历史风险不再直接描述当前函数。

> 调研范围：Python 官方文档、POSIX 规范、Linux man-pages（2026-09-12）。本文只记录证据、边界和测试建议，不修改实现。

## 结论

`Popen.communicate(timeout=...)` 的超时只产生 `TimeoutExpired`，不会替调用者终止子进程。Python 的规定动作是捕获异常、终止进程、再次 `communicate()` 读取管道并填充 `returncode`；异常后应继续用 `communicate()`，不要改用 `wait()`。但是，这个示例隐含了一个前提：终止动作确实关闭了所有继承 stdout/stderr 写端。若孙进程仍持有写端，读端没有 EOF，第二次无期限 `communicate()` 仍可能等待。

进程组组长退出也不代表进程组消失。POSIX 将进程组生命周期定义为“直到最后一个成员离开”；组长只是 PID 等于 PGID 的成员。因此，必须把“直接子进程是否已退出”和“目标 PGID 是否仍有成员/写端”作为两个独立状态检查。

## 官方语义与边界

### Python `Popen`

- `communicate()` 同时读取 stdout/stderr 直到 EOF，并等待子进程结束；使用 `PIPE` 时应优先使用它，直接 `wait()` 可能因管道缓冲区填满而死锁。[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate)
- 超时抛出 `TimeoutExpired`，子进程不会自动被杀死。官方示例是 `proc.kill()` 后再次 `proc.communicate()`；超时后的输出不会因重试而丢失，且明确要求不要在此场景调用 `wait()`。[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate)
- `Popen.terminate()` 在 POSIX 只向单个子进程发送 SIGTERM；`Popen.kill()` 在 POSIX 只向单个子进程发送 SIGKILL。二者都不会自动遍历孙进程。[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.terminate)
- `start_new_session=True` 使子进程在 exec 前调用 `setsid()`，可把该子树的初始 session/process group 与执行器隔离，之后才安全地按 PGID 发信号。[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen)
- `Popen.wait(timeout=...)` 只等待该直接子进程；超时会抛出 `TimeoutExpired`。在 POSIX 上带 timeout 的实现是非阻塞轮询加短暂 sleep。[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.wait)

### POSIX/Linux 进程组、信号与等待

- POSIX 定义进程组为共享 PGID 的进程集合；新进程继承创建者的进程组。进程组生命周期到最后一个成员离开才结束，组长定义只是“PID 等于 PGID 的进程”。[POSIX definitions 3.280–3.283](https://pubs.opengroup.org/onlinepubs/9799919799/basedefs/V1_chap03.html)
- Linux `setpgid()` 可创建或加入同一 session 内的进程组；子进程经 `fork()` 继承 PGID，PGID 跨 `execve()` 保持。[setpgid(2)](https://man7.org/linux/man-pages/man2/setpgid.2.html)
- `killpg(pgid, sig)` 向整个进程组发送信号；Linux `kill(-pgid, sig)` 具有同样的负 PID 进程组语义。返回成功仅表示至少向一个有权限的成员送达，不保证每个成员都已退出。[killpg(2)](https://man7.org/linux/man-pages/man2/killpg.2.html)、[kill(2)](https://man7.org/linux/man-pages/man2/kill.2.html)
- `waitpid()`/`waitid(P_PGID, ...)` 只能回收调用者的直接子进程；`waitpid` 的负 pid 形式表示“等待该 PGID 中的任意子进程”，并不会替调用者回收非子孙进程。[wait(2)](https://man7.org/linux/man-pages/man2/waitpid.2.html)、[POSIX wait](https://pubs.opengroup.org/onlinepubs/9699919799/functions/wait.html)
- SIGTERM 可以被忽略或捕获；SIGKILL 不能被捕获、阻塞或忽略。因此应把 SIGTERM 视为有限宽限期的第一阶段，把 SIGKILL 作为有界清理的最终阶段。[signal(7)](https://man7.org/linux/man-pages/man7/signal.7.html)
- 管道读端只有在所有写端文件描述符都关闭后才读到 EOF；只要孙进程或逃逸进程组的成员保留写端，读取会阻塞等待数据或所有写端关闭。[pipe(7)](https://man7.org/linux/man-pages/man7/pipe.7.html)、[read(3p)](https://man7.org/linux/man-pages/man3/read.3p.html)
- POSIX `timeout` 工具的规范明确指出：用进程组发送信号时，改变自身 process group 的后代不会收到信号；要覆盖这类后代，需额外的后代追踪/回收机制。[POSIX timeout rationale](https://pubs.opengroup.org/onlinepubs/9799919799/utilities/timeout.html)

## 与当前 TestKit 能力的区别

以下是当前代码已经具备的能力，不是本调研提出的新保证：

- [`tests/testkit/command_runner.py:60-77`](/home/uisee/zaofu_v7/tests/testkit/command_runner.py) 在 POSIX 使用 `start_new_session=True`、捕获 stdout/stderr，并在首次 `communicate(timeout)` 超时后调用 `terminate_process_group`，随后进行一次无 timeout 的 `communicate()`。
- [`tests/testkit/cleanup.py:77-99`](/home/uisee/zaofu_v7/tests/testkit/cleanup.py) 已实现 SIGTERM→等待→SIGKILL 的进程组清理；但函数开头若 `process.poll()` 已非空就直接返回。若组长先退出而组成员仍活着，这个早退会跳过 `killpg`；调用方随后无界读取管道时存在卡住和泄漏风险。
- [`tests/testkit/runner.py:49-69,186-200`](/home/uisee/zaofu_v7/tests/testkit/runner.py) 也使用独立 session 和进程组信号，但其 stdout/stderr 重定向到普通文件，因此没有 `communicate()` 的管道 EOF 等待；同样不能由 `process.wait()` 的完成推断整个 PGID 已清空。
- 当前代码没有证据表明已追踪“脱离原 PGID 的后代”，也没有统一的二次 drain deadline、PGID 存活探针或逃逸后代的容器/cgroup 兜底。

## 建议（与现有能力分开）

1. **启动时建立可验证的边界。** POSIX 进程执行器使用 `start_new_session=True`，记录实际 PGID（不要只依赖组长仍存活）；禁止把不受信任命令放入执行器自身 PGID。
2. **超时后按有限状态机清理。** 先对记录的 PGID 发送 SIGTERM；以单独的 grace deadline 检查直接子进程和 PGID 成员。无论组长是否已经退出，都继续探测/处理 PGID，而不是以 `poll()!=None` 作为整个组完成的条件。
3. **升级并限制收尾。** grace 到期或成员仍在时，对同一 PGID 发送 SIGKILL；随后用带明确 drain deadline 的 `communicate(timeout=...)` 排空输出。第二次仍超时时，记录“管道未闭合/可能有逃逸持有者”，关闭父端流并结束本次执行，绝不调用无期限 `communicate()`。输出应允许标记为截断，以免把清理失败伪装成普通测试失败。
4. **区分回收对象。** `wait()`/`waitpid()` 只回收直接子进程；PGID 存活检查用于发现同组成员；二者都通过后才能报告“本次执行器无已知组内泄漏”。`killpg` 返回 ESRCH 只能说明当时没有可寻址组成员，不是对曾经逃逸后代的证明。
5. **处理逃逸进程组。** POSIX 进程组信号无法覆盖调用 `setpgid()`/`setsid()` 离组的后代。Linux 托管环境可评估 cgroup/systemd scope；需要回收孤儿时可由 Linux `PR_SET_CHILD_SUBREAPER` 让孤儿重定向到执行器并由其 `wait()`，但 subreaper 本身不会自动杀死后代，仍需按 PID/生命周期实施有界终止。[PR_SET_CHILD_SUBREAPER(2const)](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)
6. **信号策略可观测。** 记录每一步的 PGID、`killpg` 错误、SIGTERM grace 是否耗尽、SIGKILL 是否发送、drain 是否超时及输出是否截断；这些字段应进入执行收据，便于区分“测试失败”和“执行器清理失败”。

## 建议的确定性测试（仅建议，当前未实现）

- **组长先退出、孙进程持有 stdout：** 独立 session 中启动一个 Python 组长，先启动孙进程继承 stdout 写端，再让组长立即退出、孙进程睡眠。断言清理入口即使 `poll()` 已非空仍向 PGID 发信号；`run_command` 在固定总 deadline 内返回，且不会留下存活孙进程或永久阻塞。
- **SIGTERM 被忽略：** 组长与孙进程都安装 `SIGTERM=SIG_IGN`。断言 SIGTERM grace 到期后发送 PGID 的 SIGKILL，并在有限时间内回收直接子进程；检查 `/proc` 或等价探针确认组内成员终止（允许短暂 zombie，最终由父进程 wait 回收）。
- **逃逸 PGID 且持有管道：** 孙进程调用 `setsid()` 后继续持有 stdout 写端。断言原 PGID 的 SIGKILL 不会被误判为全树清理；执行器在 drain deadline 到期时返回带“管道未闭合/逃逸”诊断，而不是无限等待。测试结束时由隔离环境显式终止该逃逸进程，避免污染测试运行器。
- **正常大输出：** 子进程向 stdout/stderr 写入超过典型 pipe buffer 的数据后退出。断言使用 `communicate()` 不死锁、完整收集输出；这覆盖直接 `wait()` 的已知风险。
- **信号发送竞态：** 子进程在清理期间自行退出。将 `killpg` 的 ESRCH/`ProcessLookupError` 视为幂等结果，同时仍执行有界 drain 和直接子进程回收，不能因竞态提前跳过收尾。
- **平台门控：** 进程组测试标记 POSIX/Linux；Windows 路径单独验证其 terminate/kill 或 job-object 契约，不把 `killpg` 语义带入跨平台断言。
