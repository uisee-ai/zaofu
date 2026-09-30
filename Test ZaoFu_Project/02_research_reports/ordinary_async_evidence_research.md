# 普通 CaseEvidence 的 asyncio function fixture 兼容性调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/test_async_case_evidence.py`。已有异步证据合同；fixture 生命周期与原始事件/状态证据按实际注册范围判断。

> 调研日期：2026-09-14。读取 HEAD：`142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`。本报告通过本地源码和第一方文档推导测试 oracle；没有执行测试、修改实现、启动 runtime / Provider 或进行远端写入。预期行为尚需实施测试验证。

## 结论与版本边界

当前 `CaseEvidence` 已有同步注册、阶段 checkpoint、`BaseException` owner guard 和全阶段成功后丢弃逻辑。建议只增加一个显式选择的 async fixture 文件和相邻生命周期测试，直接经过现有 `run_pytest`；不增加 async evidence API、替换事件循环或扩展 runner。此前 [ordinary_case_evidence_research.md](ordinary_case_evidence_research.md) 对“尚未存在 hook / 注册接口”的描述属于旧实现基线，不应继续用作当前缺口判断。

| 项目 | 实际核对结果 | 依据 |
| --- | --- | --- |
| Python | `.venv/bin/python` 为 CPython 3.11.15，Linux | 只读 `sys.version`；标准库 `asyncio/runners.py` |
| pytest | 锁定、安装均为 9.0.3 | `uv.lock:1068`；安装 metadata 和 `.venv/lib/python3.11/site-packages/_pytest/` |
| pytest-asyncio | 锁定、安装均为 1.3.0 | `uv.lock:1084`；安装 metadata 和 `.venv/lib/python3.11/site-packages/pytest_asyncio/plugin.py` |
| 项目默认 | `asyncio_mode = "auto"` | [pyproject.toml](../../../../pyproject.toml)，第 43–47 行 |
| 声明依赖范围 | pytest >=9.0、pytest-asyncio >=0.24 | `pyproject.toml:16`；本轮验证不能覆盖整个声明范围 |
| 在线文档版本 | pytest-asyncio stable 已为 1.4.0；Python 3.11 文档当前为 3.11.16 | 本报告优先引用 pytest-asyncio v1.3.0 和本地 3.11.15 源码；不把 stable 当作已安装版本 |

strict 模式需要 `@pytest.mark.asyncio` 和 `@pytest_asyncio.fixture`；auto 还会接管普通 `@pytest.fixture` 的异步 fixture，并自动标记异步测试。fixture 缓存 scope 与 loop scope 是不同选项，本轮均明确限定为 function。[pytest-asyncio 1.3.0 discovery](https://pytest-asyncio.readthedocs.io/en/v1.3.0/concepts.html#test-discovery-modes)、[fixture decorator](https://pytest-asyncio.readthedocs.io/en/v1.3.0/reference/decorators/index.html)

## 当前接缝与生命周期事实

| 本地源文件 / 段落 | 对测试的直接影响 |
| --- | --- |
| `tests/testkit/pytest_plugin.py:23–42` | `testkit_case_evidence` 是 function 同步 fixture；async owner 显式依赖它即可，注册 root 必须是其 `tmp_path` 私有子目录 |
| 同文件 `45–68` | setup/call 报告后 checkpoint；整个 teardown 前 checkpoint；teardown 报告后才判断全阶段成功并丢弃 |
| 同文件 `pytest_runtest_logreport`、`_case_status` | 保存每阶段原始 outcome/longrepr；call failed 优先于 teardown error；仅单一 status 无法证明双阶段失败都被保留 |
| `tests/testkit/case_evidence.py:168–217` | 固定槽位 checkpoint，同槽覆盖；guard 捕获 `BaseException`，再原样抛出；普通 `with evidence.guard()` 可包住 `await` |
| 同文件 `223–238`、`241–305` | 成功只删除自身 digest 匹配的快照；父端校验 ref、identity、原始文件字节；capture errors 会使 runner 判 harness_failure |
| `tests/testkit/runner.py:117–145`、`147–end` | 真实子进程显式加载 evidence plugin；退出后验证快照并打包；当前 `run_pytest` 没有专门的 asyncio 参数 |
| `tests/testkit/test_case_evidence_lifecycle.py:19–76`、`100–128` | 可复用现有 run、manifest、base64、digest、清理后 root 不存在的断言形式；现有 fixture 是同步版本 |

pytest-asyncio 1.3.0 的 `_wrap_asyncgen_fixture`（本地 plugin 第 294–339 行）用 `Runner.run(setup())` 执行第一次 `__anext__()`，成功 yield 后才 `request.addfinalizer(finalizer)`；finalizer 再用 `Runner.run(async_finalizer())` 执行下一次 `__anext__()`。测试体经 `_synchronize_coroutine`（703–718 行）进入同类 runner。因而 setup/call/teardown 仍向 pytest 常规报告边界传播异常。[对应版本源码](https://github.com/pytest-dev/pytest-asyncio/blob/v1.3.0/pytest_asyncio/plugin.py#L294)

据此推导两个实现测试时必须保持的条件：

- setup 在 yield 前失败时，不能指望 yield 后代码运行；root 创建后尽早注册同步 `request.addfinalizer`，让它最终删除 root。setup 已启动的 owned task 在 async setup 的 `try/finally` 中 cancel 并 await，root 留到 pytest teardown，保证 setup-report 还能拍到它。pytest 对 yield 前失败与已注册 finalizer 的承诺也支持这种安排。[pytest fixture 清理说明](https://docs.pytest.org/en/stable/how-to/fixtures.html#handling-errors-for-yield-fixture)
- fixture 中 `with evidence.guard(): yield state` 不会接收到测试体的 call 异常；finalizer 恢复 generator 使用 `__anext__()`，不是 `athrow(call_error)`。call 异常必须在测试体自己的 guard 内覆盖；teardown 异常必须在 yield 后危险动作自己的 guard 内覆盖。setup、call、teardown 可以使用同一 function loop，但不可断言它们是同一个 `asyncio.Task`。

pytest `runtestprotocol` 顺序执行 setup、可选 call、teardown；`CallInfo.from_call` 捕获 `BaseException`，常规重抛集合是 pytest Exit 与 KeyboardInterrupt，未包含 `asyncio.CancelledError`。因此本地版本下，无 SIGINT 参与的未吞没取消应形成失败阶段报告并继续清理，预期 `session_finished=True`、`timed_out=False`，而非进程中断。[pytest 9.0.3 runner](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/runner.py#L123)

`CancelledError` 自 Python 3.8 起继承 `BaseException`，取消一般必须向上重新抛出。用 `task.cancel()` 后真正 `await` 产生取消，才能证明 guard 穿越 await 的行为；仅手工 `raise CancelledError` 不足以覆盖取消投递。[Python 取消异常](https://docs.python.org/3.11/library/asyncio-exceptions.html#asyncio.CancelledError)、[Task cancellation](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation)

## 最小具体矩阵

以下 8 行分别跑 strict、auto，共 16 个有界子进程；共同使用显式 `@pytest_asyncio.fixture(scope="function", loop_scope="function")` 和 `@pytest.mark.asyncio(loop_scope="function")`。每行只操作二进制合成 `raw` 文件与一个已握手启动的 owned background task。另加 1 个 auto 原生接管探针，总计 17 个子进程，无需重跑所有同步异常组合。

记 `S=b"async-setup\\x00\\xff"`、`C=b"async-call\\x00\\xff"`、`T=b"async-teardown\\x00\\xff"` 为建议实际字节值，代码里使用单层 Python 字节转义。P/F 表示阶段 passed/failed，— 表示该阶段不存在。以下均为待验证预期。

| 场景 | 注入位置 / 原始阶段 oracle | 必须校验的取证与清理 |
| --- | --- | --- |
| pass | S 后 yield，call 写 C，异步清理成功；P/P/P，status passed，verdict pass | owned task 已 cancel+await；root 删除；snapshots 空、retention 为 `discarded_after_all_phases_passed`，无 failure bundle |
| setup-fail | S 已写入和注册，await 后 yield 前抛唯一 RuntimeError；F/—/P，error/fail | setup-report 与 pre-teardown 是 S；call 标记不存在；任务在 setup finally 完成，预注册 finalizer 删除 root |
| call-fail | yield 后测试写 C、await、抛唯一 AssertionError；P/F/P，failed/fail | call-report 和 pre-teardown 是 C；正常 async teardown 仍执行并回收任务/root |
| teardown-fail | call 通过；yield 后 await、回收任务并删除 root，再抛唯一 RuntimeError；P/P/F，error/fail | pre-teardown 是 C，不能标作 teardown 失败瞬间；全部成功判断不得提前丢弃；root 确实已删 |
| both-fail | call 唯一 AssertionError，async teardown 另一个唯一 RuntimeError；P/F/F，failed/fail | 两个原始 longrepr 各含本阶段标识；C 快照和清理完成标记都在；不能只断言汇总 status |
| setup-cancel | S 和注册完成后，setup owner guard 中 self-cancel 并 await；F/—/P，error/fail | 原始 setup longrepr 含 CancelledError；owner-guard、setup-report 是 S；任务 finally 完成，root 最终删除 |
| call-cancel + checkpoint | call 先显式 checkpoint(S)，写 C；测试体 guard 中 self-cancel 并 await；P/F/P，failed/fail | checkpoint 仍为 S；owner-guard/call-report 为 C；longrepr 保留 CancelledError；清理正常完成 |
| teardown-cancel + owner | pre-teardown 后清理 owner 写 T，在内层 guard 中 self-cancel 并 await，外层 finally 回收任务/root；P/P/F，error/fail | pre-teardown 为 C、owner-guard 为 T；原始 teardown 含 CancelledError；快照保留；finalizer 完成 |

第 17 个探针：auto 模式下不标记 `async def test`，owner 改用普通 `@pytest.fixture(scope="function")` 的 async yield fixture，执行 call-fail 行。验证 async setup/call/teardown 均实际运行、失败报告和 bundle 字节正确，才证明 auto 接管普通 decorator。仅把主矩阵切到 auto、仍用全部显式 asyncio 标记，无法证明这一路由。

不把“strict 下普通 async @pytest.fixture”列为成功支持组合。strict 明确不接管它，pytest 9.0.3 原生 async fixture warning/error 受警告策略影响；没有必要为了这个误配置对诊断文本做版本脆弱断言。若要加一条误配置边界，只要求非绿、owner body 未运行，不把它算作 async 生命周期证明。[strict/auto 官方语义](https://pytest-asyncio.readthedocs.io/en/v1.3.0/concepts.html#test-discovery-modes)

模式可在父测试用 `monkeypatch.setenv("PYTEST_ADDOPTS", "--asyncio-mode=strict -o asyncio_default_fixture_loop_scope=function -o asyncio_default_test_loop_scope=function")`，auto 同理；设置为本探针精确参数可隔离继承的额外选项，并用子测试的 `request.config.getoption("asyncio_mode")` 断言真实生效。`run_pytest` 已复制环境且命令含 `-o addopts=`，无需改 runner 签名。探针在明确被 `run_pytest` 选中的 fixture 文件里定义，不加进默认测试 suite，不涉及 xdist/rerun。版本缺失应报环境失败，不用 `importorskip` 把矩阵变成绿。

## 三组独立 oracle

### 原始报告

读取 `result.report_path` 的 JSON，以完整 nodeid 获取 result，精确断言 `phases` 的键集合及每阶段 outcome，再断言原始 longrepr 的唯一异常标识。要求 `result.session_finished`、report `session_finished` 均真，退出码失败行为为 1、成功为 0，`timed_out=False`；没有 collection error、deselected、ordinary diagnostics。正常故障行应是 `fail`，不是仅断言 `!=pass`，否则 capture 失效造成的 harness_failure 会被误当成功证明。原始 phase 事实与 `result.cases` 应一致。[本地报告与 verdict 接缝](../../../../tests/testkit/pytest_plugin.py)、[runner](../../../../tests/testkit/runner.py)

### 快照与 failure bundle

对矩阵指定每个槽位，在原始 report 的 `ordinary_evidence.snapshots` 找 ref/digest；按 manifest `source` 精确选一条 bundle 条目。断言 source 文件与 bundle 副本 SHA-256 均等于报告 digest，payload 的 schema/nodeid/capture_point/source_digest/metadata identity 符合本探针；逐文件验证路径、base64 严格解码、长度、SHA-256、S/C/T 原始二进制值。要求快照完整、capture_error 为空、`ordinary_evidence.errors` 和 runner evidence_errors、failure_bundle_error 都为空。失败保留语义为 `last_committed_observations_not_exact_failure_instant`。[CaseEvidence 验证代码](../../../../tests/testkit/case_evidence.py)

checkpoint 与 owner guard 是两个不同固定槽，故 call-cancel 可直接证明较早 S 和较晚 C 都有报告绑定；同一槽位多次调用只留下最新成功写入的观察，不能推导完整历史。字节变化与独立顺序记录是主要时序 oracle，不依赖 wall-clock 纳秒严格递增，也不要求跨文件原子一致。

### 任务与资源清理

background task 启动先 `started.set()`，再等待专用 `asyncio.Event`；owner 必须 await started 后才注入故障，避免取消尚未开始的 coroutine 导致 finally 根本没有运行。task 的 finally 记 `task-finally`。owner 清理显式 `task.cancel()` 然后 await，只在这一处接收该 owned task 的预期 CancelledError；断言 `task.done()`、`task.cancelled()` 和 finally 标记，再记 `owner-task-joined`。不要吞掉 setup/call/teardown 自身注入的取消。[Python task 取消及 finally](https://docs.python.org/3.11/library/asyncio-task.html#task-cancellation)

用父测试通过专用环境变量传入的临时诊断路径，记录有限 trace、root 路径、任务完成状态；该路径位于注册 root 外，是本测试合成的清理 oracle，不属于状态快照声明。先记录 `task-finally`、再 `owner-task-joined`、最后 `root-removed`，父端确认顺序及 root 不存在。pass 行快照会全部删除，更需要这个独立清理 oracle。root 的最后同步 finalizer 应幂等，并在清理后记录成功，不能依赖 pytest 自身过期 tmp 目录回收来证明 owner 清理。

在 loop 仍运行时，通过已持有的 owned task 引用检查完成状态；不要对包含 pytest runner 当前 task 的 `asyncio.all_tasks()` 全集断言为空。可记录 `task.get_loop() is asyncio.get_running_loop()` 来证明同一 function loop；不要依赖 task 的数字 id 跨阶段相同。可辅助检查日志没有 `Task was destroyed but it is pending` / `Task exception was never retrieved`，但日志安静不构成主清理证明。

Python 3.11.15 `Runner.close()` 自身会 cancel 剩余 task 并 gather、关闭 async generators 和 executor。因此子进程退出干净只能证明最终停止，不能证明 fixture 正确清理；必须在 owner teardown 的 loop 内完成上述 join oracle。本地 `_cancel_all_tasks` 还把非取消异常交给 loop exception handler，这类日志不保证成为普通失败 phase。[Runner 文档](https://docs.python.org/3.11/library/asyncio-runner.html#asyncio.Runner.close)；实现核对位置：`/home/uisee/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu/lib/python3.11/asyncio/runners.py` 的 `close` 和 `_cancel_all_tasks`。

## Owner guard 放置及未覆盖边界

teardown 取消行建议结构如下；这是测试样例方法，不是新增生产接口：

```python
try:
    with evidence.guard():
        raw.write_bytes(TEARDOWN_BYTES)
        asyncio.current_task().cancel("ordinary-async-teardown-cancel")
        await asyncio.sleep(0)
finally:
    await stop_owned_task()
    remove_owned_root()
```

guard 在 root 删除前展开，才能得到 T。若把删除 `finally` 放到 guard 内层，guard 只能看见删除后的状态。若在 call/setup 内提前删除 root，即使 owner-guard 已保住字节，后续自动 checkpoint 仍可能记录 root missing，使最终 verdict 为 harness_failure；这属于当前取证错误语义，不应在测试里假定自动忽略。[guard 与 checkpoint 代码](../../../../tests/testkit/case_evidence.py)

本轮不覆盖 module/session fixture、共享 loop、多线程写文件、第三方 loop policy、后台未 await 异常自动升级、contextvars 跨 TaskGroup 生命周期、SIGINT/KeyboardInterrupt、强杀/timeout、pytest 新版本、xdist 或 rerun。已有同步 kill/timeout 测试不能当作异步清理证明；本轮取消是正常 task cancellation，不是进程终止。若上述普通 17 行暴露产品实现问题，应保存原始报告和失败样例交回主任务，再决定是否修复；本调研和本轮测试授权不包含改 `src` 或重做框架。

建议验收顺序：增加显式 async probe → verify: 17 行精确 phase、byte、cleanup oracle；运行相邻普通 evidence 生命周期 / capture 测试 → verify: 原同步能力未回退。这里只提供验证计划，没有运行任何一层测试。
