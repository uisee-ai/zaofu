# ZaoFu 测试框架选型调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/runner.py`。pytest/证据/报告底座已有实现；后续工具选型须按能力与预算，不重建大型平台。

> 调研日期：2026-09-08  
> 调研范围：pytest、Hypothesis、pytest-xdist、pytest-timeout、Playwright、tox/Nox、mutmut/Cosmic Ray，以及适合本地 Python harness 的故障注入/chaos 工具。  
> 资料原则：只引用项目官方文档、官方仓库或项目自身配置。本文讨论的是如何**测试 ZaoFu 是否正确**，不是评价 Agent 或模型能力。

## 一、结论

“直接写测试函数”与“使用测试框架”不是对立选项：ZaoFu 当前的测试函数本来就运行在 pytest 或 Playwright Test 框架中。真正需要决定的是：**要不要另找一个大而全的框架，替换当前栈并试图一次覆盖整个系统？**

明确结论是：

> **使用框架，但不更换主框架，也不引入一个包办所有层次的“大框架”。保留 pytest + Playwright Test 作为双主框架，直接编写 ZaoFu 领域测试函数；建设薄的内部测试支撑层，再按已证明的缺口引入 Hypothesis 等专用工具。**

推荐路线：

1. **pytest 继续承载 Python Kernel/Runtime 主体测试。** 测试函数负责表达 ZaoFu 的状态转换、禁止结果和证据要求；fixture、参数化和 marker 负责装配与分层。
2. **Playwright Test 继续承载 Web 用户路径。** 它证明 API/SSE 投影最终在 DOM 中正确呈现，但不能代替 EventLog 和 Canonical Store 断言。
3. **`dev-verify.py` 继续做变更影响闭包与执行编排。** 它选择本次改动需要运行的测试，并调用现有 pytest/xdist/Web 命令；它不是新的测试 oracle，也不能证明全平台已经覆盖。
4. **建设薄的 ZaoFu 内部测试支撑层。** 只提供固定场景、领域断言、命名故障点、外部副作用 spy 和资源清理，不复制生产 Orchestrator/Store。
5. **增量引入专用工具。** Hypothesis 补复杂状态序列；pytest-timeout 只给可能挂死的独立 case 止损；mutation testing 做周期性测试有效性审计；tox/Nox、Toxiproxy、系统 Chaos 必须等对应需求真实出现后再引入。

一句话概括：

> 直接测试函数定义“什么算正确”，框架负责组织和执行，专用工具负责扩大输入、故障或环境空间；任何工具都不能替代 ZaoFu 自己的 canonical state、EventLog、evidence 和副作用 oracle。

### 为什么不是“不用框架”

如果不用测试运行框架而靠脚本和人工命令拼接，会失去统一的测试发现、fixture 生命周期、隔离、参数化、marker、失败报告和自动清理。pytest 官方将 fixture 定位为显式、模块化、可扩展的可靠测试上下文，并支持安全管理 teardown；这正适合 ZaoFu 的隔离 `state_dir`、临时 worktree、Mock Provider 和外部副作用记录。[pytest fixture 官方说明](https://docs.pytest.org/en/stable/explanation/fixtures.html)

### 为什么不是“再找一个完整框架”

ZaoFu 的风险跨越本地文件账本、canonical Store、进程、tmux、Provider、外部 API、SSE 和浏览器。没有一个通用工具同时理解：

- `events.jsonl` 的因果与追加不变量；
- Store 的状态所有权和 replay 结果；
- attempt/generation 的 stale 结果边界；
- evidence ref/digest 与完成 gate；
- ControlledAction 的授权、幂等和副作用次数；
- Web DOM 与后端 canonical truth 的一致性。

这些 oracle 必须写进 ZaoFu 测试函数和领域断言。新增“大框架”只会增加第二套收集、配置和执行规则，不能自动理解上述合同。

## 二、为什么 pytest 测试函数仍然必须是主干

pytest 官方将 fixture 定义为给测试提供“已定义、可靠且一致的上下文”，并强调 fixture 可以模块化、参数化和复用；`@pytest.mark.parametrize` 则可把同一行为合同应用到多组输入。[pytest fixture 官方说明](https://docs.pytest.org/en/stable/explanation/fixtures.html)、[pytest 参数化官方说明](https://docs.pytest.org/en/stable/how-to/parametrize.html)

这恰好对应 ZaoFu 的主体测试需求：

- fixture 创建隔离的 project root、`state_dir`、EventLog、Store、Mock Provider 和受控外部服务；
- 参数化表达同一个不变量在 success、failure、duplicate、stale、replay、restart 下都成立；
- 普通断言直接检查 canonical state、事件因果链、artifact ref/digest 和副作用调用次数；
- `monkeypatch` 可临时替换环境变量、属性、字典项或当前目录，并在测试结束后撤销修改。[pytest monkeypatch 官方说明](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

pytest 官方同时建议：对自己控制的代码，长期更安全的做法是把依赖显式传入，而不是大量深层 monkeypatch。因此，ZaoFu 的故障注入应优先在文件写入器、时钟、进程启动器、外部 action executor 等边界提供可替换依赖；monkeypatch 用于测试装配，不应演变成对白盒实现细节的全面耦合。[pytest monkeypatch 官方说明](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

一个合格的 ZaoFu 测试函数仍应明确包含：

```text
Given      初始 canonical state、events、attempt/generation 和隔离环境
When       执行一个动作，或在指定 commit point 注入故障
Then       必须出现的状态、事件、evidence 和退出码
Forbidden  不得出现的终态、重复事件、越权写入或外部副作用
Evidence   EventLog、Store snapshot、artifact/digest、API/DOM、调用记录
```

框架只帮助生成输入、调度测试或采集诊断，不能自动知道“旧 attempt 不得覆盖新状态”“Agent 自报完成不能成为 canonical terminal state”这类 ZaoFu 规则。

## 三、当前仓库已经拥有的基础

当前 `pyproject.toml` 已包含：

- pytest；
- pytest-asyncio；
- pytest-cov；
- pytest-xdist；
- `host`、`mock_e2e`、`perf`、`real_provider`、`serial` 分层 marker。

当前 `web/package.json` 已包含 `@playwright/test`，并已有单元测试、build、smoke 和 E2E 命令。仓库还拥有 `scripts/dev-verify.py`、`scripts/dev-premerge-gate.sh` 等影响闭包与门禁脚本。

仓库内证据位置：

- `pyproject.toml:13-52`：Python 测试依赖、默认 marker 排除与测试分层；
- `web/package.json:6-44`：Web unit/build/Playwright 命令与依赖；
- `scripts/dev-verify.py:123-154,517-658`：领域 sentinel、serial 文件和 xdist 调度；
- `scripts/dev-premerge-gate.sh:1-30`：秒级合同哨兵，并明确说明不替代全量回归。

当前没有发现 Hypothesis、pytest-timeout、tox、Nox、mutmut 或 Cosmic Ray 的项目依赖。因此，选型重点不是替换 pytest/Playwright，而是判断哪些补充能力值得引入。

另外，pytest-cov/Coverage.py 只能回答“哪些行或分支执行过”。Coverage.py 官方文档将 branch coverage 定义为比较可能跳转与实际跳转，pytest 官方也给出了 100% coverage 仍未捕获 bug 的示例；所以覆盖率适合发现盲区，不能替代 ZaoFu 的行为和不变量判据。[Coverage.py branch coverage](https://coverage.readthedocs.io/en/latest/branch.html)、[pytest 官方示例](https://docs.pytest.org/en/stable/explanation/types.html)

## 四、工具逐层判断

这里必须区分“框架”和“工具”所处的层次，否则容易把加速器、环境编排器或 Chaos 平台误认为测试正确性的来源。

| 层次 | 工具/做法 | 实际职责 | 对 ZaoFu 的决策 | 是否进入常规 merge gate |
|---|---|---|---|---|
| 测试运行框架 | pytest | fixture、参数化、断言、marker、插件承载 | **保留，Python 主框架** | 是 |
| 测试运行框架 | Playwright Test | 浏览器隔离、Web-first assertions、trace、Web E2E | **保留，Web 主框架** | 关键 Web 路径是 |
| 异步适配 | pytest-asyncio | 在 pytest 中运行和装配 asyncio 测试 | **保留现有用法** | 是 |
| 覆盖测量 | pytest-cov / Coverage.py | 测量行/分支是否执行，不判断行为是否正确 | **保留为发布测量层，不当 oracle** | 聚焦运行否；发布运行是 |
| 执行加速 | pytest-xdist | 多 worker 分发测试、缩短执行时间 | **保留；仅并行资源隔离良好的测试** | 是，但分层执行 |
| 性质测试工具 | Hypothesis | 生成值和动作序列、缩减失败样例 | **建议小范围新增** EventLog/Store/Orchestrator/Action 不变量测试 | 稳定后进入确定性 tier |
| 挂死止损 | pytest-timeout | 卡死/死锁时终止并输出诊断 | **有条件新增；只用于高风险独立 case/shard** | 限指定测试 |
| 内部故障注入 | pytest fixture + 显式 failpoint + subprocess/barrier | 在精确 commit point 触发失败并验证恢复 | **应建设，这是 ZaoFu P0 能力** | 是 |
| 内部测试支撑层 | `tests/support/` 场景、oracle、effect spy、cleanup | 复用领域装配与联合断言 | **应建设，但保持薄且非生产逻辑副本** | 被测试调用 |
| 环境编排器 | uv + `dev-verify.py` | 安装依赖、按变更选择和运行测试层 | **保留现有方案** | 是 |
| 环境编排器 | tox / Nox | 多 Python/多依赖环境和命令 session | **暂缓；当前与 uv/dev-verify 重叠** | 否 |
| 测试有效性审计 | mutmut | 变异代码，检查测试是否能发现错误 | **P2 小范围试点** | 否，周期性审计 |
| 测试有效性审计 | Cosmic Ray | session 化 mutation testing 和结果分析 | **不与 mutmut 同时引入** | 否 |
| 网络故障工具 | Toxiproxy | 对 TCP 链路注入延迟、断连、限速、reset、丢包等 | **仅实际网络链路出现缺口时按需引入** | 专用集成 tier |
| 系统 Chaos | Chaos Toolkit | 对运行中系统执行 steady-state/probe/action/rollback 实验 | **当前不引入** | 否 |

### 4.1 Hypothesis：值得引入，但只用于“不变量和序列空间”

Hypothesis 的 stateful testing 会生成的不只是数据，而是动作序列；`RuleBasedStateMachine` 可定义 rule、precondition、bundle 和 invariant，并在失败时给出缩减后的短操作序列。[Hypothesis stateful testing 官方文档](https://hypothesis.readthedocs.io/en/latest/stateful.html)、[Hypothesis stateful API](https://hypothesis.readthedocs.io/en/latest/reference/api.html#stateful-tests)

它非常适合以下 ZaoFu 对象：

- EventLog：append、duplicate、archive、query、replay 的组合；
- TaskStore：合法/非法状态迁移和旧 attempt/generation；
- Orchestrator：dispatch、fanin、rework、cancel、restart 的序列；
- ControlledAction：request、approve、apply、重复提交、恢复；
- Evidence：当前/旧 attempt artifact、digest 替换、缺失 ref 的组合。

推荐先建立内存 reference model，再让 Hypothesis 对 model 和真实实现执行同一组动作，持续检查：

```text
replay(state, events) == live_state
terminal state 单调且有合法 gate
stale result 不改变当前状态
同一 operation 的外部副作用次数 <= 1
每个 canonical mutation 都有合法 ledger 依据
```

但不要把所有既有示例测试改写成 Hypothesis：

- 协议中的关键回归仍需保留一个人类可读、名字稳定的直接 case；
- 简单数据边界用 `@given` 即可，官方文档也明确提示简单问题可能不需要 state machine；
- RuleBasedStateMachine 的 rule 通常不能直接接 pytest fixture，应通过构造工厂或实例初始化传入隔离目录和依赖；
- CI 可以使用固定 profile/derandomize 或记录 seed，但仍必须消除时钟、hash randomization、外部服务等额外非确定性。Hypothesis 官方说明固定 seed 只有在不存在其他非确定性来源时才能复现同一用例。[Hypothesis API](https://hypothesis.readthedocs.io/en/latest/reference/api.html)

结论：**引入 Hypothesis，新增少量高价值 property/stateful tests；不要替代直接 pytest 回归测试。**

### 4.2 pytest-xdist：用于加速，不用于证明并发正确

pytest-xdist 的主要能力是把测试分发到多个 CPU/worker；它支持不同调度模式和 `xdist_group` 分组。[pytest-xdist 分发文档](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)

ZaoFu 已将 pytest-xdist 纳入 dev extra，建议继续使用，但要遵守：

- 每个测试使用独立 `state_dir`、端口、tmux session、worktree 和环境变量；
- `serial`、固定宿主资源、真实 Provider 测试不与普通测试混跑；
- 参数化输入必须有稳定顺序。xdist 官方指出，各 worker 收集到的测试数量和顺序必须一致，无序 `set` 生成参数会导致错误；
- 并行模式下调试和 stdout/stderr 能力有限，失败应在单进程中复现。[pytest-xdist 已知限制](https://pytest-xdist.readthedocs.io/en/stable/known-limitations.html)

必须区分：

```text
xdist 并行运行很多彼此隔离的测试
!=
测试 ZaoFu 内部两个进程同时更新同一个 EventLog/Store
```

后者仍需专门编写 barrier、进程间同步和最终不变量断言。

### 4.3 pytest-timeout：用于挂死止损，不能当性能测试

pytest-timeout 官方定位是捕获死锁或无限挂起，不用于精确计时或性能回归；它还警告，最强硬的 thread 模式可能通过 `os._exit()` 终止整个进程，从而跳过 fixture teardown 和 JUnit 输出。[pytest-timeout 官方仓库](https://github.com/pytest-dev/pytest-timeout)

对 ZaoFu 的合理用法：

- tmux/provider 子进程等待；
- EventWatcher/stream-json 永不返回；
- lock 死锁；
- SSE/browser 等待；
- recovery loop 无界循环。

不建议一开始给整个测试套件配置很短的全局 timeout。ZaoFu 测试可能创建 tmux、Web 进程和临时 worktree，硬退出会阻断 cleanup。建议：

1. 先让被测 API 本身拥有显式业务 timeout；
2. 对挂死高风险 case 设置单测试 timeout；
3. 在独立 pytest shard/容器外再设置进程级 wall-clock 上限；
4. 无论失败与否，外层清理器检查 tmux、端口、锁和临时目录。

结论：**可引入，但作为最后一道止损，不作为正确性判据或性能指标。**

### 4.4 Playwright：继续使用，但只负责 Web 行为层

Playwright Test 默认让每个测试运行在独立 BrowserContext 中，隔离 cookies、local/session storage 等状态；官方建议测试用户可见行为而不是实现细节。[Playwright 测试隔离](https://playwright.dev/docs/browser-contexts)、[Playwright 最佳实践](https://playwright.dev/docs/best-practices)

它还提供会自动等待和重试的 Web-first assertions，适合 SSE 后最终一致的 DOM 状态；trace 可保存动作、DOM snapshot、network 和断言信息。[Playwright assertions](https://playwright.dev/docs/test-assertions)、[Playwright tracing](https://playwright.dev/docs/api/class-tracing)

ZaoFu Web 测试建议保留两类断言：

```text
后端 pytest：EventLog -> Store -> API/SSE payload
Playwright：用户动作 -> network/action -> 最终 DOM 与错误面板
```

页面显示成功但 canonical Store 或 EventLog 不满足条件时，整条测试必须失败。截图和 trace 是诊断证据，不是 canonical oracle。

根据仓库规则，浏览器 E2E 应在 Docker 中运行。Playwright 官方镜像包含浏览器及系统依赖，并建议固定镜像版本且使镜像版本与项目 Playwright 版本匹配；运行 Chromium 时推荐 `--ipc=host`，使用 `--init` 可减少僵尸进程问题。[Playwright Docker 官方文档](https://playwright.dev/docs/docker)

Playwright 可以重试并把“首次失败、重试通过”分类为 flaky。[Playwright retries 官方文档](https://playwright.dev/docs/test-retries) 对 ZaoFu merge gate，建议 retries 保持 0，或明确把 flaky 视作非绿色，避免重试掩盖竞态。

### 4.5 tox / Nox：它们是环境编排器，不是测试 oracle

tox 的职责是创建多个 Python 环境、安装依赖并运行测试命令；Nox 提供类似能力，但使用普通 Python 函数定义 session。[tox 官方手册](https://tox.wiki/en/latest/man/tox.1.html)、[Nox 官方文档](https://nox.thea.codes/en/stable/)

它们适合：

- Python 3.11/3.12/3.13 等兼容矩阵；
- base/web/stream-json/feishu extra 的干净安装；
- wheel/sdist 安装后 CLI smoke；
- lint、typecheck、docs 等多环境任务。

但当前 ZaoFu 已经使用 uv，并有 `dev-verify.py` 和 release/premerge 脚本。立即引入 tox 或 Nox会出现第二套命令编排和依赖定义，不能增加行为覆盖。

结论：**现在不引入。先在 CI 中使用 uv 建立明确环境矩阵；只有本地多 Python/多 extras 编排已经成为真实痛点时，tox/Nox 二选一，不能两者同时采用。**

### 4.6 mutation testing：适合验证测试是否有力量，但不作为日常门禁

mutation testing 会对被测代码做小改动，再运行测试；如果错误改动仍然通过，说明测试可能没有真正保护该行为。[Cosmic Ray 官方概念说明](https://cosmic-ray.readthedocs.io/en/latest/concepts.html)

mutmut 官方仓库说明它面向 pytest、支持增量缓存、并行运行和按模块/函数选择 mutant；当前版本要求 Python 3.10+，但运行平台需要 fork，Windows 通常需 WSL。[mutmut 官方仓库](https://github.com/boxed/mutmut)

ZaoFu 适合先用 mutmut 对少量纯确定性模块试点：

- event schema 与校验；
- task state transition；
- generation/attempt 比较；
- digest/path guard；
- controlled action 幂等键；
- replay reducer。

不建议直接对整个 `src/zf` 和近万测试做 mutation：成本高，Provider/Web/进程测试也会制造大量无意义超时。建议在一次性隔离 worktree 或容器中运行，并只挑核心模块；mutation 结果进入周期性测试质量报告，不进入每次 merge gate。

mutmut 与 Cosmic Ray 先选一个即可。当前 pytest 项目优先试点 mutmut；如果未来需要持久化 mutation session、独立 distributor 和复杂结果分析，再评估 Cosmic Ray。Cosmic Ray 官方将一次 mutation run 组织成 session，并记录到 SQLite 供恢复和分析。[Cosmic Ray session 官方文档](https://cosmic-ray.readthedocs.io/en/latest/concepts.html)

### 4.7 故障注入与 Chaos：先做确定性 failpoint，不上通用 Chaos 平台

ZaoFu 当前最需要的是精确验证事务边界：

- event append 前后；
- Store atomic replace 前后；
- artifact 持久化与 ref/digest event 之间；
-外部副作用成功与 result event 持久化之间；
- archive rotation 中；
- lock 获取后进程退出。

这类故障应通过 pytest fixture、显式依赖注入、barrier 和独立 subprocess 在确定位置触发。测试必须能说清楚“崩溃发生在哪一行逻辑之后”，并比较故障恢复结果与无故障基线。

Chaos Toolkit 面向运行系统的实验：先定义 steady-state hypothesis，再执行 probes/actions，最后检查系统状态和 rollback；这更适合已部署的多服务系统弹性实验。[Chaos Toolkit 核心概念](https://chaostoolkit.org/reference/concepts/)、[Chaos Toolkit 执行流程](https://chaostoolkit.org/reference/tutorials/run-flow/)

因此当前不建议用 Chaos Toolkit 替代 ZaoFu 的 pytest 故障注入。未来如果 ZaoFu 以多个远程服务部署，再用它做进程/节点/网络级演练。

Toxiproxy 专门模拟 TCP 网络条件，包括 latency、timeout、bandwidth、reset、packet loss 等，并支持确定性控制和随机 toxicity。[Toxiproxy 官方仓库](https://github.com/Shopify/toxiproxy) 它只适合实际经过 TCP 代理的 Provider、GitHub、Feishu 或 SSE 集成链路，不能模拟本地 JSONL 半写、原子替换失败、文件锁和进程 commit point。

## 五、推荐的 ZaoFu 分层测试栈

### L0：直接单元/契约测试——pytest

适用：schema、纯函数、状态机单步、event validation、path/digest、安全判断。

要求：快、无真实网络、无宿主依赖、一个失败对应一条明确合同。

### L1：参数化不变量测试——pytest

适用：同一个 Kernel 不变量在 success/failure/duplicate/stale/replay/restart 情景下验证。

建议维护场景表，而不是复制几十个结构相同的函数。

### L2：性质与模型状态机——Hypothesis + pytest

适用：事件和动作序列组合过多、示例用例难以穷举的核心模块。

要求：参考模型、固定资源预算、失败 seed/最小序列可重放；不访问真实 Provider。

### L3：组件和故障注入——pytest + subprocess

适用：EventWriter/Store/Orchestrator/ControlledAction 跨组件行为，以及 crash point、并发、恢复。

要求：隔离 state dir、明确进程 barrier、故障前后证据、强制资源清理。pytest-timeout 只做外层保险。

### L4：确定性 Mock E2E——pytest

适用：完整 workflow、Issue 到 candidate、重启恢复、EventLog 到 API 的真值链。

要求：Mock Provider/Mock GitHub/Mock Feishu，固定输入和副作用记录；这是核心 merge/release gate。

### L5：Web 浏览器 E2E——Playwright Test + Docker

适用：Kanban、SSE 重连、workspace 隔离、ControlledAction、loading/error/terminal DOM。

要求：后端 canonical 证据与 DOM 证据一起判定；浏览器隔离；trace on failure；不靠固定 sleep。

### L6：宿主和真实 Provider canary

适用：CLI 存在、启动、briefing、stream、resume、timeout、transcript、cleanup。

要求：显式 opt-in、隔离 state、预算和清理；只测试 ZaoFu 管理 Provider 的能力，不评价模型语义质量。

### L7：周期性质量与弹性审计

适用：mutmut、性能/soak、打包安装矩阵；未来可能包含 Toxiproxy 或 Chaos Toolkit。

要求：不阻塞普通开发；结果单独分类，发现幸存 mutant 或资源泄漏后转化为稳定回归测试。

## 六、建议建设的“薄测试支撑层”

不创建独立测试平台，只在 `tests/` 内建设可复用支撑代码，例如：

```text
tests/support/
  scenario.py        # 创建固定 Run/Task/Attempt/Event 场景
  oracle.py          # canonical state、事件因果链、evidence、禁止副作用断言
  failpoints.py      # 命名故障点和 barrier 控制
  processes.py       # 子进程启动、终止、诊断和强制清理
  providers.py       # Scripted/Mock Provider
  external_sinks.py  # GitHub/Feishu/git/shell 副作用记录器
  web_truth.py       # Store/API/SSE/DOM 真值链辅助断言
```

这层代码的边界必须保持清楚：

- 它只负责搭环境、收集证据和复用领域断言；
- 不重新实现生产 Orchestrator 或 Store；
- 不把复杂逻辑藏进 fixture，以免测试表面绿色但 oracle 本身复制了同一个 bug；
- 每个测试文件仍能直接看出 Given/When/Then/Forbidden。

## 七、最小落地方案与采用顺序

### 第一阶段：不增加新第三方工具，先用现有框架关闭 P0 缺口

1. Kernel 不变量参数化矩阵；
2. commit-point 故障注入矩阵；
3. EventLog → Store → API/SSE → DOM 真值链；
4. Issue → triage → Fix Run → evidence → candidate 的 Mock E2E；
5. 全入口 `state_dir` 一致性；
6. 不可信输入到外部副作用的安全矩阵。

这些仍然使用现有 pytest/Playwright。第一批内部支撑只需 `scenario`、`oracle`、`failpoints`、`cleanup` 四类能力；每种辅助能力都必须先有至少两个真实消费者，避免先造空框架。

最小可验收结果不是“创建了 `tests/support/`”，而是以下四条端到端事实已经被自动测试证明：

```text
旧 attempt/generation 的结果不能覆盖当前 Task
EventLog 写入后、Store 投影前崩溃，重启结果仍与无故障基线一致
同一 ControlledAction 重放不会重复外部副作用
页面 terminal 状态与 EventLog、Store、API/SSE 同时一致
```

完成后把新增测试登记到 `dev-verify.py` 的对应 domain/sentinel 或直接调用者闭包，让它们真正进入自动验证路径；只落测试文件、不接门禁不算形成系统闭环。

### 第二阶段：引入两个小能力

1. **Hypothesis**：只覆盖 EventLog/Store/Orchestrator/ControlledAction 的性质与序列；
2. **pytest-timeout**：只保护高风险挂死测试和独立 shard，并配外层清理。

pytest-xdist 已经存在，无需再次选型，只需完成资源隔离和 serial 分组。

Hypothesis 的首个试点应只选一个稳定的纯确定性边界，例如 EventLog replay 或 Task 状态迁移；通过运行时预算、失败重放和维护性验证后，再扩展到 Orchestrator/ControlledAction。pytest-timeout 则只有在已经观察到挂死风险的 case 上启用，不作为全局默认短超时。

### 第三阶段：测试有效性审计

在隔离环境对 3–6 个核心纯确定性模块试点 mutmut。先观察幸存 mutant 是否暴露真实断言缺口，再决定是否扩大。

### 第四阶段：出现真实需求才增加环境/Chaos 工具

- 需要持续验证多个 Python 版本和 extras 时，tox/Nox 二选一；
- 需要验证 TCP 断连、延迟和限速时，引入 Toxiproxy；
- ZaoFu 成为远程多服务部署后，再考虑 Chaos Toolkit。

## 八、最终建议

对 ZaoFu 当前阶段，推荐的答案是：

> **主要直接写 pytest/Playwright 测试函数；采用框架作为“放大器”，不让框架代替测试合同，也不新造一套测试平台。**

优先级如下：

```text
必须保留：pytest + Playwright
已经可用：pytest-xdist
建议增量引入：Hypothesis
建议谨慎引入：pytest-timeout
建议周期性试点：mutmut
暂不引入：tox/Nox、Cosmic Ray、Chaos Toolkit
按网络场景选用：Toxiproxy
```

是否引入一个工具，只看它能否关闭已证明存在的测试缺口：

- 没有新的可执行 oracle，不引入；
- 只是换一种写测试语法，不引入；
- 与 uv/dev-verify/Playwright 重复编排，不引入；
- 能发现示例测试难以覆盖的状态序列、挂死、网络故障或幸存错误，才进入对应测试层。

这能让 ZaoFu 的测试体系保持确定性、可解释、可复现，同时逐步覆盖普通测试函数难以穷举的状态空间。
