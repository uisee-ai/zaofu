# ZaoFu 全面测试工具与框架调研：ZaoFu TestKit

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/suites.py`。总体选型已进入实际 S0～S11 登记；T 层是风险分类，不按编号替代 S 阶段或产品 CI 层。

> 调研日期：2026-09-08  
> 调研目标：确认是否存在能够覆盖 ZaoFu 全部测试面的单一框架，并设计一套与当前仓库兼容、以确定性 `pass / fail` 为核心的工具组合。  
> 证据原则：仅使用工具官方文档、官方仓库和当前 ZaoFu 源码；不把 benchmark 分数、LLM judge 或 Agent 能力排名当作 ZaoFu 正确性测试。

## 结论

不存在一个框架可以独立、完整地测试 ZaoFu 的所有方面。

ZaoFu 同时包含 Python 确定性内核、事件与文件状态、异步 watcher、进程与 tmux、Provider 适配、外部网络集成、FastAPI、SSE/WebSocket 和浏览器 UI。任何单一框架都只覆盖其中一部分。真正可行的方案是：

> 以现有 `pytest + pytest-asyncio + Playwright + dev-verify` 为主干，直接编写 ZaoFu 领域 oracle；立即补充 `Hypothesis + Schemathesis + 局部 pytest-timeout`；用一层很薄的 ZaoFu TestKit 支撑代码统一场景、故障点、证据和清理；再按测试层条件引入 Testcontainers/Toxiproxy、k6、OWASP ZAP、mutmut 和多 Python 环境编排。

“框架组合”负责生成输入、驱动系统、隔离环境、注入故障和报告结果，但它不能替 ZaoFu 定义正确性。下列断言仍必须由 ZaoFu 自己编写：旧 attempt 不得覆盖新 attempt、Agent 自报完成不得成为规范终态、证据缺失不得完成、重放结果必须一致、重复受控动作不得重复产生外部副作用、Web 状态不得与 Canonical Store/EventLog 冲突。

## 一、当前仓库已经具备的主干

当前 `pyproject.toml` 已包含 `pytest`、`pytest-asyncio`、`pytest-cov`、`pytest-xdist`，并注册 `host`、`mock_e2e`、`perf`、`real_provider`、`serial` 等分层 marker；`web/package.json` 已包含 Playwright；`scripts/dev-verify.py` 已能按 config、event/store、orchestration、artifact handoff、provider/host 和 Web 等变更域规划测试闭包。因此 ZaoFu 不需要迁移主测试框架，也不需要另建一个与 pytest/Playwright 平行的大平台。

- pytest fixture 提供可组合的固定测试上下文和安全 teardown，参数化可以让同一个不变量覆盖多组输入：[pytest fixtures](https://docs.pytest.org/en/stable/explanation/fixtures.html)、[pytest 参数化](https://docs.pytest.org/en/stable/how-to/parametrize.html)。
- pytest-asyncio 可以在 pytest 内执行协程测试，并控制 event-loop scope，适合 watcher、SSE、transport 和异步恢复路径：[pytest-asyncio markers](https://pytest-asyncio.readthedocs.io/en/stable/reference/markers/)。
- pytest-xdist 可以把用例分发到多个 worker 以缩短执行时间；它只是执行加速器，不证明 ZaoFu 的并发正确性，涉及共享端口、tmux、锁和 state dir 的测试仍需显式串行或进程级场景：[pytest-xdist](https://pytest-xdist.readthedocs.io/en/stable/)、[分发模式](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)。
- pytest-cov/coverage.py 可报告未执行语句和分支；它能发现测试盲区，但高覆盖率不等于状态和副作用正确：[pytest-cov](https://pytest-cov.readthedocs.io/en/latest/)、[coverage.py 分支覆盖](https://coverage.readthedocs.io/en/7.12.0/branch.html)。
- Playwright 为每个测试建立隔离 BrowserContext，提供自动等待断言和失败 trace，适合真实浏览器中的 Kanban、project switch、SSE 和受控动作：[测试隔离](https://playwright.dev/docs/browser-contexts)、[断言](https://playwright.dev/docs/test-assertions)、[Trace Viewer](https://playwright.dev/docs/trace-viewer-intro)。

这些工具应继续作为 TestKit 的执行骨架，`dev-verify` 继续负责“某次变更应该运行哪些层”，而不是被外部框架替换。

## 二、推荐的 ZaoFu TestKit 分层架构

| 层 | 工具/框架 | 对 ZaoFu 的具体测试职责 | 不能独立证明什么 | 建议 |
|---|---|---|---|---|
| 领域测试与总执行器 | pytest + pytest-asyncio | 配置、EventLog、Store、Orchestrator、evidence gate、CLI/API、恢复与受控动作的确定性断言 | 不会自动知道 ZaoFu 不变量 | 保留，主干 |
| 测试闭包选择 | `scripts/dev-verify.py` + pytest marker | 按变更域选择 focused、serial、mock E2E、Web、host/provider 层 | 不是全系统覆盖证明 | 保留，主干 |
| 状态空间探索 | Hypothesis stateful | 生成 duplicate、乱序、stale attempt/generation、retry、restart、replay 动作序列，并在每一步检查不变量 | 不定义正确状态，也不适合真实 Provider 质量 | 立即小范围引入 |
| API schema/负向测试 | Schemathesis | 从 FastAPI `/openapi.json` 生成合法和非法请求，校验响应 schema、状态码、500、验证绕过；可生成多操作状态序列 | 不理解 EventLog/Store 权威关系、SSE 语义和业务权限 | 立即试点 |
| 挂死保护 | pytest-timeout | 只保护 watcher、tmux、锁、SSE、subprocess 等可能永久挂死的用例并输出线程栈 | 不是性能测试；硬退出可能跳过 teardown | 局部引入 |
| 浏览器 E2E | Playwright + Docker | DOM、用户操作、项目隔离、token gate、SSE 重连、console/network 和 trace | 页面成功不能证明 canonical state 正确 | 保留并强化真值链 |
| 精确故障注入 | pytest fixture + ZaoFu 命名 failpoint + subprocess | 在事件写入、Store 投影、artifact/digest、外部副作用/result event 等 commit point 崩溃并恢复 | 通用工具不能自动找到 ZaoFu 事务边界 | 立即自建薄层 |
| 外部服务隔离 | Testcontainers | 按测试启动一次性服务或 Toxiproxy 容器，测试 GitHub/GitLab、Feishu、WebSocket 等真实协议边界 | Docker 模拟不等于真实 SaaS 权限和限流 | 有对应集成场景时引入 |
| 网络故障 | Toxiproxy | 在 TCP 链路注入延迟、超时、限速、断连、reset、数据截断，检查重试、幂等和恢复 | 不模拟文件损坏、进程内部 crash 和语义错误 | 条件引入 |
| 性能/soak | k6 | 对 Web/API/SSE 建立并发、spike、stress、soak，使用 threshold 形成明确非零退出门槛 | 不验证 Kernel 语义；性能阈值需先有基线 | 发布/定时层 |
| Web/API 动态安全 | OWASP ZAP | 被动扫描、OpenAPI 导入、认证后爬取和隔离环境主动扫描 | 不能证明角色权限、EventLog 权威和 prompt injection 安全 | 发布/定时层 |
| 测试有效性审计 | mutmut | 修改比较符、布尔值、常量等，检查核心测试能否杀死错误实现 | 成本高，存活 mutant 也可能等价 | 定期、限定模块 |
| 安装兼容编排 | Nox 或 CI matrix + uv | 在 Python 3.11+、不同 extras、wheel/sdist 安装环境运行 smoke/契约测试 | 不增加业务行为覆盖 | 发布层，二选一 |
| 大规模混沌 | Chaos Toolkit | 在未来分布式部署中执行 steady state → 故障动作 → steady state → rollback | 当前本地单仓库进程模型下粒度过大 | 当前不引入 |

### 1. Hypothesis：覆盖手写 case 难以穷举的状态序列

Hypothesis 的 stateful testing 不是只生成参数，而是生成由多条 rule 组成的动作序列；invariant 会在每一步之后执行，失败时工具会尝试给出较短的复现程序。[Hypothesis stateful 官方文档](https://hypothesis.readthedocs.io/en/latest/stateful.html)、[RuleBasedStateMachine API](https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.stateful.RuleBasedStateMachine)

建议建立四个小模型，而不是把全部测试改成随机测试：

1. `EventLogModel`：append、duplicate、archive、replay、非法/半行事件；断言 event ID 唯一、顺序和重放一致。
2. `TaskAttemptModel`：dispatch、timeout、retry、late result、generation rollover；断言 attempt/generation 单调且 stale result 无权推进状态。
3. `OrchestratorModel`：fanout、fanin、rework、cancel、restart；断言依赖未闭合不推进、fanin 只发生一次、retry 有界。
4. `ControlledActionModel`：request、approve、execute、result 丢失、replay；断言一个 operation 最多产生一次外部副作用。

Hypothesis 发现的最小反例应固化为普通回归测试。CI 中固定 profile、example 数和 deadline；不能把随机 seed 当成唯一复现证据，必须保存动作序列、state dir 和事件日志。

### 2. Schemathesis：补 FastAPI/OpenAPI 的系统负向测试

ZaoFu 的 `src/zf/web/server.py::create_app()` 创建 FastAPI 应用，并挂载多个 APIRouter；FastAPI 默认提供 `/openapi.json`。[FastAPI OpenAPI URL](https://fastapi.tiangolo.com/tutorial/metadata/#openapi-url)

Schemathesis 能从 OpenAPI/GraphQL schema 生成正向和违反约束的请求，检查 server error、响应 schema、状态码和非法输入是否被接受；它还可以把 API operation 组成 state machine，并支持直接调用 ASGI 应用或访问真实 HTTP 服务。[Schemathesis 官方仓库](https://github.com/schemathesis/schemathesis)、[架构与执行阶段](https://github.com/schemathesis/schemathesis/blob/master/ARCHITECTURE.md)、[Quick Start](https://schemathesis.readthedocs.io/en/stable/quick-start/)

对 ZaoFu 的首批目标应限定为：

- 所有 GET 投影接口对边界 query/path 参数不返回未分类 500；
- mutation API 对缺失/伪造 token、错误 project scope、未知字段和错误 content type fail-closed；
- response body 与 OpenAPI schema 一致；
- 创建/查询/取消等有明确 OpenAPI link 的操作可做 stateful 测试；
- 每个生成 case 使用独立 project/state dir，禁止碰真实 dev state。

Schemathesis 不能测试 SSE 长连接的完整时序，也不会知道“API 显示 done 但 Store 未完成”是错误。因此每个关键 API case 仍要联合读取 Canonical Store/EventLog。

### 3. pytest-timeout：只作为挂死保险丝

pytest-timeout 的定位是终止死锁或永久挂起的测试并输出线程栈，不是精确计时或性能回归框架。官方还说明 thread 模式可能终止整个 pytest 进程并跳过 fixture teardown/JUnit 输出。[pytest-timeout 官方仓库](https://github.com/pytest-dev/pytest-timeout)

因此不要设置一个很短的全局 timeout。只给以下测试设置宽松的单用例或 session timeout：EventWatcher、SSE/WebSocket、tmux/provider subprocess、文件锁竞争和 crash/restart。进程级测试必须使用独立 `/tmp/zf-.../` state dir，并由外层 runner 做兜底清理。

### 4. Testcontainers + Toxiproxy：测试真实协议和网络失败

Testcontainers Python 为功能/集成测试启动一次性 Docker 容器，并在上下文结束时回收；generic `DockerContainer` 可运行任意镜像。[Testcontainers Python 官方仓库](https://github.com/testcontainers/testcontainers-python)、[Core API](https://testcontainers-python.readthedocs.io/en/testcontainers-v4.14.2/core/README.html)

Toxiproxy 让被测程序的 TCP 连接经过代理，并通过 HTTP API 注入 latency、timeout、bandwidth、slow close、reset peer、limit data、packet loss 等故障；其官方定位就是测试、CI 和开发环境中的确定性网络扰动。[Toxiproxy 官方仓库](https://github.com/Shopify/toxiproxy)

对 ZaoFu 可把 GitHub/GitLab HTTP、Feishu、WebSocket 或未来远端 Provider endpoint 接到代理，测试：

- 请求已在远端成功但本地 result event 尚未写入时断连；
- 429/5xx、连接 reset、半响应、超时和恢复；
- webhook 重复、乱序、延迟到达；
- retry 后 operation ID 不变，外部副作用不重复；
- 网络恢复后 canonical state 不倒退、不伪造成功。

注意：截至本次调研，Testcontainers Python 官方仓库没有成熟的专用 Toxiproxy module，相关请求仍处于 issue；应使用 generic `DockerContainer` 或直接由测试启动官方镜像，不能假设存在 Python 专用封装。

### 5. k6：把性能指标变成测试门槛

k6 面向 spike、stress、soak 等负载测试，自动采集请求指标；`threshold` 是显式 pass/fail 条件，违反阈值时返回非零退出码。普通 `check` 失败本身不会让整次执行失败，必须绑定 threshold。[k6 官方文档](https://grafana.com/docs/k6/latest/)、[Checks](https://grafana.com/docs/k6/latest/using-k6/checks/)、[Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)

ZaoFu 首批性能场景可以测：

- 多客户端查询 Kanban/Trace/Graph；
- SSE 重连风暴与 cursor replay；
- 大 EventLog/archive 下 API 延迟；
- 大 fanout 后投影读取；
- 长时间运行后的内存、文件句柄、进程/tmux/session 泄漏。

门槛必须同时包含错误率、p95/p99、断线恢复率、事件遗漏数和测试后资源清理。k6 只观察请求与指标，不能以“HTTP 200”代替 EventLog/Store oracle。Locust 也能用 Python 编写并分布式发压，但为避免维护两个负载框架，本方案优先选择 threshold/CI 语义清晰的 k6；除非团队明确要求测试脚本全部使用 Python，否则不同时引入 Locust。[Locust 分布式执行](https://docs.locust.io/en/stable/running-distributed.html)

### 6. OWASP ZAP：补 Web/API 动态安全扫描

ZAP Baseline Scan 会爬取目标并进行被动扫描，不发起主动攻击，适合 CI；规则可以配置为 FAIL/WARN/IGNORE。[ZAP Baseline Scan](https://www.zaproxy.org/docs/docker/baseline-scan/)

ZAP Automation Framework 可以用 YAML 定义环境、认证、OpenAPI/GraphQL 导入、spider、passive/active scan 和 job tests。[ZAP Automation Framework](https://www.zaproxy.org/docs/automate/automation-framework/)

建议分两层：

- 每次发布候选在隔离 Web 环境跑 baseline/passive scan；
- 定时在一次性环境跑 authenticated active scan，明确禁止连接真实 GitHub、Feishu 或真实项目 state。

ZAP 擅长 HTTP 层漏洞发现，但不会理解 ZaoFu 的角色授权模型、ControlledAction 幂等、事件签名和 prompt injection。因此这些仍需 pytest 安全矩阵直接断言“危险 sink 调用次数为 0”。

### 7. mutmut：验证测试能不能发现错误

mutmut 会对 Python 源码产生细小变异并运行 pytest，支持按模块/函数限定范围、增量结果和交互查看。[mutmut 官方仓库](https://github.com/boxed/mutmut)

只建议定期扫描高风险纯确定性模块，例如 schema、state transition、digest/path guard、action idempotency。不要一开始对全部 runtime、Web 和 Provider 代码运行：ZaoFu 测试规模大，fork、全局状态、异步和外部资源会使成本与噪声迅速上升。mutation score 是测试有效性审计信号，不是单独发布标准。

### 8. Nox/tox 与 Chaos Toolkit：为什么当前不作为主方案

Nox 和 tox 都能创建隔离环境并在多个 Python 版本/依赖组合执行测试；Nox 用 Python 声明 session，tox 侧重通用虚拟环境与测试命令编排。[Nox 多 Python session](https://nox.thea.codes/en/latest/tutorial.html)、[tox 官方文档](https://tox.wiki/en/latest/)

当前 ZaoFu 已有 uv、pytest marker、`dev-verify` 和 shell gate，立即引入 Nox/tox 会增加第二套编排配置而不会增加领域覆盖。发布期如果需要开发机一键复现 Python 3.11+、base/web/stream-json/feishu extras 和 wheel/sdist 安装矩阵，可选择 Nox；也可以直接使用 CI matrix + uv。不要同时引入 Nox 和 tox。

Chaos Toolkit 用“steady state hypothesis → probes/actions → 再检查 steady state → rollback”组织混沌实验。[Chaos Toolkit 概念](https://chaostoolkit.org/reference/concepts/)、[Experiment 规范](https://chaostoolkit.org/reference/api/experiment/)

这个模型适合未来的远程、多服务 ZaoFu 部署，但当前最关键的是 EventLog append、Store projection、sidecar/digest 和 external-action result 等代码级 commit point。命名 failpoint + 独立 subprocess 更精确、更快、更可重现。因此当前不引入 Chaos Toolkit；当 ZaoFu 出现多个长期运行服务、远端队列/数据库和正式 SLO 后再评估。

## 三、薄 ZaoFu TestKit 应该提供什么

TestKit 不是新的测试 runner，而是一组供 pytest、Schemathesis 和 Playwright 复用的领域设施：

```text
tests/testkit/
  scenarios.py       # 固定 Run/Task/Attempt/Project 场景
  oracles.py         # EventLog + Store + evidence + side-effect 联合断言
  failpoints.py      # 命名 commit point；默认关闭
  providers.py       # Scripted Mock Provider：成功、超时、截断、坏 JSON、迟到
  processes.py       # start/crash/restart、端口/tmux/pid 管理
  external_sinks.py  # GitHub/Feishu/git/shell 副作用 spy 与 operation 去重
  web_truth.py       # Store/API/SSE/DOM 真值链断言
  cleanup.py         # 检查遗留进程、tmux、端口、锁和临时 state
```

每个测试 case 使用统一合同：

```yaml
case_id: duplicate-wakeup-does-not-redispatch
tier: deterministic
given: 固定配置、事件、Store 和 mock 行为
when: 动作或故障点
then: 必须成立的 canonical state / event / evidence
forbidden: 禁止事件、状态和外部副作用
evidence: events.jsonl、Store snapshot、artifact digest、API/SSE/DOM、trace
cleanup: 不得遗留进程、tmux、端口、锁和临时状态
```

oracle 的事实优先级保持：Canonical Store → EventLog 因果链 → artifact/ref/digest → 结构化测试结果 → API/SSE → DOM/trace。低优先级投影不能推翻高优先级事实。

## 四、如何串成一次端到端系统测试闭环

一次完整 TestKit run 建议按以下顺序执行。前一层失败时仍保存证据，但可阻止更昂贵、可能产生外部影响的后续层：

1. **冻结输入与隔离环境**：记录 ZaoFu commit、`zf.yaml` digest、依赖锁、测试 case 版本；创建独立 project root 和 `/tmp/zf-<purpose>-<utc>/` state dir；使用 scripted provider 和 fake external sinks。
2. **配置与预检**：执行 config/schema、`zf validate --cold-start`、doctor/依赖检查；错误配置必须非零退出且指向准确字段。
3. **确定性 Kernel 层**：pytest 运行 EventLog、Store、Orchestrator、evidence、security 和 CLI/API contract；联合检查 Then、Forbidden 和 Evidence。
4. **状态空间层**：Hypothesis 执行事件/attempt/action 模型；失败时保存最小动作序列并转为普通回归 case。
5. **crash/restart 层**：在每个命名 commit point 杀死独立进程并恢复；断言恢复终态等于无故障 baseline，已完成副作用不重复。
6. **Mock 工作流 E2E**：从 `task.created` 或 Issue ingress 运行到 verify/judge/terminal；同时验证成功、失败、重复、乱序和重启路径。
7. **API 层**：Schemathesis 对隔离 FastAPI/HTTP 实例执行 schema、边界和负向输入；关键 case 回查 Store/EventLog。
8. **Web 真值链**：在 Docker Playwright 中执行 `EventLog → Store → API → SSE → DOM`，收集 trace、console、network 和截图；页面绿色但 canonical state 错误时必须失败。
9. **协议/网络恢复层**：条件启用 Testcontainers/Toxiproxy，注入 latency/reset/timeout/半连接，验证 retry、operation 幂等和恢复。
10. **安全层**：先跑 pytest 不可信输入→危险 sink 矩阵，再在隔离环境跑 ZAP baseline/active scan；任何未授权外部副作用为零。
11. **性能和长期运行层**：k6 按已批准 threshold 执行并发、spike、stress/soak；结束后再检查 EventLog 完整性和资源泄漏。
12. **发布兼容层**：CI matrix 或 Nox 在 Python 3.11+、不同 extras、wheel/sdist 干净安装环境运行最小 smoke；真实 Provider 只作为显式 canary。
13. **测试有效性审计**：定时用 mutmut 扫核心确定性模块；存活的高风险 mutant 转成回归测试，不要求普通提交等待全量 mutation。
14. **统一判定与清理**：聚合 JUnit/JSON、coverage、Hypothesis 反例、Playwright trace、ZAP/k6 结果及 ZaoFu evidence；任一 required case 失败、仅靠重试变绿、基础设施错误、digest 不一致或资源未清理，整次 run 均不算绿色。

系统级通过条件不是“所有命令退出码都是 0”这么简单，而是：

```text
全部 required case 的 Then 成立
+ 全部 Forbidden 未发生
+ canonical state、EventLog、evidence、API/SSE/DOM 一致
+ crash/replay 后终态与 baseline 一致
+ 未授权或重复外部副作用为 0
+ 没有 flaky retry、未分类错误和测试资源泄漏
```

## 五、工具与测试域覆盖矩阵

| ZaoFu 测试域 | 主工具 | 补充工具 | 完整 oracle |
|---|---|---|---|
| 配置、schema、启动 | pytest | Hypothesis、发布环境矩阵 | 返回码、字段路径、实际加载配置、state dir 文件边界 |
| EventLog | pytest | Hypothesis、命名 failpoint | 事件体、顺序、因果、去重、archive/replay、Store 投影 |
| Canonical Store | pytest | Hypothesis、subprocess crash | 原子性、锁、所有权、重放等价、损坏 fail-closed |
| Orchestrator | pytest/mock E2E | Hypothesis、pytest-timeout | dispatch/fanout/fanin/rework、attempt/generation、禁止终态 |
| Evidence/artifact/gate | pytest | failpoint、mutation audit | 正确 Task/Attempt/commit/ref/digest，缺失或陈旧证据拒绝 |
| 恢复和并发 | pytest/subprocess | Toxiproxy、soak | baseline 等价、无重复副作用、无状态倒退、无资源泄漏 |
| Provider/transport | pytest mock | Toxiproxy、真实 provider canary | briefing、session、transcript、超时/异常、canonical state 不受污染 |
| 安全/ControlledAction | pytest | Schemathesis、ZAP | token/scope/path/signature、operation 幂等、危险 sink 调用为 0 |
| Web/API/SSE | Schemathesis + Playwright | k6、ZAP | Store/EventLog/API/SSE/DOM 同源一致，console/network 无未分类错误 |
| 安装/兼容 | CI matrix + uv 或 Nox | Testcontainers | wheel/sdist、Python/extras、cold-start/CLI smoke |
| 性能/长稳 | k6 | 长时 mock run、系统资源采样 | 错误率、延迟、恢复、事件遗漏、副作用重复、资源泄漏阈值 |

没有一行可以只靠工具默认断言通过。每一行最后都必须落到 ZaoFu 领域 oracle。

## 六、最小引入顺序

### 阶段 0：不增加外部依赖

1. 保留 pytest/pytest-asyncio/pytest-cov/pytest-xdist、Playwright 和 dev-verify。
2. 先建设 `tests/testkit/` 的 scenario、oracle、scripted provider、external sink spy 和 cleanup。
3. 用普通 pytest/Playwright 补齐 Kernel 不变量、commit-point、真值链和 Issue 修复交付整链路。

### 阶段 1：立即引入的最小工具

1. `Hypothesis`：只覆盖 EventLog/TaskAttempt/Orchestrator/ControlledAction 四个状态模型。
2. `pytest-timeout`：只标记 watcher/tmux/锁/SSE/subprocess 用例。
3. `Schemathesis`：先对只读 API 和 token-gated mutation 做小范围试点；确认生成 case 全部隔离后再扩大。

### 阶段 2：条件引入

1. 出现需要真实协议的外部集成测试时，引入 Testcontainers + Toxiproxy。
2. 有经过基线确认的容量/SLO 目标时，引入 k6。
3. 有隔离的可攻击 Web 环境时，引入 ZAP。
4. 发布需要本地复现多 Python/extras 时，选择 Nox；否则继续 CI matrix + uv。
5. 核心确定性测试稳定后，定时对少量模块运行 mutmut。

### 当前不建议引入

- 新的“大而全”测试平台：与 pytest、Playwright、dev-verify 重复，仍不能生成 ZaoFu oracle。
- Chaos Toolkit：当前应先完成代码级命名 failpoint；等分布式部署后再评估。
- tox 与 Nox 同时引入：职责重复。
- Locust 与 k6 同时引入：选择一个负载框架即可，本方案优先 k6。
- 把所有测试放进 Testcontainers：纯文件/内存 Kernel 测试会因此变慢且更不稳定。
- 把 xdist 当并发正确性测试：它只能并行调度已有测试。
- 把 coverage、mutation score、ZAP alert 数或 k6 单一指标当作 ZaoFu 的总评分。
- Inspect AI、OpenAI Evals、AgentBench 等 evaluation/benchmark 作为 P0 发布门禁：它们可以提供任务组织灵感，但不能证明 canonical state、EventLog、evidence 和副作用边界正确。

## 七、最终选型

```text
主干（已有）：
  pytest + pytest-asyncio + pytest-cov + pytest-xdist
  Playwright + Docker
  scripts/dev-verify.py + 仓库 gate

领域核心（自建薄层）：
  scenario + oracle + named failpoint
  scripted provider + external-effect spy
  subprocess crash/restart + cleanup
  EventLog/Store/API/SSE/DOM truth-chain assertion

立即补充：
  Hypothesis
  Schemathesis
  pytest-timeout（局部）

条件/发布层：
  Testcontainers + Toxiproxy
  k6
  OWASP ZAP
  CI matrix + uv 或 Nox
  mutmut（定时、限定模块）

当前不引入：
  新的单体测试平台
  Chaos Toolkit
  tox + Nox 双重编排
  Locust + k6 双重负载框架
```

最终答案不是“找一个万能框架”，而是把每个工具限制在它擅长的观察面，并由 ZaoFu 自己的 deterministic oracle 把这些观察面闭合起来。这样才能形成可重复、可诊断、可进入合并门禁，而且不会把评估分数误当成正确性证明的系统测试闭环。

## 主要一手资料

1. [pytest 官方文档](https://docs.pytest.org/en/stable/contents.html)
2. [pytest-asyncio 官方文档](https://pytest-asyncio.readthedocs.io/en/stable/)
3. [pytest-xdist 官方文档](https://pytest-xdist.readthedocs.io/en/stable/)
4. [pytest-timeout 官方仓库](https://github.com/pytest-dev/pytest-timeout)
5. [pytest-cov 官方文档](https://pytest-cov.readthedocs.io/en/latest/)
6. [coverage.py 官方文档](https://coverage.readthedocs.io/en/latest/)
7. [Hypothesis 官方文档](https://hypothesis.readthedocs.io/en/latest/)
8. [Playwright 官方文档](https://playwright.dev/docs/intro)
9. [Schemathesis 官方文档](https://schemathesis.readthedocs.io/en/stable/)
10. [Schemathesis 官方仓库](https://github.com/schemathesis/schemathesis)
11. [FastAPI 官方 OpenAPI 文档](https://fastapi.tiangolo.com/tutorial/metadata/#openapi-url)
12. [Testcontainers Python 官方仓库](https://github.com/testcontainers/testcontainers-python)
13. [Toxiproxy 官方仓库](https://github.com/Shopify/toxiproxy)
14. [Grafana k6 官方文档](https://grafana.com/docs/k6/latest/)
15. [Locust 官方文档](https://docs.locust.io/en/stable/)
16. [OWASP ZAP 官方文档](https://www.zaproxy.org/docs/)
17. [mutmut 官方仓库](https://github.com/boxed/mutmut)
18. [Nox 官方文档](https://nox.thea.codes/en/latest/)
19. [tox 官方文档](https://tox.wiki/en/latest/)
20. [Chaos Toolkit 官方文档](https://chaostoolkit.org/reference/concepts/)
