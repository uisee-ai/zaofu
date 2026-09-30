# Hypothesis 状态机失败证据保留研究

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/model_evidence.py`。失败证据支撑已存在，配套 test_model_evidence.py；保留 shrinking/历史反例边界，不作为本轮执行结果。

研究日期：2026-09-14；范围：S11 状态机、TestKit 测试支撑层与 pytest 子进程报告。
核验基线：HEAD `142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`，包含研究时已有未提交修改。
本报告只提出方案；没有修改生产代码、调用真实 provider、提交或推送。

建议在规则与不变量内部捕获失败，清理前把操作序列和完整受控 fixture 快照写入稳定目录。
每个测试只保留 `first.json` 与 `latest.json` 两槽；后者表示最近观察到的失败，不宣称是最小反例。
Hypothesis 最终输出继续保存在 pytest 报告；操作 JSON 提供独立、可审计的显式重放入口。

## 已有实现与缺口

- [EventLog 状态机](../../../../tests/conformance/test_zaofu_testkit_s11_hypothesis.py) 每轮建立 `TemporaryDirectory`，`teardown()` 立即执行 `cleanup()`。
- 同文件的 TaskAttempt `@given` 测试使用临时目录上下文；异常离开上下文时也会删除状态文件。
- [两个控制模型](../../../../tests/conformance/test_zaofu_testkit_s11_control_models.py) 同样在 teardown 删除 fixture，涉及 TaskStore、EventLog 与运行时相关文件。
- `ControlledProposalMachine.__init__()` 调用已装饰的 `self.reopen()`；新增操作记录须识别初始化边界，避免重放时额外执行初始化操作。
- [pytest_plugin.py](../../../../tests/testkit/pytest_plugin.py) 保存阶段 outcome 和 longrepr，没有按状态机示例保存文件。
- [runner.py](../../../../tests/testkit/runner.py) 在 pytest 子进程结束后才构建 failure bundle，目前输入限报告、JUnit、stdout、stderr。
- [failure_bundle.py](../../../../tests/testkit/failure_bundle.py) 已支持不可覆盖目录、原子完成、逐文件摘要，适合作为外层交付封装。
- [evidence.py](../../../../tests/testkit/evidence.py) 提供 JSON 原子写入与 SHA-256，可复用；其写入异常路径尚无临时文件清理。

Python 明确规定临时目录上下文退出或对象销毁会删除目录内容；显式 `cleanup()` 更早执行删除。
因此晚期报告只能收集预先保留的副本，不能靠路径字符串找回已删除文件。[Python tempfile](https://docs.python.org/3.11/library/tempfile.html#tempfile.TemporaryDirectory)

## 装饰器顺序：保留属性仍可能绕过包装器

推荐组合是 `rule(...)(record_action(function))`，即记录装饰器最靠近原函数：

```python
@precondition(lambda self: self.ready)
@rule(value=st.integers())
@record_action
def append(self, value):
    ...

@invariant()
@record_invariant
def consistent(self):
    ...
```

记录包装器使用 `functools.wraps`，保留名称、说明、注解和函数字典；公开机制支持这些属性的复制。
但属性复制不等于修改已经注册的回调。[Python functools](https://docs.python.org/3/library/functools.html#functools.update_wrapper)

6.168.0 官方源码显示：`rule` 与 `invariant` 保存装饰时收到的函数，执行器直接调用该函数。
若把 `record_action` 放在 `@rule` 外侧，复制的元数据仍指向原函数，生成执行会绕过记录层。
此处只用源码解释行为；实现不读取或修改 marker、Rule 对象、ConjectureData 等内部结构。[Hypothesis stateful 源码](https://hypothesis.readthedocs.io/en/latest/_modules/hypothesis/stateful.html)

本次用公开 `run_state_machine_as_test`、`settings` 与装饰器执行了无文件探针：

| 失败位置 | 记录器在外侧 | 记录器在内侧 |
| --- | --- | --- |
| rule 抛 AssertionError | 捕获 0 次 | 捕获 12 次 |
| invariant 抛 AssertionError | 捕获 0 次 | 捕获 2 次 |

计数只是本地 6.168.0 的观察值，不能写成未来执行次数契约；应断言是否真正捕获和原异常是否保留。
公开状态机入口允许传入工厂及 settings，足以构建这些测试，不需要私有测试生成器。[状态机文档](https://hypothesis.readthedocs.io/en/latest/stateful.html#more-fine-grained-control)

## teardown、异常状态与报告生命周期

状态机 teardown 没有公开的 outcome 或 shrinking 完成参数；官方源码当前从每轮执行的 `finally` 调用它。
规则抛错时此位置可能看到活动异常，但这不是“最后一次失败”的通知。[状态机源码](https://hypothesis.readthedocs.io/en/latest/_modules/hypothesis/stateful.html)

`sys.exc_info()` 返回调用栈中正在处理的异常；它不负责判断异常属于哪个示例。
探针中，成功状态机在外层 `except RuntimeError` 内执行，其 teardown 仍观察到 `RuntimeError`。
因此用 `sys.exc_info()[0] is not None` 决定保留失败文件会出现误判。[Python sys](https://docs.python.org/3.11/library/sys.html#sys.exc_info)

pytest 的 call 报告创建于测试调用结束之后，随后才执行 pytest teardown；teardown 报告更晚。
普通 yield fixture 通常此时仍存活，但状态机每轮自有 cleanup 已在 call 内执行，二者不可混同。
`pytest_runtest_makereport` / `pytest_runtest_logreport` 应汇总副本引用，不承担抢救每轮临时目录。[pytest 生命周期](https://docs.pytest.org/en/stable/reference/reference.html#pytest.hookspec.pytest_runtest_protocol)

## shrinking 与“最小”的准确表述

发现失败后还可能执行 shrink、explain，并再次执行最小失败用例检查稳定性；`max_examples` 不是总调用次数上限。
这说明每轮失败都是候选证据，无法仅凭计数、文件时间或 teardown 顺序识别最终最小反例。[Hypothesis 执行次数](https://hypothesis.readthedocs.io/en/latest/explanation/test-case-count.html#failing-test-cases)

多个独立错误、重放不稳定、执行中断等情况下，最后保存的失败不能代表完整最终报告。
建议元数据固定写 `selection=first_observed/latest_observed`、`minimality=unverified`，不读取私有 `is_final`。
Hypothesis 可一次报告多个错误；即使设置只报告一个错误，也不应把保留策略升级为公开最小性保证。[Hypothesis settings](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.settings.report_multiple_bugs)

每次原异常的 note 附当前 trace digest 与稳定目录，使最终报告能核对该 trace 是否仍在两槽中。
不能只附 `latest.json` 路径：它后来可能已被不同失败覆盖；匹配失败须明确注明。
若以后必须交付“最终最小序列对应的状态”，应另设显式重放验证步骤，本轮两槽方案不承诺这一点。

## 最小测试支撑层建议

新增单一 `tests/testkit/model_evidence.py`，提供实例级记录器和内层规则／不变量装饰器；避免全局类扫描与自动 monkeypatch。
实例只保留本轮动作与必要检查点，规则执行前记录参数，使导致异常的最后一步也在序列内。
当前模型参数是有限字符串、整数、布尔值，可用受限 JSON 编码；不依赖任意对象 `repr` 作为可重放数据。
保留初始 fixture 参数与模型版本；初始化期间的内部规则调用须抑制记录，或明确纳入初始化阶段。
不变量在初始化后也可能运行，故应保存检查名和对应操作位置，不能默认失败总发生在某个规则内。

捕获时先写一个自包含 JSON：相对文件路径、base64 原始字节、每文件 SHA-256、操作序列与 trace digest。
同时写 exception 类型／消息、模型名、nodeid、Python／pytest／Hypothesis 版本、settings、源码／配置／lock 摘要。
实际 `state_dir` 单独记为 fixture 相对路径；EventLog 保存原始字节，不通过重新序列化事件改变现场。
快照含诊断现场；动作重放从新建初始 fixture 开始，不能把失败后的状态当初始状态再次执行完整操作。

runner 注入 `ZF_TESTKIT_MODEL_EVIDENCE=<output>/model-evidence`；目录按 nodeid 摘要、模型、PID 隔离。
该环境变量是测试证据输出约定，不是替代 `zf.yaml` 的运行时控制面配置。
每个测试稳定保留 first/latest 两槽，子进程完成后把这些 JSON 纳入现有 failure bundle。
普通 pytest 直接运行时，仅首次真实失败才创建持久临时证据目录；成功示例正常清理，不生成逐轮目录。
输出根应位于模型 fixture 之外；重试／并行执行依赖独立 run 根，PID 不能单独充当持久唯一身份。

复制前限制文件数、单文件大小和总原始字节；另计 base64 膨胀、JSON、两槽与一次原子写临时文件的空间。
只接受 fixture 根内普通文件，拒绝符号链接、根外路径，排除证据输出自身，避免递归复制。
超限或缺失必需文件应记录 `capture_error/incomplete`，不能静默截断后仍宣称完整可重放。
已有原子 JSON helper 出错可能遗留临时文件，清理必须限本次 writer 创建的具体路径，确保两槽约束成立。

捕获器忽略 `UnsatisfiedAssumption`；这是示例被舍弃，不是产品失败。[Hypothesis assume](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.assume)
证据序列化、读文件、写文件或清理出错时，附加 capture_error 后用裸 `raise` 保留原异常及 traceback。
note 可使用 Python 3.11 `BaseException.add_note`，诊断写入本身也须有保护。[Python exception notes](https://docs.python.org/3.11/library/exceptions.html#BaseException.add_note)
构造函数、precondition、Hypothesis 框架和 teardown 自身失败不在两种包装器覆盖内；报告应允许 `evidence_unavailable`。
成功规则记录层若出错，不能阻止被测规则执行；记录故障应独立标记，最终验收不得误报证据完整。

## 重放身份、seed 与版本锁定

`derandomize=True` 受测试函数、Python 与 Hypothesis 版本影响；它不能证明已取得某个数字 seed。
只有测试显式配置或 CLI 实际收到 seed，才记录值及来源；否则写 null／not_captured，不读取私有随机状态。[Hypothesis seed](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.seed)
`print_blob=True` 保留实际输出；blob 不跨 Hypothesis 版本稳定，不能把未出现的 blob 伪造为已捕获。[官方失败重放](https://hypothesis.readthedocs.io/en/latest/tutorial/replaying-failures.html#replaying-test-cases-with-reproduce-failure)

本地与 [uv.lock](../../../../uv.lock) 一致：Hypothesis 6.168.0、pytest 9.0.3；探针使用 Python 3.11.15。
[pyproject.toml](../../../../pyproject.toml) 目前仅约束 `hypothesis>=6.100`；验收应从既有 lock 安装并核对实际版本。
若绕过 lock 安装则失去此基线；升级 Hypothesis 时重跑装饰器、shrinking、捕获错误和重放契约。
版本读取用公开包元数据；源码 dirty=true 仍不足重建代码，应补所涉测试与支撑层的内容摘要。[Python package metadata](https://docs.python.org/3.11/library/importlib.metadata.html#distribution-versions)

## 必须补充的有界契约测试

- 注入合成状态机 bug → verify：真实 pytest 子进程非零退出，报告含原 AssertionError 和 Hypothesis 失败输出；开启 shrink。
- 子进程退出后检查 → verify：原 fixture 已删除，first/latest 仍可读取；文件内容、trace 与 manifest 摘要重新计算一致。
- 白名单模型工厂＋白名单动作重放 → verify：新 fixture 上出现相同异常与关键 EventLog／Store 事实，不执行 artifact 中的任意代码。
- 固定装饰器顺序 → verify：rule 与 invariant 两种失败均捕获；被测函数名称保留，precondition 行为不变。
- 注入文件写入失败／超预算 → verify：原产品异常仍在报告，capture_error 可见，结果非 pass，无无限增长的临时文件。
- 正常模型运行 → verify：所有示例目录清理，模型失败目录为空或不存在，报告仍为 pass。
- 跨 nodeid／模型／子进程运行 → verify：证据互不覆盖；同一测试多次失败最多两槽，latest 不标注为最小。
- 重放假 bug 修复版本 → verify：原序列在修复版本通过，证明捕获产物可转为确定性回归，不只是可解析 JSON。

这些适合用 pytest 官方 `pytester`／子进程支持实现隔离测试，限制 examples、steps、超时与文件预算。[pytest 插件测试](https://docs.pytest.org/en/stable/how-to/writing_plugins.html#testing-plugins)
本次仅执行了装饰器顺序及 `sys.exc_info()` 的公开 API 探针；尚未实现或运行上述失败包端到端契约。
