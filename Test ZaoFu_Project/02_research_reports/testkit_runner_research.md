# TestKit 独立 pytest runner 调研（2026-09-09）

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/pytest_plugin.py`。已有 TestReport 分相采集及 runner 分类；不能只按 JUnit skip 或退出码判断已知缺陷通过。

本调研只使用 pytest、pytest-cov 和 Python 标准库的一手资料，目标是为 ZaoFu TestKit 设计一个独立进程 runner。它不替代合并门禁的策略实现；它定义 runner 能可靠观察的事实、建议的薄实现，以及明确的边界。

## 结论

建议每次运行由 `sys.executable -m pytest` 启动一个全新进程，并显式追加 `--no-cov --junitxml=<绝对路径>`。参数使用 argv 数组、`shell=False`、明确 `cwd` 和受控环境；保存原始命令、cwd、stdout、stderr、返回码、超时信息、JUnit 文件和一个由 pytest plugin 生成的结构化 JSON。`pyproject.toml` 的 `addopts` 可以启用覆盖率，但 pytest-cov 官方提供的 `--no-cov` 会完全禁用 coverage，因此这个显式参数必须保留。

JUnit XML 只作互操作和诊断证据，不能作为七类状态的唯一来源。pytest 的 JUnit writer 将预期失败（XFAIL）写成 `<skipped type="pytest.xfail">`，非 strict XPASS 走普通 passed 路径；两者在 XML 中都不能安全地表达 TestKit 所需的独立 `xfail`/`xpass` 语义。一个极薄的 plugin 应通过公开报告 hook 记录每个 nodeid 的 setup、call、teardown 报告，并读取公开的 `TestReport.wasxfail` 属性；随后由 runner 根据事实归类并另行计算 gate 结果。

对 ZaoFu 合并门禁，`passed` 是唯一可计入绿色的测试状态。`skipped`、`xfail`、`xpass` 和 `not_run` 都必须令 `gate_exit` 非零；普通 `failed`、setup/teardown `error`、collection/internal/usage 错误及超时也必须非零。尤其是非 strict XPASS 的 pytest 进程返回码可能仍为 0，绝不能用进程返回码或 JUnit 的 `testsuite failures/errors` 单独放行。

## pytest 事实与状态映射

### 进程退出码

pytest 9.0.3 的公开 `ExitCode` 是：0（收集到的测试全部通过）、1（有失败）、2（用户中断）、3（内部错误）、4（命令行用法错误）、5（没有收集到测试）。这是会话级事实，不是每个测试的结果；特别是含非 strict XPASS/skip/xfail 的会话可能为 0。未来 pytest 版本可能增加退出码，因此未知非零应 fail-closed 地记作 runner/session error，而不是猜测其含义。

来源：[pytest 9.0.x Exit codes](https://docs.pytest.org/en/9.0.x/reference/exit-codes.html)、[pytest 9.0.3 `_pytest/skipping.py`](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py)。

### XFAIL、XPASS 与 strict XPASS

pytest 9.0.3 的 skipping 实现把预期失败异常或匹配的预期失败转换为 `report.outcome = "skipped"` 并设置 `report.wasxfail`；非 strict 的预期测试成功时设置 `wasxfail`、保留 passed；strict XPASS 则设置 failed 并把 longrepr 前缀设为 `[XPASS(strict)] ...`。终端状态 hook 用 `wasxfail` 区分 xfailed（`x`）和 xpassed（`X`）。因此 plugin 应至少采用以下优先级：

* `report.when == "call"` 且 `report.skipped` 且存在 `wasxfail`：`xfail`；
* `report.when == "call"` 且 `report.passed` 且存在 `wasxfail`：`xpass`；
* `report.when == "call"` 且 `report.failed` 且 `longrepr` 是官方 `[XPASS(strict)] ` 前缀：仍归 `xpass`，同时记录 `strict: true` 并令门禁失败；
* 其余 call failed：`failed`。

最后一条兼容识别只针对 pytest 官方当前精确前缀；若将来前缀改变，应保留原始报告并把它标为 `failed`/`unclassified`，不能默认为通过。不要通过私有 `item.stash` 推断 xfail；优先使用 `TestReport.wasxfail` 和报告公开属性。

来源：[pytest skipping 源码 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py)、[pytest skip/xfail 指南](https://docs.pytest.org/en/stable/how-to/skipping.html)、[TestReport API](https://docs.pytest.org/en/stable/reference/reference.html#pytest.TestReport)。

### failed 与 error

`pytest_runtest_logreport(report)` 会分别收到 setup、call、teardown 三个阶段的 `TestReport`。call 阶段异常是 `failed`；setup 或 teardown 阶段失败应单独归为 `error`，否则只看会话失败总数会丢掉错误位置。collection report 通过 `pytest_collectreport` 提供；collection failure 是 session error，不能伪装成某个测试的 call failure。

来源：[pytest runtest hooks API](https://docs.pytest.org/en/stable/reference/reference.html#pytest_runtest_logreport)、[pytest runtest protocol](https://docs.pytest.org/en/stable/reference/reference.html#pytest_runtest_protocol)、[pytest JUnit writer 源码](https://docs.pytest.org/en/stable/_modules/_pytest/junitxml.html)。

### skipped 与 not_run

普通 skip 是没有 `wasxfail` 的 skipped report；JUnit writer 会写 `<skipped type="pytest.skip">`。`not_run` 不是 pytest 的 TestReport outcome：plugin 在 `pytest_collection_finish(session)` 保存最终收集到的 nodeid 集合，在 `pytest_runtest_logreport` 记录每个 nodeid 已出现的阶段，session 结束时将没有任何终态 call/skip/xfailed/xpassed/error 报告的已选 nodeid 标成 `not_run`。这覆盖 `pytest` 中断、`-x`/`--maxfail` 或进程被超时终止后的剩余项目。

被用户参数主动 deselect 的项目不应冒充执行失败；如需审计，plugin 可在 `pytest_deselected(items)` 单独记录 `deselected` 数量和 nodeid，但它不计入本次 selected 测试总数。collection 失败时无法知道未收集项目的 nodeid，只记录 collection error 和 session 状态。

来源：[collection hooks API](https://docs.pytest.org/en/stable/reference/reference.html#collection-hooks)、[pytest hook `pytest_collection_finish`](https://docs.pytest.org/en/stable/reference/reference.html#pytest_collection_finish)、[pytest output/JUnit 文档](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files)。

## 最薄 plugin / runner 形状

plugin 只需实现 `pytest_collection_finish`、`pytest_deselected`、`pytest_collectreport`、`pytest_runtest_logreport` 和 `pytest_sessionfinish`（必要时实现 `pytest_internalerror`）。每个 nodeid 记录：原始阶段报告摘要、最终分类、`wasxfail`/strict、duration、longrepr 摘要、捕获的 stdout/stderr 引用。不要改写 pytest 的 outcome，也不要在 plugin 内决定 ZaoFu gate。

runner 负责：

1. 在启动前获取 `git rev-parse --show-toplevel`、`git rev-parse HEAD`、`git status --porcelain=v1 --branch`，并把 worktree 路径、commit、分支（若可得）写入证据；这些命令只读且使用 argv。
2. 生成唯一、绝对路径的 JUnit 和 JSON 输出路径，命令形如 `(<sys.executable>, "-m", "pytest", *user_args, "--no-cov", "--junitxml=<path>", "-p", "<testkit_plugin>")`。应在证据中同时保存最终 argv，而非只存展示字符串。
3. 使用 `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True, cwd=..., env=..., shell=False, start_new_session=True)`；`sys.executable` 是 Python 官方推荐的重新启动当前解释器方式。正常完成后 `communicate()` 得到完整 stdout/stderr 和返回码。
4. 超时捕获 `TimeoutExpired` 后记录已收集的部分 stdout/stderr，向进程组发送 SIGTERM，短暂等待，再发送 SIGKILL，最后 `communicate()`/`wait()` 回收；设置 `start_new_session=True` 后 POSIX 子进程拥有独立 session，可用 `os.killpg(proc.pid, signal)` 清理其子孙。Windows 需要等价的 process-group/job-object 实现，不能假设 `killpg` 存在。
5. 解析 plugin JSON（若缺失、损坏或 runner 本身异常，gate fail-closed），合并进程退出码、每项分类和 timeout，写一个结构化结果。至少包含 `schema_version`、`command`（argv）、`cwd`、`worktree`、`commit`、`branch`、`started_at`/`finished_at`、`returncode`、`timed_out`、`stdout`、`stderr`、`junit_xml`、`tests`（每项含 nodeid/status/phase/evidence）和 `counts`。

来源：[Python `subprocess.Popen`](https://docs.python.org/3/library/subprocess.html#subprocess.Popen)、[Python `subprocess.run` timeout 语义](https://docs.python.org/3/library/subprocess.html#subprocess.run)、[Python `os.killpg`](https://docs.python.org/3.11/library/os.html#os.killpg)、[pytest hook 编写指南](https://docs.pytest.org/en/stable/how-to/writing_hook_functions.html)。

## JUnit 使用边界

使用 `--junitxml=<path>` 可生成供 CI/Jenkins 消费的 XML；pytest 默认将 setup/teardown 错误放进 `<error>`、call 失败放进 `<failure>`，普通 skip 和 xfail 都放进 `<skipped>`。JUnit 的 testsuite `tests/failures/errors/skipped` 是聚合计数，且非 strict XPASS 由 pytest writer 的 `append_pass` 处理，不能恢复 xpass 事实。不要通过修改 XML 把 xfail/xpass 重新解释为 passed；结构化 plugin JSON 才是 TestKit 的分类来源，XML 保持原样以便互操作和诊断。

来源：[pytest JUnit XML 输出文档](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files)、[pytest 9.0.3 JUnit writer 源码](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/junitxml.py)。

## 建议的 gate 规则与验证边界

定义 `gate_exit = 0` 当且仅当：runner 没超时、plugin JSON 完整、pytest 返回码为 0、至少收集并执行了一个 selected 测试（若 TestKit 明确允许空集则单独配置）、且所有 selected 项均为 `passed`。任何 `failed/error/skipped/xfail/xpass/not_run`、collection error、未知退出码、JSON/JUnit 缺失或超时均令 `gate_exit = 1`；runner 自身无法启动或结果不可解析可用 `gate_exit = 2` 表示 infrastructure error。`xfail` 不能因为“预期失败”而放行，`xpass` 不能因为“测试成功”而放行，strict XPASS 必须作为 xpass 证据保留。

最小回归夹具应在独立临时目录覆盖：普通 pass、assert failure、setup error、teardown error、skip、xfail、非 strict xpass、strict xpass、收集空集、`-x` 造成 not_run、以及超时且带子进程的测试。验证每项 JSON 分类、JUnit 原始节点、stdout/stderr、commit/worktree identity、超时后的进程组无残留；不需要启动 ZaoFu production runtime。

## 已知限制

pytest hook API 传递的是阶段报告，不承诺提供“最终每项单一结果”字段；plugin 必须按 nodeid 聚合并定义 setup/call/teardown 冲突优先级（建议 error > failed > xpass/xfail/skip > passed）。插件、xdist、第三方 reporter 可能改变时序或输出，故应锁定受支持 pytest 版本并把原始报告保留。进程级 timeout 不能证明测试内部已安全释放资源；只能通过独立 session/process group 的终止与最终 wait 降低泄漏风险。JUnit XML 本身不保证覆盖 TestKit 的 `not_run`，未执行项目只能来自 plugin 的收集集合与报告差集。
