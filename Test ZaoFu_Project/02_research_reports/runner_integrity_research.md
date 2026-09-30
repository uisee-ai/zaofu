# TestKit runner 结果完整性调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/runner.py`。完整性校验及 pytest_plugin.py 已存在；本报告是方法来源，不是 runner 仍未实现的清单。

调研日期：2026-09-10。文档类别：调研记录。范围：pytest 官方文档与源码；仓库 `uv.lock` 当前锁定 pytest 9.0.3，因此源码优先引用官方 9.0.x 文档。以下是 runner 判据建议，尚不代表 ZaoFu 已实现或通过回归验证。

结论：`exit 0` 只提供本次 pytest 调用的状态，不能独自证明 runner 要求的全部测试实际执行并通过。必须核对预期测试身份、收集和排除记录、逐项阶段报告及进程结束状态。依据见下列官方来源。

## 官方语义

| 观察值 | 可以证明什么，以及缺少什么 |
| --- | --- |
| `exit 0` | 官方退出码页描述成功；但默认 `_main` 只检查 `testsfailed` 和 `testscollected == 0`。成功收集至少一项的 `--collect-only` 会跳过 runtest loop 并可返回 0。部分 deselect 后剩余测试通过，也可返回 0；默认全部排除则返回 5。不能由退出 0 推出测试范围完整。[退出码](https://docs.pytest.org/en/stable/reference/exit-codes.html)、[主流程源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/main.html) |
| `pytest_collection_finish(session)` | 正常路径位于 `pytest_collection_modifyitems` 之后，此时 `session.items` 是筛选后的待执行项。`session.testscollected` 在该 hook 返回后才更新，因此 hook 内应读取并冻结 `session.items` 的 nodeid。该 hook 在 `finally` 中，也不能单凭其出现判定收集无异常。[收集源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/main.html#Session.perform_collect) |
| `pytest_deselected(items)` | 通知哪些测试被排除；可能多次调用，需要累积。自定义 `collection_modifyitems` 删除测试时必须主动调用它，pytest 不会自动替自定义插件补报。`-k`、`-m` 的内置实现会通知后原地缩减 `items`。[hook 契约](https://docs.pytest.org/en/9.0.x/reference/reference.html#pytest.hookspec.pytest_deselected)、[内置筛选源码](https://docs.pytest.org/en/stable/_modules/_pytest/mark.html) |
| `pytest_runtest_logfinish` | 单项 runtest 协议结束。setup 未通过或启用 `--setup-only` 时，仍可缺少 call 阶段；所以“结束”不等于测试体通过。setup、call、teardown 必须分别判读。[执行源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/runner.html) |
| `pytest_sessionfinish` | 会话结束通知；默认主流程在 `finally` 调用它，收集失败、用户中断或 `pytest.exit(..., returncode=0)` 提前退出都可能走到这里。它不能证明所有计划项已完成。[主流程源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/main.html) |

## 显式调用仍受环境与配置影响

参数合成顺序为配置文件 `addopts`、`PYTEST_ADDOPTS`、显式参数。显式测试路径不会取消环境中的 `-k`、`-m`、`--deselect` 或 `--collect-only`；仅对相互覆盖的选项适用“后者胜出”，不能把它理解成显式参数替换全部默认参数。[官方参数示例](https://docs.pytest.org/en/9.0.x/example/simple.html#how-to-change-command-line-options-defaults)

`Config.invocation_params.args` 不包含环境及配置的追加参数；只存启动 argv 会漏记实际影响测试的条件。建议 runner 在子进程环境移除 `PYTEST_ADDOPTS`，用 `-o addopts=` 明确覆盖配置追加参数，再显式提供所需选项，并记录解析后的关键选项。此为基于解析顺序的实现建议；会改变依赖项目默认参数的行为，应由 runner 契约明确。[配置解析源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/config.html)、[配置覆盖接口](https://docs.pytest.org/en/stable/reference/reference.html#configuration-options)

## 建议的机械判据

以下建议针对 runner 控制的普通串行 pytest 执行；重试、重复收集、分布式插件需要独立契约，不能直接套用“每项恰好一次”。判据建立在上述官方生命周期上，并非 pytest 自带的完整性保证。

1. 固定预期范围：由 runner 的计划定义必测集合 `E`，保留参数化后的完整 nodeid；不能用本次筛选后的结果反过来定义预期。若计划只给文件或函数范围，必须显式核对每个 selector 的覆盖，且承认无法仅凭数量证明所有参数实例存在。
2. 核对收集：累积 `pytest_itemcollected` 的原始项 `C`、所有 deselected 项 `D`，在 collection finish 冻结执行项 `S`。严格全跑模式要求 `D` 为空、`S` 非空且覆盖 `E`；有完整精确计划时要求 `S == E`。再核对 `Counter(C) == Counter(S) + Counter(D)`，发现插件静默删项或重复项时拒绝完整通过。`C` 也看不到从未被收集的文件，因此它不能替代独立预期。[收集 hook 顺序](https://docs.pytest.org/en/9.0.x/reference/reference.html#pytest.hookspec.pytest_collection)
3. 核对执行：每个必测 nodeid 有匹配的开始、结束及 setup/call/teardown 报告；严格通过要求三阶段均通过且无 xfail 标记。缺少 call、只有 setup、仅有一个总通过计数均不合格。skip/xfail 要保留独立结果，并按显式接受策略处理；不能自动算作必测项已验证通过。[执行源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/runner.html)、[skip/xfail 说明](https://docs.pytest.org/en/stable/how-to/skipping.html)
4. 核对结束：要求本次新生成的完整报告、会话结束记录、子进程实际退出 0，以及全部必测项执行证据齐全；拒绝收集错误、阶段失败、缺失或重复证据、未知 nodeid、超时和异常终止。报告须绑定本次 run 身份及计划；旧报告不能补足本次缺失。即使 sessionfinish 或退出码声称成功，只要逐项证据不闭合就判为执行不完整。

建议回归反例：环境注入 `-k`/`--deselect` 仅运行部分项；环境注入 `--collect-only`；`--setup-only`；自定义插件删项未通知；首项后提前退出 0；call 通过但 teardown 失败；所有必测项被 skip；读取上次成功报告。这些是待验证的用例建议，本调研未执行复现。

## 局限

测试进程内插件报告不是恶意进程的信任边界：pytest 允许插件修改收集项、执行协议及报告，同进程代码也可干扰采集器。上述判据用于识别正常工具链的参数污染、漏跑和报告不一致；不能证明恶意测试未伪造报告，更不能证明断言充分。测试数据完整性 ≠ 安全沙箱。恶意代码隔离需要测试进程外的权限和执行边界；本调研不评估这类安全机制。[pytest hook 扩展接口](https://docs.pytest.org/en/9.0.x/reference/reference.html#pytest.hookspec.pytest_runtest_protocol)
