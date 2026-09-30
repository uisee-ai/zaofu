# ZaoFu 及其产品项目测试方案（P0）

> 文档定位（2026-09-30）：这是两套方案拆分前的 P0 范围草案，保留方法背景，不再作为当前执行队列或完成度分母。ZaoFu 自身以 [ZaoFu_TestKit.md](ZaoFu_TestKit.md) 为准，生成产品以 [ZaoFu_Product_TestKit.md](ZaoFu_Product_TestKit.md) 为准；本文件 T0～T5 是历史分层，不与当前 S0～S11 或产品 T0～T3 逐号等同。Provider 两项按操作者要求跳过，Feishu、`/issue-merge` 与降级项不恢复实施。历史示例不构成运行授权。

## 0. 目标和术语边界

本方案的主线是测试，不是模型评估。

**测试（Test）**：针对一个明确的软件行为或不变量，给定固定输入、环境和预期结果，执行后得到可复现的通过/失败判定。测试结果可以作为 CI、Fix Run、verify gate 或 merge 的证据。

**评估（Evaluation）**：在多个模型、Provider、版本或策略之间进行比较，汇总成功率、成本、时延或一致性，用于判断相对能力和趋势。评估结果不能替代某个具体功能的测试，也不能单独证明 ZaoFu 内核或产品功能正确。

因此本 P0 的原则是：

- 默认测试必须有明确的 oracle（预期事件、状态、返回值、文件或 UI 行为）；
- 默认测试使用固定 fixture 和 mock provider，保证可重放；
- 测试失败可以阻断修复和合并；
- Provider 横向比较、排行榜和大规模 benchmark 延后到测试底座稳定之后。

## 1. 测试范围

需要同时测试三个边界：

1. **ZaoFu 本身**：配置、CLI、Kernel、Runtime、EventLog、Store、Controlled Action、恢复机制和 Web/API。
2. **ZaoFu 生成的产品项目**：产品功能、API、数据、前端、E2E、发布和回滚。
3. **ZaoFu 与产品的连接**：source root、state dir、TaskContract、测试命令、evidence、verify 和 candidate 是否正确串联。

测试成功不是“agent 说完成”，而是：

~~~text
固定输入
  -> 运行组件或工作流
  -> 产生事件、状态和产物
  -> 执行确定性断言
  -> 通过或失败
~~~

## 2. P0 测试层级

| 层级 | 内容 | 是否阻断 merge |
| --- | --- | --- |
| T0 | 配置、schema、静态检查、依赖和拓扑 | 是 |
| T1 | 单元、契约、EventLog、Store、Controlled Action | 是 |
| T2 | mock provider 的工作流、状态机、fanout、rework、retry | 是 |
| T3 | 产品 API、数据库、Playwright Web E2E | 是 |
| T4 | restart/resume、超时、非法输入、权限和副作用测试 | 是 |
| T5 | 真实 Codex/Claude smoke | 默认否，显式执行 |
| EVAL | 多 Provider、模型、Prompt 或版本横向比较 | 报告用途，默认否 |

执行顺序：

~~~text
T0 -> T1 -> T2 -> T3 -> T4 -> 必要时 T5
~~~

EVAL 不属于 P0 的通过条件。

## 3. 每个测试夹具必须固定什么

每个测试 case 都必须有独立、可回读的 fixture：

~~~yaml
case_id: product-login-invalid-password
subject: product-project
source_root: /path/to/product
base_commit: <commit>
config: /path/to/zf.yaml
state_dir: /tmp/zf-test-<timestamp>/state
environment_image: <image>
input: <request or event>
expected:
  terminal_state: failed
  required_events:
    - task.created
    - test.failed
  forbidden_events:
    - run.goal.completed
allowed_side_effects:
  - test-report.json
evidence:
  - events.jsonl
  - test-report.json
  - screenshot.png
~~~

固定项至少包括：

- case_id；
- 产品或 ZaoFu 的 source root；
- base commit；
- zf.yaml 和质量门禁；
- 独立 state dir；
- 环境镜像和依赖版本；
- 输入、预期状态、允许副作用和禁止副作用；
- evidence 路径；
- scorer/断言版本。

## 4. ZaoFu 自身测试方案

### 4.1 T0：配置和静态边界

~~~bash
uv sync --extra dev --extra web

uv run ruff check src tests
python3 -m compileall -q src tests

uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf doctor
python scripts/dev-verify.py plan --base dev
~~~

同时记录：

~~~bash
git status --short
git log -1 --oneline
~~~

测试必须使用独立的临时 state dir，不要覆盖当前开发用的 .zf/。

### 4.2 T1：Kernel、EventLog 和 Store

必须测试：

- events.jsonl 只追加，不被覆盖；
- event schema、causation、correlation、ref 和 verdict 正确；
- TaskStore、FeatureStore、SessionStore、RoleSessionRegistry 的状态所有权不越界；
- 非法、重复、乱序和旧 generation 事件被拒绝或安全处理；
- Web、Feishu、外部 Issue 的写入都经过 EventWriter 或 ControlledAction；
- 终态必须有对应 evidence 和 gate。

关键事件应覆盖：

~~~text
task.created
task.dispatched
worker.completed
review.approved / review.rejected
verify.passed / verify.failed
test.passed / test.failed
judge.passed / judge.failed
run.goal.completed
run.goal.blocked
event.schema.violated
discriminator.failed
~~~

典型测试：

~~~text
给定 task.created
  -> 只能出现一次 task.dispatched
  -> dispatch 必须指向正确 task、attempt 和 generation
  -> 缺少 evidence 时不得出现 run.goal.completed
~~~

### 4.3 T2：Runtime 工作流和恢复

测试：

- zf start 使用正确的配置和 state dir；
- EventWatcher 能被事件唤醒；
- 同一事件不会重复 dispatch；
- fanout、rework、retry、预算、超时和阻塞遵循配置；
- 重启后从事件和 sidecar 恢复，而不是重建伪状态；
- provider 失败后进入正确的失败或 rework 状态；
- 完成事件只在完整链路满足后出现。

最小成功链：

~~~text
exact Run
  -> exact Task
  -> exact attempt/dispatch
  -> evidence
  -> test result
  -> verification
  -> judge
  -> completion gate
  -> run.goal.completed
~~~

最小失败链：

~~~text
固定失败输入
  -> test.failed 或 verify.failed
  -> 不得出现 run.goal.completed
  -> 产生 rework、blocked 或 needs_info
  -> 保存失败 evidence
~~~

### 4.4 Provider、Transport 和 Workdir

优先使用 isolated mock provider，覆盖：

- tmux/stream-json 的启动、输入、输出和退出；
- provider 不存在、版本不符、登录失败、超时和异常退出码；
- worktree/workdir 创建、隔离、复用和清理；
- 输出损坏、截断、格式错误和重复输出；
- token、时间和成本预算；
- provider 声称完成但没有 evidence 的情况。

失败分类要分开：

- ZaoFu 代码缺陷；
- provider 环境问题；
- 宿主机能力问题；
- 测试基础设施问题。

### 4.5 T3：ZaoFu Web/API

测试：

- Kanban、Task、Run、Trace、Graph、Loop、Inbox 与 canonical state 一致；
- project scope、权限和 token gate 生效；
- SSE 断线重连、事件 gap 和快照刷新安全；
- API 错误有明确响应；
- loading、empty、error、stale 状态正确；
- 用户写操作进入 ControlledAction，而不是直接修改 JSON。

页面测试不能只断言 toast。必须同时断言 API、EventLog 或产品后端真实状态。

浏览器测试使用 Docker Playwright；真实开发端口保留 8001，临时测试用 8002+，API/UI 监听 0.0.0.0。

## 5. 产品项目测试方案

ZaoFu 工作流成功不等于产品功能正确。产品项目必须有自己的测试命令、数据和验收 oracle。

### 5.1 产品测试层

至少包括：

- 业务规则和核心用例；
- API contract、鉴权、错误码和幂等；
- 数据库 schema、迁移、约束和一致性；
- 前端组件、路由、表单、权限、空态和错误态；
- 关键用户旅程的 Playwright E2E；
- 可恢复中断、超时和重复请求；
- 发布、回滚和数据恢复。

产品项目的 zf.yaml 应明确质量门禁：

~~~yaml
quality_gates:
  static:
    required_checks:
      - npm run typecheck
      - npm run build
  test:
    required_checks:
      - npm test
      - npm run test:e2e
~~~

### 5.2 产品与 ZaoFu 的连接测试

必须验证：

- Task 是否落到正确的产品 source root；
- 测试命令是否在产品目录执行；
- 产品 state dir 与 ZaoFu state dir 是否隔离；
- 报告、截图、日志、coverage 和构建物是否保存为 evidence；
- 产品测试失败是否进入正确 rework；
- 产品未通过时不得产生 candidate；
- 产品通过后 candidate 是否绑定正确 commit 和测试报告。

### 5.3 最小产品 fixture

第一个产品 fixture 至少包含：

1. 一个成功业务用例；
2. 一个业务校验失败用例；
3. 一个产品测试失败用例；
4. 一个可恢复中断用例；
5. 一个已有 bug 和对应回归测试。

每个 case 都必须能独立启动、独立清理、重复运行，并产生可计算的最终状态。

## 6. 测试 oracle 和证据

测试 oracle 按优先级排序：

1. 精确的数据库或 Store 状态；
2. 精确的 EventLog 事件序列；
3. 命令退出码和结构化测试报告；
4. 文件、构建物和 sidecar digest；
5. API JSON 响应；
6. 页面 DOM、网络请求、console 和 trace；
7. 人工截图或自然语言说明。

最后两项不能单独证明业务完成。

每次测试至少保存：

- 测试输入；
- 配置和 commit；
- state dir；
- events；
- Task/Run/Attempt ID；
- provider transcript（如果有）；
- 测试报告；
- 日志、截图、trace；
- 失败原因和分类。

## 7. 测试指标

P0 只保留与测试和工程质量直接相关的指标：

| 指标 | P0 要求 |
| --- | --- |
| T0/T1/T2/T3/T4 通过率 | 受影响范围 100% |
| 缺陷复现稳定性 | 同一 fixture 可重复复现 |
| false completion | 0 |
| duplicate invoke/dispatch | 0 |
| stale generation dispatch | 0 |
| required evidence missing | 0 |
| 非法动作越权 | 0 |
| 产品关键验收用例 | 100% 通过 |
| recovery case | 100% 到达预期恢复状态 |
| 测试结果可回读 | 100% 有 case、commit、state 和 evidence |

coverage、耗时和成本是辅助指标，不得替代行为断言。

只有在 P0 测试稳定后，才进行可选的评估：

~~~text
同一批测试 fixture
  -> 多个 Provider 或模型
  -> 每个 case 多次运行
  -> 统计成功率、成本、时延和一致性
~~~

这类结果用于比较方案，不作为默认 merge gate。

## 8. 问题处理：从测试失败到 merge

### 8.1 ZaoFu 自身缺陷

~~~text
测试发现 ZaoFu 缺陷
  -> 保存 fixture 和 evidence
  -> /issue
  -> 人工确认并发布 Issue
  -> External Issue mirror
  -> 只读 triage
  -> ready_to_fix / needs_info
  -> exact proposal approval
  -> Fix Run
  -> ZaoFu T0-T4 测试
  -> independent verify
  -> judge
  -> verified_candidate
  -> owner review
  -> 本地 review branch
  -> 人工 push / PR / merge
  -> 只读同步 merged
~~~

约束：

- /issue 用于 ZaoFu 自身问题；
- Issue 必须绑定现象、复现步骤、期望/实际、commit 和 evidence；
- ready_to_fix 不是“已经修复”；
- ZaoFu 不自动 push、创建 PR、merge、部署或关闭 Issue。

### 8.2 产品项目缺陷

~~~text
产品测试失败
  -> 产品项目 Issue
  -> 固定产品 source root 和 base commit
  -> 生成或补充最小回归测试
  -> Fix Run
  -> 产品 unit/API/E2E
  -> independent verify
  -> candidate
  -> 产品 owner review
  -> 人工 merge
~~~

产品缺陷不能把产品源码错误映射到 ZaoFu 的 Self-Issue target root。ZaoFu runtime 的诊断可以作为关联 evidence，但产品 Issue 和产品测试仍然是权威入口。

### 8.3 /issue 最小字段

~~~text
标题：
可观察症状：
复现步骤：
期望行为：
实际行为：
项目和 source root：
版本 / base commit：
Project / Workflow / Task / Run：
相关命令：
事件 ID / Task ID / Run ID：
测试报告、日志、截图或 trace：
影响范围：
是否可以稳定复现：
~~~

## 9. 第一次执行顺序

### 第一步：建立边界

- 记录当前 dirty worktree；
- 创建 /tmp/zf-<purpose>-<timestamp>/；
- 使用唯一 state dir、端口和 tmux session；
- 不清理当前真实 .zf/；
- 固定配置、commit、镜像和测试命令。

### 第二步：执行 ZaoFu T0-T2

~~~bash
uv run ruff check src tests
python3 -m compileall -q src tests
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf doctor
python scripts/dev-verify.py plan --base dev
~~~

然后运行受影响模块、直接调用者和共享契约测试：

~~~bash
uv run pytest \
  tests/test_event_writer.py \
  tests/test_task_store.py \
  tests/test_config_loader.py \
  tests/test_orchestrator.py \
  tests/e2e/test_robustness_suite.py \
  -q --no-cov
~~~

### 第三步：建立最小产品 fixture

先不要接真实 provider。用一个最小产品项目跑通：

~~~text
成功 -> 业务失败 -> 测试失败 -> 中断恢复 -> 回归测试
~~~

确认每个 case 都能回读产品状态和 evidence。

### 第四步：执行产品 Web/API 测试

先 API 和数据库，再 Playwright。页面绿色提示只能作为辅助证据，不能作为唯一成功条件。

### 第五步：跑一次受控故障闭环

~~~text
可复现失败
  -> 保存 evidence
  -> 正确的 /issue 或产品 Issue
  -> triage
  -> fix
  -> 测试
  -> independent verify
  -> candidate
  -> owner review
  -> 人工 merge
~~~

真实 Codex/Claude smoke 和 Provider 横向比较放在最后，并且不取代 T0-T4。

## 10. P0 完成标准

P0 完成必须同时满足：

- ZaoFu T0-T4 受影响测试全部通过；
- 最小产品 fixture 的成功、失败、恢复和回归 case 全部通过；
- 每个失败都能稳定复现并保存 evidence；
- 没有 false completion、重复 dispatch、stale generation 或越权动作；
- 产品 candidate 绑定正确 source root、commit、测试报告和 verify 结果；
- 至少一次问题完整走通“测试失败 -> Issue -> Fix Run -> 测试 -> verify -> candidate -> 人工 merge”。

这套方案的核心不是给 ZaoFu 打一个总分，而是证明：

~~~text
给定固定输入和环境
  -> 行为符合合同
  -> 状态和事件正确
  -> 产品真实功能正确
  -> 失败可复现
  -> 修复可验证
  -> merge 后状态不被伪造
~~~
