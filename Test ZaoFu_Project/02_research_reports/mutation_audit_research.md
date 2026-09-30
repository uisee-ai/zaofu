# mutmut 变异测试审计调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/mutation_audit.py`。已有 mutation 审计工具；依赖版本、样本和真实运行仍属显式周期层，不扩大为本轮执行。

> 调研边界：只研究 mutmut 的官方文档、PyPI 元数据和当前主分支源码；没有在 ZaoFu 上安装项目依赖，也没有执行任何变异运行。为验证本地可行性，只执行了未变异的基线 pytest。

## 1. 结论

mutmut 适合做 ZaoFu 核心纯 Python 模块的周期性测试有效性审计，而不是日常功能回归，也不是产品质量评分。建议把它放在 S10/perf-and-audit 或 nightly 层，先限定到 `EventSigner`、EventLog、Store 和状态转换等确定性模块。

当前官方版本是 **3.7.0**：官方 PyPI 页面显示 3.7.0 于 2026-07-31 发布，要求 Python `>=3.10`；官方仓库 `pyproject.toml` 同样声明版本 3.7.0 和 Python 下限。当前 ZaoFu 使用 Python 3.11（`uv run python --version` 为 3.11.15），因此解释器版本满足要求；但 ZaoFu 项目的 `.venv` 没有 mutmut 项目依赖。已验证隔离工具缓存可解析 `mutmut, version 3.7.0`，而不改变项目依赖。

来源：

- [mutmut 官方 PyPI 元数据与发布历史](https://pypi.org/project/mutmut/)
- [mutmut 官方仓库 pyproject.toml](https://raw.githubusercontent.com/boxed/mutmut/main/pyproject.toml)
- [mutmut 官方文档](https://mutmut.readthedocs.io/en/latest/)

## 2. mutmut 的执行模型和配置事实

### 2.1 运行模型

`mutmut run` 会把源代码复制到 `mutants/`，生成带变异 schema/trampoline 的副本，先收集测试与函数的关联，再为每个 mutant fork 子进程执行关联测试。结果写入每个变异文件对应的 `*.meta`，统计信息写入 `mutants/mutmut-stats.json`；`mutmut export-cicd-stats` 可再生成 `mutants/mutmut-cicd-stats.json`。

官方源码中的关键行为：

- `run` 默认使用 pytest runner；先执行 clean tests，基线失败会直接报 `Failed to run clean test` 并退出，不进入正常变异结论。
- 无关联测试的 mutant 被写为退出码 33。
- 变异子进程通过 `os.fork()` 启动，并使用 CPU/墙钟限制监控慢 mutant，因此运行环境必须支持 fork；官方文档明确 Windows 要在 WSL 中运行。
- 官方 README 的典型变异包括整数加一、比较边界改变、`break`/`continue` 替换；这类变异是测试断言强度的探针，不是生产缺陷本身。

来源：[官方 `__main__.py` 源码（run、clean baseline、fork 和 timeout）](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/__main__.py)。

### 2.2 源码路径和测试选择

3.7.0 推荐在 `pyproject.toml` 使用：

```toml
[tool.mutmut]
source_paths = ["src/"]
only_mutate = ["src/zf/core/security/signing.py"]
pytest_add_cli_args_test_selection = ["tests/test_security_signing.py"]
```

- `source_paths` 是 mutmut 需要复制、导入和追踪的源代码根目录；不宜误解为“只生成单个文件”。
- `only_mutate` 才是按文件收窄实际变异范围的配置（3.6+）；若版本不支持，应使用运行参数的 mutant 名称通配符并核对结果清单。
- `paths_to_mutate` 是旧名称，当前源码读取但会发出弃用警告，推荐迁移到 `source_paths`。
- `pytest_add_cli_args_test_selection` 专门承载测试路径、`-k`、`-m` 等选择参数；`tests_dir` 仍被读取但已弃用并会被拼接到该列表，不能依赖旧语义。
- `pytest_add_cli_args` 承载非选择类 pytest 参数，例如 `-c mutmut_pytest.ini` 或禁用会干扰的插件。
- 如果需要最小 pytest 配置，可复制一个隔离的 `mutmut_pytest.ini`，并通过 `pytest_add_cli_args = ["-c", "mutmut_pytest.ini"]` 指定；官方文档要求把该文件加入 `also_copy`。

对 ZaoFu 的建议是用 `source_paths = ["src/"]` 保证导入上下文完整，用 `only_mutate` 选 `signing.py`，用 `pytest_add_cli_args_test_selection` 只运行 `tests/test_security_signing.py`，不要把项目默认的全量 E2E、host、real-provider 测试带入单模块审计。

来源：[官方配置文档](https://mutmut.readthedocs.io/en/latest/#configuration)、[官方 configuration.py](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/configuration.py)。

### 2.3 `max_children`

`mutmut run` 接受 `--max-children INTEGER`。当前源码中该参数不是 `[tool.mutmut]` 的持久配置字段：不传时 `_run` 使用 `os.cpu_count() or 4`；它同时限制变异文件生成阶段的 multiprocessing Pool 和在途 mutant 子进程数量。对共享状态、低资源 CI 或验证结果稳定性，建议先使用 `--max-children=1`，确认结果后再增加并发；不能把并发数当作测试并发正确性的证明。

来源：[官方 `__main__.py` 的 `run`/`create_mutants` 实现](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/__main__.py)。

## 3. 推荐的单模块隔离执行方式（只给方案，未执行变异）

当前已验证基线本身可运行：在当前树执行 `uv run pytest tests/test_security_signing.py -q --no-cov` 得到 **5 passed**；把 `signing.py` 与该测试复制到临时源码副本，并使用现有 `.venv/bin/python`、`PYTHONPATH=<tmp>/src` 执行同一基线，也得到 **5 passed**。这证明目标模块和测试可以脱离 ZaoFu 全量 suite 做隔离基线。

未来在获得明确执行授权后，建议流程如下。以下命令不会修改 ZaoFu 工作树；执行前应把目标源码和测试提交或快照，并始终使用新的临时目录：

```bash
tmp_dir="$(mktemp -d /tmp/zf-mutmut-signing-XXXXXX)"
# 将当前提交的源码、tests/test_security_signing.py、pyproject.toml、uv.lock
# 复制/归档到 "$tmp_dir"，不把 .zf、state、运行日志或其他项目状态带入。
cd "$tmp_dir"

# 基线（必须先成功）
PYTHONPATH="$tmp_dir/src" /home/uisee/zaofu_v7/.venv/bin/python \
  -m pytest tests/test_security_signing.py -q --no-cov

# 未来的隔离 mutmut 命令；这里采用隔离工具环境，不把 mutmut 写入 ZaoFu .venv
uv tool run --from mutmut==3.7.0 mutmut run --max-children=1 \
  "zf.core.security.signing*"

# 结果查看
uv tool run --from mutmut==3.7.0 mutmut results --all true
uv tool run --from mutmut==3.7.0 mutmut show <mutant-name>
```

如果需要让 mutmut 使用现有虚拟环境的解释器，应在单独、可丢弃的虚拟环境中显式安装并锁定 `mutmut==3.7.0`；当前没有这样做。`uv tool run --from` 使用独立 tool 环境，产品源码依旧通过临时目录和 `PYTHONPATH` 提供。由于 mutmut 需要 fork，Linux 当前可行；Windows 原生不可行，应使用 WSL。

`mutmut run "zf.core.security.signing*"` 的通配符作用于 mutant/function 名称；它不能替代 `only_mutate` 的文件范围限制，所以结果中仍要核对实际 mutant 文件清单。每次运行都应保存：精确命令及 digest、Python/依赖版本、目标 commit、`mutants/*.meta`、`mutmut-stats.json`、stdout/stderr 和 TestKit 独立 pytest JSON/JUnit 报告。

来源：[官方通配符与运行说明](https://mutmut.readthedocs.io/en/latest/#wildcards-for-testing-mutants)、[官方 README 的隔离配置示例](https://github.com/boxed/mutmut/blob/main/README.rst)。

## 4. 官方状态和严格判别规则

### 4.1 mutmut 原生状态映射

当前官方 `stats.py` 将 mutant 的子进程退出码映射为：

| 原生状态 | 退出码（当前源码） | 含义 | ZaoFu 判定 |
|---|---:|---|---|
| `killed` | 1、3 | pytest 失败；3 是 pytest internal error 也被 mutmut 归为 killed | **不能直接通过**；必须独立确认是完整测试调用中的业务断言失败，而不是收集、fixture 或基础设施错误 |
| `survived` | 0 | 关联测试执行完且通过，未检测到变异 | 测试缺口；P0 目标模块应 fail 或进入明确的人工等价 mutant 复核 |
| `no tests` | 5、33 | pytest 没有测试，或 mutmut 没找到关联测试 | 覆盖/映射缺口；不能算通过，不能纳入“已验证”分母后隐藏 |
| `skipped` | 34 | 测试被跳过 | 未完成；只有有理由的显式排除才可记录，不得绿灯 |
| `suspicious` | 35 | 运行异常/可疑退出码 | harness failure，人工调查 |
| `timeout` | 36、24、-24、152、255 | 超过 mutmut 的 CPU/墙钟限制或 SIGXCPU 等 | 只有确认基线成功、确由该 mutant 触发受控超时才可算“检测到”；资源争用、外层 runner 超时归 harness failure |
| `caught by type check` | 37 | 被配置的 mypy/pyrefly 预过滤捕获 | 不是测试杀死；单独计数，不得伪装成测试通过 |
| `segfault` | -11、-9 | 子进程段错误/被 SIGKILL | 未完成或环境异常；不得算 killed |
| `check was interrupted by user` | 2 | 人工中断 | 未完成 |
| `not checked` | `None` | 尚未执行或无缓存结果 | 未完成 |

来源：[官方 `stats.py` 的完整 `status_by_exit_code`](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/stats.py)。

### 4.2 `incompetent` 与 baseline error 不是 mutmut 状态

- **`incompetent`** 是 Cosmic Ray 的术语：其官方文档把“测试 mutant 超时并被杀死”标记为 incompetent。mutmut 没有该状态；mutmut 的对应原生状态通常是 `timeout`，但不能机械互换。若报告接收外部工具结果，必须保留原工具名和原始状态。
- **baseline error** 不是 mutant verdict。mutmut 在变异前先运行 clean tests；若 clean tests 非零，直接报 `Failed to run clean test` 并退出。此时应判定为“基线/测试环境失败”，mutation score 无效，不能把后续缓存或任何 mutant 状态算作通过。
- 统计收集阶段若没有 active tests 或没有任何 mutant 被测试覆盖，也会提前退出；这是测试选择/导入路径问题，不是“所有 mutant 通过”。

来源：[Cosmic Ray 官方 Concepts（incompetent 定义）](https://github.com/sixty-north/cosmic-ray/blob/master/docs/source/concepts.rst#timeouts)、[mutmut 官方 `run` 的 clean-test 与无测试逻辑](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/__main__.py)。

### 4.3 ZaoFu TestKit 的独立判别

mutmut 的 `killed` 是一个必要但不充分的信号。尤其当前源码把 pytest internal error（退出码 3）归为 `killed`，所以 TestKit 不得只解析 `mutmut results` 或退出码。对每个目标 mutant，应由独立进程复核：

1. 基线 pytest 在同一临时副本、同一 commit、同一依赖下成功；
2. 目标测试确实被收集并执行，且没有 collection error、deselected、setup/teardown error；
3. 变异期间发生的是测试调用失败（例如明确的断言或契约不一致），而非 import、插件、路径、Docker、网络或资源错误；
4. timeout 仅在能证明是变异造成的受控行为时记录为“检测到”；
5. 完整保存变异 diff、测试报告、退出码、信号、时间限制和 stderr；
6. `survived`、`no tests`、`timeout`、`suspicious`、`not checked`、`segfault`、跳过和 type-check 结果都不能被默认转成绿色。

建议 TestKit 使用自己的 verdict：

```text
native_killed + 独立报告满足“call 失败且基础设施完整” -> killed_verified
native_killed 但 internal error/收集/fixture/环境异常 -> harness_failure
native_survived -> survived（测试缺口）
native_timeout -> timeout_detected 或 harness_failure（按证据区分）
native_no_tests -> no_tests（测试映射缺口）
baseline 非零/无 active tests -> baseline_error（本轮无有效 mutation score）
```

## 5. `run`、`results`、`show` 的用途和证据要求

### `mutmut run`

执行生成、统计收集、clean baseline 和逐 mutant 测试；可选位置参数是 mutant 名称或 Unix 通配符，`--max-children` 控制并行度。它支持中断后利用 `mutants/` 缓存继续，但缓存只能在目标 commit、配置、依赖和测试选择未漂移时复用。官方源码会对相关配置和依赖变化做缓存失效检测，仍建议 TestKit 每次记录 commit/config digest。

### `mutmut results`

从 `mutants/<source>.meta` 读取退出码并打印状态；当前实现默认隐藏 `killed`，使用 `mutmut results --all true` 才包含 killed。该命令是人类查看器，不提供 ZaoFu 所需的基线、测试调用和副作用证据，因此不能作为唯一 gate。

### `mutmut show <mutant-name>`

读取指定 mutant 的 diff，并在标题中显示当前映射状态；适合把 `survived` 或可疑结果转成测试补强任务。应同时保存 `mutmut show` 输出和 TestKit 对应的独立复现报告，禁止直接 `mutmut apply` 到工作树；官方文档要求 apply 前先确保源码已提交。

来源：[官方 CLI 实现（results/show）](https://raw.githubusercontent.com/boxed/mutmut/main/src/mutmut/__main__.py)、[官方工作流文档](https://mutmut.readthedocs.io/en/latest/#workflow)。

## 6. ZaoFu 的审计门禁建议

对 `EventSigner` 这类核心安全模块，建议把“完整审计”定义为：

- 基线无错误；
- 目标 mutant 总数大于零且全部有测试关联；
- `killed_verified` 覆盖所有非等价、可执行 mutant；
- `survived` 必须转成测试缺口或有审计人、代码位置和理由的等价 mutant 记录；
- `no tests`、`timeout`、`suspicious`、`not checked`、`segfault`、`skipped` 和无法归因的 internal error 均阻塞审计结论；
- type-check 捕获单独报告，不能提高测试杀死率；
- 目标 commit、配置 digest、测试选择、mutmut 版本和证据 digest 全部一致；
- 运行结束清理临时副本、进程、端口和锁，但保留不可变的报告副本。

这比“killed / total 的百分比达到某阈值”更适合 ZaoFu：百分比不能证明测试没有被错误选择、基线是否健康，也不能区分 mutmut 把 internal error 记为 killed 的情况。若确需展示分数，只对经过上述独立复核的有效 mutant 计算，并同时展示每类排除项数量和理由。

## 7. 与 ZaoFu S10 的接入建议

建议新增一个显式、非默认的 mutation audit case：

```text
准备：临时源码副本 + 固定 commit + signing.py + test_security_signing.py
基线：pytest --no-cov，必须完整通过
变异：mutmut==3.7.0，source_paths=src/，only_mutate=signing.py，max_children=1
收集：meta、mutmut-stats、results --all、show diff、独立 pytest JSON/JUnit
裁决：TestKit 逐 mutant 复核；native killed 不自动等于通过
输出：mutation-audit.json + markdown 摘要 + 每个 survivor 的 issue/test-gap 引用
```

mutmut 只负责生成变异和执行关联测试；ZaoFu TestKit 负责隔离 worktree、固定 state/commit、独立收集 pytest 结果、分类 baseline/harness/product 信号、绑定证据 digest，并在发现真实存活 mutant 时写入测试缺口，而不是修改 ZaoFu 生产代码。
