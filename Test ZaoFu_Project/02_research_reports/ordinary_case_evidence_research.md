# 普通 pytest 用例的清理前证据调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/case_evidence.py`。普通用例证据支撑及 runtime_case_evidence.py 已存在；不能再按早期建议认定尚未建设，也不假定所有 case 自动接入。

> 调研日期：2026-09-14。仓库读取基线：`142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`；`uv.lock` 和本地安装的 pytest 均为 `9.0.3`。本报告只读核对源码与第一方文档，没有实现 TestKit、运行测试、启动 Provider/runtime 或提交代码。以下新增接口均为建议，不代表已经存在。

## 结论与覆盖边界

建议采用「显式注册测试拥有的合成状态 + pytest 阶段报告 hook + 清理前有界候选快照 + 显式 checkpoint」。pytest hook 可以覆盖仍存活的 fixture 状态；无法恢复在测试体 `with`、fixture setup 内部异常展开或清理函数内部已经删除的文件。需要精确保存这些位置的状态时，必须由资源 owner 在删除之前调用 guard/checkpoint。

| 情况 | 可用捕获点 | 能诚实证明什么 |
| --- | --- | --- |
| call 断言失败、异常或 `pytest.fail()` | `pytest_runtest_makereport` 的 call 报告完成后 | 普通 pytest fixture 清理前、报告时仍存在的注册文件；测试体已清理的资源除外 |
| fixture setup 失败 | setup 报告后；注册应先于危险 setup | 已注册且仍存活的部分 setup 状态；未创建/未注册状态只能明确缺失 |
| teardown 先删除状态再失败 | teardown 开始前保存候选；teardown 报告将失败关联到候选 | 清理前状态，不能称为清理失败那一瞬间的状态 |
| teardown 中途改变状态再失败 | owner 在危险清理动作外围 guard，且在删除前 checkpoint | guard 位置还存在的状态；已删除字节只有较早候选 |
| xfail/XPASS | 读取 pytest 完成转换后的阶段 report | 原始异常、阶段和 xfail 原因；TestKit 仍不得计为 pass |
| timeout、SIGTERM、SIGKILL、`os._exit()` | runner 回收已原子写入的 checkpoint/报告 | 最后一次已完成捕获；可能陈旧或没有证据，不能保证 hook 执行 |

时序依据：pytest `runtestprotocol` 顺序执行 setup、可选 call、teardown；`call_and_report` 先执行该阶段，再调用 `pytest_runtest_makereport`，之后才 `pytest_runtest_logreport`。所以 call 报告早于 pytest teardown，teardown 报告晚于清理。[pytest 9.0.3 runner 源码](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/runner.py#L123)

## 现有 TestKit 的真实接缝

- [pytest_plugin.py](../../../../tests/testkit/pytest_plugin.py) 已有 `pytest_runtest_logreport(report)`，按 nodeid 保存阶段 outcome、duration、wasxfail、longrepr；只有 collection finish 和 session finish 调用 `_write`。因此超时不仅会失去状态文件，还可能失去此前尚未落盘的阶段报告。没有 `makereport`、pre-teardown 或普通状态注册接口。
- [runner.py](../../../../tests/testkit/runner.py) 的 `run_pytest(*, project_root, stage="all", output_dir, timeout_seconds=900, nodeids=None)` 显式加载该 plugin，设置 `ZF_TESTKIT_MODEL_EVIDENCE`。子进程结束后 bundle 收集报告、JUnit、stdout/stderr 及 `model-evidence/*/*.json`。这能保存报告字节，却不能从报告里的临时路径复原状态。
- [model_evidence.py](../../../../tests/testkit/model_evidence.py) 的 `ModelEvidence(root, model_name, source, reference, settings, max_files=128, max_bytes=4 MiB)` 为已接入的模型 guard 保存 first/latest JSON；`_files()` 包含原始字节、相对路径、长度、SHA-256，拒绝 symlink/特殊文件/超限。它没有普通 pytest 注册能力；guard 只捕获 `Exception`，也不是通用 teardown、constructor 或 framework 异常拦截器。
- [scenarios.py](../../../../tests/testkit/scenarios.py) 的 `isolated_project(root, run_id, task_id, operation_id, attempt, generation)` 返回 `IsolatedProject(root, state_dir=root/"state", ...)`，可提供已知身份和显式 state 目录；它没有 `request`、注册表或捕获回调。参数 root 来自调用者，函数本身没有证明任意传入目录均归本测试所有；不能把调用该函数等同安全授权。
- [test_testkit_runner.py](../../../../tests/test_testkit_runner.py) 已验证 xfail/strict XPASS 非绿、xfail 异常文本、setup error、skip、collection error、超时、顽固子进程回收、缺失 teardown、输出不可复用、错误报告结构等；尚不能证明普通注册状态在清理前保存。已有 timeout `0.1s` 用例也没有证明测试体已经开始，新的 checkpoint 回收用例需先同步 ready。

`RunnerResult` 的 `timed_out/session_finished/verdict`、`write_failure_bundle` 的副本/ref/digest 与输出目录独占 claim 可直接延续；新增 case 快照应作为单独普通证据输入，不伪装成模型动作回放。bundle 复制发生在子进程退出后，必须复制之前已经保存到输出目录的字节。[runner](../../../../tests/testkit/runner.py)、[failure_bundle](../../../../tests/testkit/failure_bundle.py)

## Hook 与 finalizer 顺序的实施含义

建议新增 `pytest_runtest_makereport` wrapper，使用公开 `item.stash` 保存注册信息与阶段事实。`@pytest.hookimpl(wrapper=True, tryfirst=True)` 在 `yield` 返回之后观察报告，并返回原 report；外层 wrapper 的后半段晚于内层执行。保持现有 `logreport` 汇总，通过一次明确的写入路径逐阶段原子 flush。不要返回自行创建的 report 去截断其他实现。`tryfirst` 只是排序约束，第三方同优先级 wrapper 仍需兼容测试。[pytest hook wrappers 与排序](https://docs.pytest.org/en/stable/how-to/writing_hook_functions.html#hook-function-ordering-call-example)、[Stash API](https://docs.pytest.org/en/stable/reference/reference.html#stash)

xfail 插件在自己的 `makereport` wrapper 后半段修改 report：预期异常可以成为 skipped + wasxfail；非 strict XPASS 为 passed + wasxfail；strict XPASS 为 failed，longrepr 带 `[XPASS(strict)]`，不一定具有 wasxfail。它并非只处理 call，setup/teardown 异常也可能被转换。捕获选择应基于转换后的事实，至少涵盖 failed、wasxfail，而不是只看 `call.excinfo` 或 `report.failed`。[pytest 9.0.3 skipping 源码](https://github.com/pytest-dev/pytest/blob/9.0.3/src/_pytest/skipping.py#L276)

当前 plugin 的 setup skip/xfail 没有 call 时通常归 `not_run`，call 通过而 teardown xfail 时也可能归 `not_run`；call failed 优先于 setup/teardown error。它仍 fail-closed，但阶段语义不充分。实施时应保留全部 phase 原始事实，给 setup/teardown xfail、普通 setup skip 和多阶段失败增加明确归类测试，不以调整单一 status 丢弃第二个失败。

teardown 入口使用 `pytest_runtest_teardown` wrapper 的 **yield 之前** 保存候选；yield 后或 teardown makereport 只关联 outcome。即便 call 已成功也必须先保存候选，才能覆盖随后清理删除后失败；所有阶段确认通过再丢弃候选。若要求「成功测试绝不发生任何快照写入」，就无法通用覆盖后续 teardown 删除后失败；两者需在契约上明确取舍。

不能使用 `pytest_fixture_post_finalizer` 作为清理前钩子：它在 fixture 全部 finalizer 后才调用。pytest 会收集多个 finalizer 异常并继续执行剩余 finalizer，因此第一个清理错误也不会天然阻止其他清理删除证据。[pytest fixture finish 源码](https://docs.pytest.org/en/9.0.x/_modules/_pytest/fixtures.html#FixtureDef.finish)

finalizer 遵循后注册先执行；更容易踩坑的是 yield fixture：pytest 在首次 yield 成功返回后才注册生成器清理 finalizer，因此 fixture 在 yield **之前** `request.addfinalizer(capture)`，capture 反而会在 yield 后清理逻辑之后运行。使用显式 owner 清理包装或 teardown 入口 wrapper，避免依赖这种注册偶然顺序。fixture 在 yield 前失败时，其 yield 后代码不执行，但已成功初始化的其他 fixture 仍会清理。[pytest finalizer 顺序](https://docs.pytest.org/en/stable/how-to/fixtures.html#note-on-finalizer-order)、[pytest yield fixture 错误处理](https://docs.pytest.org/en/stable/how-to/fixtures.html#handling-errors-for-yield-fixture)

`pytest.fail.Exception` 经 `Failed -> OutcomeException` 继承 `BaseException`，普通 `except Exception` 不覆盖它，`pytest.xfail()` 也有相应控制流异常。owner guard 若采用 `except BaseException`，只能有限取证后裸 `raise`；不能吞掉 skip、xfail、KeyboardInterrupt、SystemExit，也不能把这些全部重标成产品失败。报告 hook 负责最终分类，原始异常和 traceback 必须保留。捕获 helper 自身的错误应另记 `capture_error`，不能替换原异常。[pytest outcomes 源码](https://docs.pytest.org/en/stable/_modules/_pytest/outcomes.html)

## 显式注册与有界快照契约

建议仅新增薄 fixture `testkit_case_evidence`，提供 `register(...)` 与 `checkpoint(label)`/owner guard；此处接口名称是设计建议。注册结构保持普通 Python 数据，不引入生产 runtime 状态机：

- 必填 pytest nodeid 来自 request；root/files 来自测试显式声明。使用测试创建的私有临时子目录并验证所有权边界，先注册再做会失败的 setup。默认只支持 function scope；module/session 共享 root 必须独立设计责任归属，不能隐式把最后一个 case 当 root owner。
- case 元数据显式提供输入、期望、禁止项、task/run/operation/attempt/generation（适用者）。不能从日志、文件名或环境猜 run ID；不适用/尚未初始化用 null 加原因。来源文件、运行命令、提交和依赖摘要与 runner 已有身份绑定。
- 注册文件清单或明确的合成 root 相对范围，包括 state 内原始 ledger/store/artifact 字节；保留损坏字节，不能只做成功解析后的 JSON 导出。文件原路径仅是来源标签，证据是输出目录中的实际副本、ref、字节数和摘要。
- 强制每 root、每 case、每 run 的文件数、总字节、遍历目录/条目数和捕获次数上限；仅有限 slot（例如失败快照、pre-teardown 候选、latest checkpoint）。序列化 metadata 也有预算。超限不得默默截断后声称 complete；给出 `partial/unavailable`、失败原因及已捕获项。
- output 与输入 root 分离，并拒绝输入中包含 output；避免递归捕获自身。拒绝路径逃逸、symlink（包括父路径）、特殊文件；动态文件标为不一致或重试有限次数。`resolve()+is_relative_to()` 只能辅助检查，不自动消除并发替换的 TOCTOU；需要遍历和打开时的无跟随约束或明确限定无并发改链的合成 fixture。不能声称现有 ModelEvidence 已解决全部此类问题。
- 只接受本次测试拥有且已声明无真实秘密的合成数据，不自动枚举 `item.funcargs`、整个 tmp_path 树、仓库、真实 `project.state_dir`、全局 workspace registry、环境变量、Provider 配置或用户目录。临时目录位置本身不是「无秘密」证明；测试凭据使用明确公开假值。秘密排除导致缺少所需字节时，标明取证范围和完整性，不能把脱敏内容称为原样可重放。

这些是基于现有 [ModelEvidence 有界文件捕获](../../../../tests/testkit/model_evidence.py) 与 [bundle 原子副本契约](../../../../tests/testkit/failure_bundle.py) 的实施建议；不是 pytest 自动提供的安全保证。初次实现不必统一重构模型 helper，但应复用低层原子写入/hash，保持普通证据 schema 与模型动作 schema 分离。

建议区分 `capture_point`（setup-report/call-report/pre-teardown/owner-guard/checkpoint）、`observed_phase`、`outcome`、`complete_for_declared_scope` 与 `capture_error`。完整字节覆盖不意味着时间上精确等于失败现场，更不意味着证明了所有产品验收条件。

## 超时与硬退出

runner 当前以新进程 session 启动 pytest，并在 timeout 后调用 `terminate_process_group`；POSIX 先 SIGTERM，之后若进程组仍在则 SIGKILL。SIGKILL 不可捕获，Python signal handler 执行也可能被长时间 C 运算延迟；`os._exit()` 不执行 Python 清理。不能依赖 sessionfinish、fixture finalizer 或信号 handler 在这类退出时保存现场。[本仓 cleanup](../../../../tests/testkit/cleanup.py)、[Python signal 执行规则](https://docs.python.org/3/library/signal.html#execution-of-python-signal-handlers)、[SIGKILL](https://docs.python.org/3/library/signal.html#signal.SIGKILL)、[os._exit](https://docs.python.org/3/library/os.html#os._exit)

最小可靠策略：注册和每阶段报告原子持久化；已注册状态在 setup 成功后保存有界 checkpoint，高风险阻塞动作前由测试显式 checkpoint。父 runner 只回收自己的 output 中已完成文件，忽略未提交 `.tmp`，验证 ref/digest，记录 timed_out 与 snapshot 的时间位置。尚未注册、初始化过程中被杀、最后一次 checkpoint 后发生变化都必须明确表现为证据缺失或陈旧；即便 checkpoint 完整，verdict 仍是 harness_failure。

可选后续增强是父进程读取显式注册表，在终止测试进程组后对残存合成 root 做有界副本；只能读本次 test owner 的白名单目录，不能扫描 runtime。它无法执行子进程里的 Python reference callback，也无法恢复已删除文件；并发子进程、退出时清理和断写文件影响一致性。默认首版用已持久化 checkpoint，可少一个跨进程路径信任和读取窗口。

## 有限实施顺序与验收

1. 定义注册、scope/预算、普通证据 schema 和完整性语义；实现薄 helper 与显式 fixture → verify：只读注册白名单，原始字节/ref/digest 正确，拒绝逃逸、symlink、特殊文件、过大 metadata/文件树，捕获错误不覆盖原错误。
2. 接入 makereport、pre-teardown wrapper、逐阶段 flush → verify：普通 assert、`pytest.fail`、setup 部分初始化失败、call 失败后 fixture 删除均保存对应字节；通过 case 清除自有候选且不影响状态清理。
3. 验证清理边界 → verify：teardown 删除后 raise 仍有 pre-teardown 候选；先改变再失败的 guard 记录时间点；多个 finalizer 报错不吞异常；测试体 `with TemporaryDirectory` 删除使用 owner guard，缺少 guard 明确 unavailable。
4. 验证 outcome 转换 → verify：call/setup/teardown xfail、`xfail(run=False)`、strict/non-strict XPASS、普通 skip、call 与 teardown 同时失败、pytest.exit 缺失 teardown 的原始阶段事实与最终非绿结论一致。
5. runner 收集普通快照并核验 claim/ref/digest → verify：副本在 fixture 消失后仍可读；输出复用拒绝；捕获缺失/损坏只影响 evidence 状态或 harness verdict，不制造产品 pass。
6. 同步 ready 后测试超时与硬退出 → verify：先 checkpoint 再阻塞/SIGTERM/SIGKILL/`os._exit`；父进程回收已提交文件、无半写快照成功声明、非绿且进程组无残留；注册前杀死明确 unavailable。使用纯 Python 合成子进程，不运行真实 Provider。
7. 先接入少量不同资源 owner 的代表普通用例，再逐批适配现有独立 tmp roots → verify：覆盖矩阵逐项标示 registered/guarded/checkpoint-only/unavailable，不把共用 `isolated_project` 的接入外推到全部测试。

聚焦验证闭包为普通证据 helper 新测试、[test_testkit_runner.py](../../../../tests/test_testkit_runner.py)、bundle/evidence 原有测试和已显式适配的普通 case；若提取修改 ModelEvidence 共用原语，追加其直接模型捕获回归。pytest xdist、rerun、自定义 runtest protocol 和 async fixture 的插件排序/身份隔离未在本次执行验证；首版应明确支持范围，不能仅凭 nodeid 键就宣称多 worker/多 attempt 安全。本次未运行任何测试，以上为待实施验收清单。

## 主线程实施补充：局部故障注入与报告持久化交互

### S3/S4 普通生产行为适配前核对

本轮只读核对 `test_zaofu_testkit_s3.py`、`test_zaofu_testkit_s4.py`、`scenarios.py` 与 fanout `_state/_start`。S3 fixture 为 function-owned tmp/state、mock role + dry-run tmux，S4 普通恢复同构；两个真实 dispatch crash 测试同时创建 baseline/fault roots，必须一起留存。`IsolatedProject.run_id` 在这些测试中主要是输入 correlation，不能据字段名冒充已准入 Run；初始 attempt/generation/operation 未创建时用 null+原因，注明身份时点为动作前。fanout 的 child/operation 身份由内核运行产生，初始元数据不猜值，原始状态负责保留实际身份。

`orchestration_context` 还被 terminal-store 模块导入，因此不在共享 fixture 上隐式给所有消费者套同一描述；在所选普通测试内显式注册，保留既有断言。S4 两树测试改用一个 test-owned 私有父目录容纳 baseline/fault，不改真实子进程的动作或 oracle；在运行前 checkpoint，退出后先保存准确 args/returncode/stdout/stderr，再执行原断言。私有 baseline/fault 状态和 receipt 一起捕获，不能只留消失路径。

安全边界仍是合成数据与显式所有权：这些 fixture 不启动真实 Provider/宿主 tmux，不调用 SpawnCoordinator 的真实 credential provisioning，也不加载产品项目。capture 不收集父 pytest 环境或真实 workspace。完成适配后应检查原有红灯仍出现、取证无路径/容量错误、原始文件及 source SHA 可回读，再与之前全部闭包联调；不能据几个普通节点宣称所有域已自动覆盖。

### 提交窗口与清理补查（2026-09-14，实施前决策）

本地 pytest 9.0.3 `runner.py::call_and_report/get_reraise_exceptions` 明确重抛 Exit，故 `pytest.exit(returncode=0)` 也可能没有 call/teardown report；不能凭进程 0 声称完成。`fixtures.py::FixtureDef.finish` 会依次运行 finalizer 并聚合多个异常，故应验证全部错误保留以及 pre-teardown 与 owner-guard 的不同时间位置。使用已有官方来源和本地锁定版本，不依赖新的外部工具。

当前 CaseEvidence 的原子文件替换先于 snapshots/ref 写回 pytest 报告；首个注册快照或覆盖 checkpoint 后硬退出，都可能存在“完整文件但没有匹配报告引用”。父进程现只遍历报告 ref，因而会漏掉可保留的现场。有限方案：注册声明先落报告；父进程只在已声明 case 的 hash 目录探测六个固定槽，沿用 no-follow、大小、schema、nodeid、capture point、嵌入字节摘要检查，忽略 `.tmp`、陌生目录和未注册源。新发现或摘要不匹配的完整文件只进 `ordinary_diagnostics`，标明无匹配报告绑定；绝不改写 case phase、registered/expected digest 或把它变成验收通过证据。出现这类材料维持 harness_failure；没有快照时明确缺失。

这不是从外部目录恢复 runtime，也不承诺快照真实性认证、跨文件事务一致或精确中断时刻。父进程新算 SHA 只用于诊断副本完整性，不是替代子进程原先的 ref。用真实子进程在 first snapshot/new slot/slot replace 后、replace 前未提交 tmp、注册前分别退出来证实；普通错误、所有旧模型及三例普通生产适配需同闭包联调。

后续原始取证实施已遵循前述时序，详见 steps.md 的最新 K4 节。有限支持 POSIX 单进程普通 function scope；源遍历与创建输出父目录使用 dir_fd/no-follow，输出目录由 runner 独占，不声称对抗任意并发改名/篡改输出的恶意测试代码或获得跨文件原子一致性。第一轮代码复核发现 Path.mkdir 先创建再检查祖先 symlink，会在合成 outside 目录中留下新目录；新增负向用例证明后改为逐级 no-follow 创建。另以原始坏 record/缺少 snapshot 的反向用例补父进程 fail-closed 分类，不替换原断言。没有新增生产规则或修改 src。

快照使用六个固定槽，保留明确时点，不追溯已删除字节；身份字段要求 run/task/attempt/generation/operation 都显式提供，未初始化项 null+reason。只有原 phase 都通过且无 capture error 才逐文件核对摘要后清除自有候选；不做递归输出删除。当前仅适配三个已有普通行为节点，其余方法与插件兼容不是已实现结论。44 个新增契约及 26 入口联调回执见 steps.md；原文未运行测试的声明仅描述最初调研时点。

前置阶段的五个硬退出探针已按 TDD 验证原先阶段事实丢失，再实现 logreport 原子 flush，原分类优先级保持不变。首次联调 `/tmp/zf-phase-facts-integration-glyhwh_e` 在模型捕获的 `write_error` 例发生 INTERNALERROR：该例替换 `tests.testkit.evidence.os.replace`，实际是进程共享 `os` 模块，连 plugin 的 `Path.replace` 也被替换。调用栈落在 `pytest_plugin._write → pathlib.replace → fail_replace`，属于测试故障域过宽，不是生产问题。

处理方式：保留真实模型捕获路径，只在目标位于该例声明的 output 时注入 OSError，其他目录委托原 os.replace；额外断言原异常 note 确实含 `injected replace failure`，不能靠未触发注入而通过。报告器不屏蔽 OSError，不修改 ModelEvidence 或生产写入行为。S0 同时发现覆盖矩阵漏登新增测试，补齐登记后重跑相同闭包。旧报告的 98 passed/1 failed/244 not_run、session_finished=false/harness_failure 保留，证明部分报告没有将未执行项算绿；这仍不代表 fixture 原始状态已自动保存。
