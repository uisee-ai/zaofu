# pytest xfail 与 TestKit 可追溯性调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/pytest_plugin.py`。当前插件已保存 skipped/xfail 的 longrepr；S0 已有追溯入口。旧“只保存 failed”描述只适用于历史基线。

> 调研范围：仓库锁定 pytest 9.0.3（见 `uv.lock`）；以下语义以 pytest 官方文档及 9.0.3 源码为准。

## 官方语义

### xfail、XPASS、strict

- `pytest.mark.xfail` 表示“预期该测试失败”；测试仍会执行。失败记为 XFAIL，成功记为 XPASS。官方说明明确区分了 skip（条件不满足时不执行）与 xfail（预期失败）[pytest skipping/how-to](https://docs.pytest.org/en/stable/how-to/skipping.html#xfail-mark-test-functions-as-expected-to-fail)。
- 默认 `strict=False` 时，XFAIL 与 XPASS 都不会令 pytest 会话失败；`strict=True` 时，预期失败仍为 XFAIL，而意外通过的 XPASS 会令整个测试套件失败[pytest.mark.xfail API](https://docs.pytest.org/en/stable/reference/reference.html#pytest.mark.xfail)。
- 配置项 `strict_xfail` 的默认值是 `false`；pytest 9 将旧名 `xfail_strict` 作为别名接受。项目若要求每个缺陷标记严格可见，应在标记上显式写 `strict=True`，不能依赖全局默认[pytest config/API](https://docs.pytest.org/en/stable/reference/reference.html#confval-strict_xfail)。
- `--runxfail` 会把 xfail 当普通测试运行并报告，同时使 `pytest.xfail()` 不生效；它适合验证修复，不应作为常规门禁配置[pytest skipping/how-to](https://docs.pytest.org/en/stable/how-to/skipping.html#ignoring-xfail)。

### `raises` 限定

- `raises` 接受一个异常类或异常类元组；只有测试抛出这些类型（含其子类）时才算预期失败，其他异常会按普通失败报告[pytest.mark.xfail API](https://docs.pytest.org/en/stable/reference/reference.html#pytest.mark.xfail)。
- pytest 9.0.3 实现先取得 `raises`；若为 `None`，任何调用异常都可转为 xfailed；若为类/元组则使用 `isinstance`，另支持 pytest 的 `AbstractRaises.matches`[skipping.py 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py#L1296-L1327)。
- 因而 `raises=None`（默认）可以把任意异常转换为 XFAIL，包括 `AssertionError`；`raises=AssertionError` 也能接受普通断言失败。pytest 不会判断该断言是否真的是某个产品缺陷，`raises` 只限定 Python 异常类型。
- 若使用 `raises=SomeError`，断言失败是 `AssertionError`，类型不匹配时应保留为普通失败；这可防止把无关错误伪装成已知缺陷，但不能替代 Issue/阶段语义审核。

### skip 与 xfail 的结果形态

- skip 的含义是“不运行”；xfail 的默认含义是“运行并预期失败”。二者都会在终端单独计数，`-r xX s` 可显示详情[pytest skipping/how-to](https://docs.pytest.org/en/stable/how-to/skipping.html#how-to-use-skip-and-xfail-to-deal-with-tests-that-cannot-succeed)。
- pytest 9.0.3 的 `pytest_runtest_makereport` 在 xfail 异常或匹配 `raises` 时把 `TestReport.outcome` 设为 `"skipped"`，并设置 `report.wasxfail`；终端状态 hook 以该属性区分 XFAIL (`x`) 与 XPASS (`X`)[skipping.py 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py#L1288-L1355)。
- strict XPASS 的实现把 outcome 设为 `"failed"`，并将 `longrepr` 设为 `[XPASS(strict)] <reason>`；非 strict XPASS 保持 `"passed"` 并设置 `wasxfail`[skipping.py 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py#L1329-L1342)。
- 因此不能只看 `report.outcome`：`skipped + wasxfail` 是 XFAIL，`passed + wasxfail` 是非 strict XPASS，`failed` 且 longrepr 以 `[XPASS(strict)]` 开头是 strict XPASS。普通 skip 没有 `wasxfail`。

## 失败证据（longrepr）

- 官方 `TestReport` 明确公开 `outcome`、`longrepr`、`when`（setup/call/teardown）、`sections` 等字段；`longrepr` 是失败表示，`sections` 还承载捕获的 stdout/stderr/log 文本[TestReport 源码](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/reports.py#L2055-L2152)。
- `TestReport.from_item_and_call` 在调用异常时先构造失败 `longrepr`，其中包含 traceback/断言表示[reports.py 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/reports.py#L2170-L2275)。随后 xfail hook 只改 outcome/wasxfail；对匹配的 xfail 没有清空原 `longrepr`[skipping.py 9.0.3](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py#L1296-L1327)。
- 这意味着插件收到 call 阶段 XFAIL 时仍可读取并序列化 `report.longrepr`（以及 `report.sections`）作为精确失败证据。当前 `tests/testkit/pytest_plugin.py` 仅在 `report.failed` 时保存 longrepr；XFAIL 是 `skipped`，故当前报告只保留 `wasxfail`/reason，缺少 traceback，建议补上对 `wasxfail` 的 longrepr 保存。

## 对 ZaoFu TestKit 的结论与建议

pytest 自身只提供异常匹配、XFAIL/XPASS 状态和 strict 行为；“这是已登记 Issue”“属于哪个阶段”“原因必须带 Issue ID”“每阶段 `known_issues` 必须覆盖”都不是 pytest 能力，而是 ZaoFu/TestKit 项目规则。

当前 S0 静态审计主要扫描 `tests/conformance` 中的 xfail，不能证明 `SUITES` 注册的其他阶段文件没有未审计标记。建议扫描 `tests/testkit/suites.py` 的 `SUITES` 展开后的每个实际文件（S0 也包含），并对每个 `pytest.mark.xfail` 强制检查：

1. 标记显式含 `strict=True`；
2. `reason` 为字面字符串且含合法 Issue ID（例如 `ZF-TEST-014`）；
3. 该 Issue ID 出现在当前执行阶段的 `coverage_matrix.json` `known_issues`；
4. 若同一测试文件注册到多个阶段，则每个阶段的 `known_issues` 都要声明该 ID；
5. 运行时报告保留 `call` 阶段的 `longrepr`/sections，连同 `wasxfail`、reason、nodeid 和阶段，形成可复核证据；strict XPASS 必须继续作为失败。

`raises` 可用于缩小预期异常（推荐针对稳定、明确的异常类型），但即使写了 `raises`，也不能自动建立 Issue 或阶段追踪关系。不要用无条件 `xfail`、`raises=None` 或普通 `skip` 来吞掉任意断言失败；若确属已知产品缺陷，应由上述项目规则和证据链约束。
