# TestKit 普通证据：并行与重试合同调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/test_parallel.py`。已有并行及 test_rerun_evidence.py 合同；重试不覆盖首轮红灯，隔离并行不等于业务并发证明。

> 调研日期：2026-09-14。仅检查本地 `.venv`、仓库 TestKit 代码及 pytest/xdist 第一方文档与源码；未运行真实 Provider/远端。

## 版本与事实

- `.venv` 实测：pytest `9.0.3`、pytest-xdist `3.8.0`；`pytest-rerunfailures` 未安装（因此其行为只能引用官方文档，不能声称本地兼容已验证）。版本锁定也见 [`uv.lock`](../../../../uv.lock) 的 pytest/pytest-xdist 条目。
- 当前 [`tests/testkit/pytest_plugin.py`](../../../../tests/testkit/pytest_plugin.py) 以 `report.nodeid` 为 key，把 setup/call/teardown 写进单一 `results`，并在每个 `pytest_runtest_logreport` 原子写报告。它没有 worker_id、run attempt 或 rerun phase 字段；多个进程若共享 `--testkit-report`，最后一次 `replace` 会覆盖此前结果。
- pytest 的 `TestReport` 公开 `nodeid`、`when`、`outcome`、`longrepr`、`sections` 等字段；`pytest_runtest_logreport` 接收每个阶段报告（[pytest 9.0.3 reports.py](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/reports.py)、[hookspec](https://docs.pytest.org/en/9.0.x/reference/reference.html#pytest.hookspec.pytest_runtest_logreport)）。这些字段足以保留阶段事实，但不包含 xdist worker 身份。

## xdist 的并行语义

pytest-xdist 官方文档规定 `worker_id` fixture 返回 `"master"`（无 worker 时）或形如 `"gw0"` 的 worker 名称；`testrun_uid` 标识一次完整测试运行（[distributed testing: fixtures](https://pytest-xdist.readthedocs.io/en/stable/how-to.html#identifying-the-worker-process-during-a-test)、[testrun_uid](https://pytest-xdist.readthedocs.io/en/stable/how-to.html#uniquely-identifying-the-current-test-run)）。同一个 nodeid 可能在不同 worker 进程出现，故 `(testrun_uid, worker_id, nodeid)` 才是普通证据的最小进程内身份；若还启用 rerun，必须再加 attempt/phase。

xdist 的 worker 通过 execnet 向 controller 发送序列化 report，controller 再把测试结果呈现给 pytest；插件在 worker 侧写文件时不会自动由 xdist 合并插件自有 JSON（[xdist architecture](https://pytest-xdist.readthedocs.io/en/stable/how-it-works.html)、[pytest hook implementation guidance](https://pytest-xdist.readthedocs.io/en/stable/how-to.html#making-session-scoped-fixtures-execute-only-once)）。因此 TestKit 应让每个 worker 使用独占输出目录/文件，结束后由父 runner 做 schema 校验与显式合并；不能让多个 worker 竞争同一个 `pytest-testkit.json`。

**可支持**：每 worker 独占目录；记录 `worker_id`、`testrun_uid`、nodeid、阶段 outcome/longrepr；父进程按完整身份合并，并拒绝重复身份或缺失 worker 元数据。**应 fail-closed**：共享 report 路径、仅按 nodeid 合并、worker 退出但无 session-finish、同一身份出现互相冲突阶段，均不得生成 pass。

## rerun phase 语义

pytest-rerunfailures 官方文档说明 `--reruns N` 会在失败后重新执行测试，并可用 `--only-rerun`/`--reruns-delay` 控制；插件提供 `pytest_rerunfailures` 标记和 `report.rerun` 计数（[usage](https://pytest-rerunfailures.readthedocs.io/en/stable/quickstart.html)、[plugin source](https://github.com/pytest-dev/pytest-rerunfailures/blob/master/src/pytest_rerunfailures.py)）。pytest 原生 `TestReport` 没有 rerun 字段，故 TestKit 必须在 logreport 时以 `getattr(report, "rerun", None)` 读取插件字段，并保留每一轮 setup/call/teardown，而不能覆盖同一 nodeid 的旧阶段。

重试后最终通过不等于首轮通过。普通证据合同应将每轮保存为 `attempt=0..N`，最终 verdict 对“曾失败后重试通过”至少标为 harness/non-green（或明确 `flaky_pass`，但不能是普通 `pass`）；缺少 rerun 插件时发现重复 nodeid/阶段序列应 fail-closed，而不是猜测 attempt。

## 最小合同测试（只新增测试，优先不改生产代码）

1. **xdist 身份**：合成项目运行 `pytest -n 2`，两个测试各写一条证据；verify：每条含 `testrun_uid`、`worker_id in {gw0,gw1}`、nodeid，文件路径互不相同；父合并后恰有两条。若插件无法注入 worker_id，期待 `harness_failure`。
2. **同 nodeid 保护**：两个 worker 伪造/产生同 nodeid；verify：合并器拒绝覆盖并报告 duplicate identity，绝不 pass。
3. **worker 崩溃**：worker 在阶段报告后硬退出；verify：已落盘阶段可诊断，缺少 session 完成或 teardown 的 case 为 `harness_failure`。
4. **rerun（依赖安装后）**：首轮 `assert` 失败、第二轮通过，`--reruns 1`；verify：保留两轮 `report.rerun`（0、1）及各阶段，最终 verdict 不是普通 pass。未安装插件时该测试应 skip 为环境缺失，不能把未执行当支持证明。
5. **输出目录隔离**：并行 + rerun 同时运行；verify：路径至少按 `testrun_uid/worker_id/nodeid/attempt` 隔离；发现共享路径或 digest 冲突即 fail-closed。

首版安全范围应声明为“单进程普通 function scope”或实现上述身份/合并合同后再宣称 xdist/rerun 支持；仅给现有 nodeid map 增字段而不改变写入与合并路径，不足以证明安全。
