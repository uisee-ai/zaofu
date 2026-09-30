# ZaoFu-TestKit

> 目标：建立一套只针对 ZaoFu 自身的、可重复、可判定、可进入合并门禁的系统测试方案。
>
> 范围：ZaoFu 的配置、CLI、确定性 Kernel、Runtime、EventLog、Canonical Store、Orchestrator、Evidence、Provider/Transport、Workdir、恢复、并发、安全、Web/API、可观测性和资源生命周期。
>
> 对象边界：本方案测试 ZaoFu 自身；生成产品的业务测试另见 [ZaoFu_Product_TestKit.md](ZaoFu_Product_TestKit.md)，两者的连接通过 run/candidate/commit/evidence 与 P-18 交付合同核对。独立产品方案及 TestKit 已存在，不再列为“待调研”。

## 当前执行口径（2026-09-30 文档核对）

- 本文是测试要求与判据，不是执行报告。下文域清单的 `[ ]` 表示逐次验收检查项，不能直接统计成框架未实现数量。
- 框架完成看“可执行、可判定、可留证、可复现、可清理”。稳定检出 ZaoFu 缺陷是有效检测能力；被测系统的完整通过和最终同源验收另行判定。
- 当前实施状态见 [steps.md](steps.md) 顶部 K613 暂停快照和 [已完成/待完成梳理](zaofu_done_part.md)。本次仅审核文档，不产生新的测试通过结论，不重算完成率。
- Codex K547 与 Claude 原生 pipeline 均为 `SKIPPED_BY_OPERATOR_ENVIRONMENT`：方法保留、执行跳过。历史 CLI/登录观察不代表重启后的环境状态。
- Feishu 联调、`/issue-merge` 和明确降级项本轮不实现；下文相关条目保留为边界或未来要求，不重新纳入已批准主干工作。已有本地/mock 合同不删。
- T0～T6 是本文的风险分层；真实 runner 使用 `tests/testkit/suites.py` 中的 S0～S11 和显式 profile，二者不能按编号等同。入口为 `uv run python -m tests.testkit --stage <stage> --output <新证据目录>`；`--profile mainline` 只是登记的确定性子集，不是全仓或完整原生 E2E。
- 实施记录与审计 JSON 的历史数量不累计、不代表当前工作树全绿。重启前备份索引见 [重启交接](../05_restart_handoff/README.md)。

## 1. 测试目标和基本原则

### 1.1 测试目标

ZaoFu 测试要证明以下事实：

```text
给定固定配置、输入和隔离环境
    -> ZaoFu 执行确定性动作
    -> EventLog、Canonical Store、artifact 和 projection 产生结果
    -> 测试 oracle 检查正确状态、必需事件、证据和副作用边界
    -> 得到可重放的 pass / fail / infrastructure_error
```

核心测试问题不是 Provider 或 Agent “聪不聪明”，而是：

- 是否只发生允许的状态转移；
- EventLog 是否保持追加、顺序、因果和去重；
- Canonical Store 是否由正确的组件修改；
- stale attempt/generation 是否不能覆盖当前状态；
- 没有 evidence 时是否不能完成；
- 重启、重复唤醒、乱序事件和旧 worker 是否不会制造错误状态；
- 未授权 ControlledAction 和外部副作用是否为零；
- Web/API/CLI 是否只投影或触发受控动作，而不是绕过规范状态；
- 测试失败是否能留下足够证据供 `/issue` 和修复流程使用。

### 1.2 测试与评估的边界

本方案属于测试：每个 case 有固定输入、固定预期和明确禁止结果。以下内容不作为本方案的通过依据：

- 多 Provider 或多模型排行榜；
- Agent 能力排名；
- LLM judge 的总体质量分；
- token 成本或平均成功率的横向比较；
- 单纯的截图、自然语言报告或“Agent 自称完成”。

真实 Provider 只用于验证 ZaoFu 的启动、通信、超时、恢复和清理能力，不评价 Provider 的语义能力。

### 1.3 ZaoFu 的测试不变量

以下不变量必须有对应的自动测试：

1. `zf.yaml` 是唯一控制平面配置；所有运行入口尊重 `project.state_dir`。
2. `events.jsonl` 是追加账本，不能被覆盖、伪造或静默重排。
3. EVT-05 要求正式入口对同一事件去重、不得重复投影；这是待验证不变量，不是所有低层 raw append 已保证唯一的实现声明。低层账本保留实际发生记录，不能靠删重复输入让测试变绿。
4. Event 的 schema、scope、causation、correlation、attempt 和 generation 必须一致。
5. TaskStore、FeatureStore、SessionStore、RoleSessionRegistry 只能通过其规范接口更新。
6. Agent/Provider 的输出是事实或意图，不能直接成为 canonical terminal state。
7. stale attempt/generation 的结果不能覆盖当前状态。
8. 缺少 required evidence、错误 digest 或错误 commit 时不能通过完成 gate。
9. fanin 必须等待规定的全部子任务；fanout、retry、rework 必须有界且幂等。
10. 重启后的状态必须由持久化事件、Store 和 sidecar 恢复，不能凭空创建伪状态。
11. 同一个 ControlledAction operation 重放不能重复执行外部副作用。
12. 未授权、越界、带错误 token 或错误 project scope 的操作必须 fail-closed。
13. Web/API/CLI 的投影不能与 canonical state 相冲突。
14. 测试、运行和恢复结束后不能遗留未经允许的进程、tmux、端口、锁和临时状态。

## 2. 测试工具和执行分层

### 2.1 工具职责

| 工具/设施 | 主要职责 | 使用边界 | 结果是否进入普通 merge gate |
|---|---|---|---|
| pytest | Python 测试发现、fixture、参数化、断言、marker | 配置、Kernel、Runtime、Store、CLI/API、Mock E2E | 是 |
| pytest-asyncio | 异步 watcher、transport、SSE、恢复测试 | asyncio 生命周期和异步 fixture | 是 |
| pytest-cov/Coverage.py | 行/分支覆盖测量 | 发现盲区，不定义正确性 | 发布或显式覆盖层 |
| pytest-xdist | 隔离测试并行执行 | 只并行无共享资源的 case | 是，按资源分组 |
| Hypothesis | 生成状态序列和最小反例 | EventLog、Store、Orchestrator、Action | 稳定后进入确定性层 |
| `unittest.mock`/monkeypatch | 模拟 Provider、时钟、磁盘和外部 sink | 进程内故障和依赖替换 | 是 |
| subprocess/barrier/failpoint | 真实进程崩溃、重启和提交点故障 | crash/recovery、文件和进程边界 | 是 |
| Schemathesis | FastAPI/OpenAPI 正负向请求 | API schema、状态码和边界参数 | API 层稳定后进入 |
| Playwright + Docker | 浏览器 DOM、SSE、用户操作和 trace | Web 行为；必须联查后端真值 | 关键 Web 路径 |
| Testcontainers | 启动隔离的协议服务 | 真实协议集成测试 | 专用集成层 |
| Toxiproxy | TCP 延迟、超时、断连、reset、限速 | 外部网络链路 | 专用网络层 |
| k6 | API/Web/SSE 压力、spike、soak | 已有性能基线后 | 发布/定时层 |
| OWASP ZAP | 隔离 Web/API 动态安全扫描 | baseline 和认证后扫描 | 发布/定时层 |
| mutmut | 检查测试是否能杀死错误变异 | Kernel 高风险模块 | 周期性审计 |

### 2.2 执行层级

```text
T0  配置、静态和依赖预检
T1  Kernel、EventLog、Store、Schema、ControlledAction
T2  Runtime、Orchestrator、fanout/rework/retry、Mock Provider
T3  恢复、故障注入、并发和资源清理
T4  FastAPI/API、SSE、Web、CLI 投影
T5  宿主机和真实 Provider canary
T6  性能、安全扫描、安装兼容和 mutation 审计
```

执行规则：

- T0–T4 是 ZaoFu 日常正确性主线；核心变更应阻断 merge。
- T5 需要显式宿主能力和凭据，默认不阻断普通 merge。
- T6 是发布、定时或周期性质量层，不能替代 T0–T4。
- 第一次失败后重试成功，必须标记 flaky 或 race，不能直接算绿色。
- `infrastructure_error`、`not_run`、`flaky` 与 `fail` 分开记录，均不能冒充 `pass`。

### 2.3 当前仓库的测试入口映射

下面是现有测试代码的代表性入口。它们是执行起点，不代表一个文件已经覆盖该域全部风险；变更时仍按 `dev-verify.py` 计算影响闭包。

| 测试域 | 代表性现有测试入口 | 适合补充的缺口 |
|---|---|---|
| 配置/启动 | `tests/test_config_schema.py`、`tests/test_loader_field_coverage.py`、`tests/test_workflow_profiles.py` | 全 CLI/Web/sidecar 的 state dir 一致性 |
| EventLog/Schema | `tests/test_event_contracts.py`、`tests/test_events_log.py`、`tests/test_event_writer_schema.py`、`tests/test_events_archive.py` | 半写、乱序、旧 schema 和 replay 矩阵 |
| Store/锁 | `tests/test_task_store.py`、`tests/test_feature_store.py`、`tests/test_state_locks.py` | 多 Store 交叉恢复和损坏 fail-closed |
| Orchestrator | `tests/test_runtime_orchestrator.py`、`tests/test_workflow_spine_projection.py`、`tests/test_wake_pattern_coverage.py` | 统一不变量矩阵和所有工作流终态覆盖 |
| fanout/rework/recovery | `tests/test_fanout_recovery_runtime.py`、`tests/test_task_attempt_recovery.py`、`tests/test_control_actions_recovery.py` | commit-point 故障注入和 baseline 等价 |
| Evidence/operation | `tests/test_sidecar_refs.py`、`tests/test_workflow_operation.py`、`tests/test_payload_contract.py` | 全部 gate 与 artifact/commit 绑定组合 |
| Provider/transport | `tests/test_backend_adapters.py`、`tests/test_transport_stream_json.py`、`tests/test_tmux_transport_lifecycle.py`、`tests/test_workdirs.py` | 真实协议 canary、超时清理和网络故障 |
| CLI/API/Web | `tests/test_web_server.py`、`tests/test_web_sse.py`、`tests/test_workspace_projects.py`、`web/tests/*.test.ts`、`web/tests/*.spec.ts` | API schema 自动生成、跨层真值链和 WebSocket |
| 安全/ControlledAction | `tests/test_security_signing.py`、`tests/test_path_guard.py`、`tests/test_action_orchestrator.py`、`tests/test_external_issue_ingress.py` | 不可信输入到危险 sink 的全矩阵 |
| 观测/资源 | `tests/test_delivery_trace.py`、`tests/test_metrics_collector.py`、`tests/test_shutdown_stale_inflight_cleanup.py` | 失败 bundle、secret redaction 和资源泄漏 |

必要的自动门禁入口：

```bash
python scripts/dev-verify.py plan --base dev
python scripts/dev-verify.py run --base dev
bash scripts/dev-premerge-gate.sh
```

`dev-verify` 负责按变更选择测试；`dev-premerge-gate.sh` 是快速合同哨兵，不能被解释为全量测试结果。

## 3. 每个测试 Case 的统一合同

### 3.1 Case 元数据

每个 case 至少固定：

```yaml
case_id: duplicate-wakeup-does-not-redispatch
subject: zaofu
test_domain: orchestration
tier: T2
source_root: /home/uisee/zaofu_v7
base_commit: <git commit>
config_digest: <sha256 of zf.yaml>
dependency_lock_digest: <sha256 of uv.lock>
state_dir: /tmp/zf-test-<run-id>/state
input:
  events: []
  commands: []
  provider_script: []
expected:
  state: {}
  required_events: []
  required_evidence: []
forbidden:
  events: []
  states: []
  side_effects: []
allowed_side_effects: []
evidence:
  - events.jsonl
  - task-store.json
  - diagnostics.json
cleanup:
  tmux_sessions: 0
  processes: 0
  listening_ports: []
```

### 3.2 Given / When / Then / Forbidden / Evidence

每个测试函数必须能回答：

```text
Given      初始配置、事件、Store、attempt/generation 和隔离资源
When       调用一个 CLI/API、运行一个 loop 或注入一个故障
Then       期望的状态、事件、artifact、返回码和 projection
Forbidden  绝对不能出现的状态、事件、重复动作和副作用
Evidence   从哪些文件、API、DOM、trace 和调用记录取证
```

### 3.3 统一判定优先级

按以下顺序裁决：

1. Canonical Store 精确状态；
2. EventLog 事件、顺序和因果链；
3. artifact、sidecar、ref、digest 和 commit 绑定；
4. 结构化测试报告、退出码和诊断；
5. API JSON/SSE payload；
6. Web DOM、network、console、trace；
7. 截图和自然语言描述。

低层级投影不能推翻高层级事实。例如页面显示 `completed`，但没有合法 `verify.passed` 或 Store 仍是 `in_progress`，case 必须失败。

## 4. 测试支撑层清单

`tests/testkit/` 薄支撑层已存在，复用场景和领域断言，不复制生产状态机。下列是职责示意，不是必须逐一新建同名文件的待办；实际入口包括 `runner.py`、`pytest_plugin.py`、`case_evidence.py`、`model_evidence.py`、`cleanup.py` 和 `suites.py`。

```text
tests/testkit/
  scenarios.py       # Run/Task/Attempt/Event 的固定构造
  oracles.py         # Store/EventLog/evidence/副作用联合断言
  providers.py       # Scripted Mock Provider
  failpoints.py      # 命名 commit point 和 barrier
  processes.py       # subprocess 启动、崩溃、重启和诊断
  external_sinks.py  # GitHub/Feishu/git/shell effect spy
  web_truth.py       # API/SSE/DOM 与后端真值联合检查
  cleanup.py         # tmux、端口、锁、进程和临时目录清理
```

清单：

- [ ] 每个 fixture 创建独立 `state_dir`，不触碰开发环境 `.zf/`。
- [ ] 每个 fixture 显式记录 `run_id`、`task_id`、`attempt`、`generation`、`operation_id`。
- [ ] Mock Provider 支持成功、失败、超时、截断、坏 JSON、重复输出和迟到输出。
- [ ] Effect spy 记录调用参数、调用顺序、调用次数和 operation ID。
- [ ] failpoint 默认关闭，只能由测试显式启用。
- [ ] subprocess case 能获取退出码、signal、stdout、stderr 和 state dir。
- [ ] cleanup 在成功、断言失败、超时和硬退出后都执行。
- [ ] 支撑层断言不重新实现被测 Orchestrator 或 Store 的逻辑。

## 5. 测试域一：配置、启动和静态边界

### 5.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| CFG-01 | 合法最小配置加载 | pytest、config loader | 返回成功；默认值和字段精确等于 schema |
| CFG-02 | 缺失必填字段 | pytest、`zf validate` | 非零退出；错误包含准确字段路径；不创建运行态 |
| CFG-03 | 未知字段和错误类型 | pytest、schema validator | 明确拒绝；不得静默忽略或转换成错误默认值 |
| CFG-04 | profile/role/workflow/backend 引用闭合 | pytest、loader | 所有引用可解析；断开的引用非零失败 |
| CFG-05 | 自定义 `project.state_dir` | pytest、CLI subprocess | 所有运行态文件只出现在指定目录；默认 `.zf` 不产生新状态 |
| CFG-06 | cold-start/preflight/doctor | pytest、CLI | 正确配置成功；缺少依赖时明确报告并返回非零 |
| CFG-07 | 配置 digest 和来源记录 | pytest、结构化报告 | 报告记录实际配置来源和 digest；无隐式第二配置 |
| CFG-08 | CLI 入口一致性 | pytest 参数化 | 每个注册命令解析相同项目根和 state dir |
| CFG-09 | 配置环境污染 | subprocess、monkeypatch | 外部环境变量不能覆盖禁止覆盖的控制字段 |
| CFG-10 | 配置失败清理 | pytest、cleanup fixture | 失败后无残留 state、进程、端口和临时文件 |

### 5.2 执行命令

```bash
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf doctor
python scripts/dev-verify.py plan --base dev
```

### 5.3 域通过清单

- [ ] 合法、非法、未知字段和错误类型均有测试。
- [ ] 自定义 state dir 已通过 CLI、Runtime、Web、sidecar、archive、cleanup 全入口验证。
- [ ] 预检失败不会创建伪造的 Task、Run 或 EventLog。
- [ ] 配置错误包含字段路径、原因和非零退出码。
- [ ] 运行态目录边界没有越界写入。

## 6. 测试域二：Schema、EventLog 和事件契约

### 6.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| EVT-01 | append 新事件 | pytest、EventWriter | 文件追加一行；事件可读回且 schema 正确 |
| EVT-02 | EventLog 只能追加 | pytest、文件快照/hash | 旧行不改变、不覆盖、不重排 |
| EVT-03 | 缺必填字段 | pytest、schema validator | 拒绝或产生明确 `event.schema.violated`；不投影业务状态 |
| EVT-04 | 非法类型、枚举和 scope | pytest | 非零/拒绝；Store 不发生非法变化 |
| EVT-05 | duplicate event ID | pytest、重复 append | 只接受一次；projector 不重复运行；attempt 不重复增长 |
| EVT-06 | causation/correlation 链 | pytest、event graph oracle | 每个派生事件指向合法父事件；断链失败 |
| EVT-07 | 乱序事件 | pytest、参数化 | 旧事件不能覆盖新状态；拒绝或安全延迟处理 |
| EVT-08 | old generation/attempt | pytest | stale 事件不能 dispatch、完成或覆盖当前 Store |
| EVT-09 | archive 后查询 | pytest、archive reader | active + archive 查询结果完整，顺序和全局 cursor 正确 |
| EVT-10 | 非法尾行/半写行 | pytest、真实临时文件 | 不影响已提交事件；诊断明确；不会凭空补事件 |
| EVT-11 | 签名和篡改 | pytest、hash/signature helper | 原文可验证；篡改或错误 key 拒绝 |
| EVT-12 | projector replay | pytest、snapshot comparison | 同一事件集首次执行和 replay 得到相同 canonical state |

### 6.2 典型 case

```text
Given: task.created，Task 当前 attempt=1
When: EventWatcher 被同一 event_id 唤醒两次
Then: 只有一个有效 task.dispatched，attempt 仍为 1
Forbidden: 第二个 dispatch、attempt=2、run.goal.completed
Evidence: events.jsonl、TaskStore、orchestrator diagnostics
```

### 6.3 域通过清单

- [ ] 所有关键事件有 payload schema 和 consumer contract。
- [ ] 事件体、顺序、因果、scope、attempt、generation 都被断言。
- [ ] 重复、乱序、旧版本、非法和半写事件都有负向测试。
- [ ] active log、archive 和 replay 的结果一致。
- [ ] EventLog 发生写入错误时，错误被记录且不会伪造成功状态。

## 7. 测试域三：Canonical Store 和状态所有权

### 7.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| STO-01 | Task 创建、查询和列表 | pytest、TaskStore | 字段完整；ID、scope 和状态正确 |
| STO-02 | 合法状态迁移 | pytest、状态机 | 只允许定义的 forward transition |
| STO-03 | 非法回退和终态重开 | pytest | 拒绝；canonical state 保持不变 |
| STO-04 | Feature/Session/Role store ownership | pytest | 只有所属 Store 更新规范字段 |
| STO-05 | 原子写入 | pytest、monkeypatch、文件快照 | 失败时旧文件完整；无半截 JSON |
| STO-06 | 并发更新 | pytest、multiprocessing、serial | 不丢更新、不覆盖其他进程有效修改 |
| STO-07 | 归档和 terminal 查询 | pytest | active 与 archive 查询一致；最新 attempt 明确 |
| STO-08 | Store 损坏 | pytest、损坏 JSON | fail-closed；报告损坏，不从低权威 projection 猜状态 |
| STO-09 | 已定义重建合同的 projection replay 与 Store 恢复 | pytest、snapshot | 只删除可重建投影，使用真实账本、Store 和必需 sidecar 验证等价；不能推断任意 canonical Store 可仅由事件重建 |
| STO-10 | 直接文件写入审计 | pytest、静态扫描 | 禁止组件不直接修改 canonical 文件 |

### 7.2 关键断言

```text
同一组 events
  -> 首次运行得到 state_A
  -> 删除可重建 projection
  -> replay 得到 state_B
  -> state_A == state_B
```

### 7.3 域通过清单

- [ ] Task、Feature、Session、RoleSessionRegistry 均有所有权测试。
- [ ] 原子替换、锁、损坏、归档和 replay 均有测试。
- [ ] Provider、Web、Feishu 和 Issue integration 不得绕过 Store 接口。
- [ ] terminal 状态一旦成立不能被旧事件或普通 worker 报告重开。
- [ ] Store snapshot 与 EventLog 不一致时有明确权威优先级。

## 8. 测试域四：Orchestrator 和工作流状态推进

### 8.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| ORC-01 | ready task dispatch | pytest、Mock Provider | 正确 role、task、attempt、generation 一次派发 |
| ORC-02 | blocked dependency | pytest 参数化 | 依赖未完成不 dispatch；依赖完成后只推进一次 |
| ORC-03 | duplicate wakeup | pytest | 不产生重复 dispatch 或 attempt 增长 |
| ORC-04 | worker completed 只记录事实 | pytest | 不直接把 Task/Run 设为 terminal |
| ORC-05 | evidence/test/verify/judge 链 | pytest、mock E2E | 只有全部 gate 满足才产生 terminal event |
| ORC-06 | missing evidence | pytest | `verify.failed`/`discriminator.failed` 或 rework；不得完成 |
| ORC-07 | fanout | pytest、Mock Provider | child 数量、父子 lineage 和 target commit 正确 |
| ORC-08 | fanin | pytest | 未完成全部 required child 时 parent 不完成；最后一个只触发一次 |
| ORC-09 | rework | pytest | 路由到正确 owner/stage；generation 更新有界 |
| ORC-10 | retry cap | pytest | 达到上限后进入明确 blocked/deadletter/human 终态，不无限循环 |
| ORC-11 | cancel/pause/resume | pytest | 取消和暂停边界正确；迟到结果不能复活已取消运行 |
| ORC-12 | budget/timeout | pytest-timeout、Mock Provider | 超限生成准确失败事件和诊断，不静默完成 |
| ORC-13 | plan fingerprint dedupe | pytest | 等价计划不重复铸造；不同指纹可创建新计划 |
| ORC-14 | retrigger delta gate | pytest | 无有效 delta 时抑制 retrigger；有 delta 时因果链正确 |
| ORC-15 | candidate incremental idempotency | pytest、临时 Git repo | 等价 patch 不重复纳入；候选绑定正确 commit |

### 8.2 最小成功链和失败链

```text
成功：
task.created
 -> task.dispatched
 -> worker.completed
 -> evidence accepted
 -> test.passed
 -> verify.passed
 -> judge.passed
 -> run.goal.completed
```

```text
失败：
task.created
 -> task.dispatched
 -> worker.completed
 -> required evidence missing
 -> verify.failed / discriminator.failed
 -> bounded rework or blocked
 -> 不得出现 run.goal.completed
```

### 8.3 域通过清单

- [ ] 每条官方工作流至少有成功、失败、缺失依赖、重复和乱序 case。
- [ ] 每个 dispatch 有唯一 task/attempt/generation/target commit。
- [ ] fanout/fanin、rework/retry、cancel/pause/resume 都有有界测试。
- [ ] kernel terminal gate 不能由 Agent 自己声明绕过。
- [ ] plan fingerprint、delta gate、candidate patch-id 等已落地合同有回归测试。

## 9. 测试域五：Evidence、Artifact、Verification 和完成 Gate

### 9.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| EVD-01 | 必需 artifact 存在 | pytest、临时目录 | 正确 Task/Attempt/Run 下存在且可读取 |
| EVD-02 | artifact 为空 | pytest | gate 拒绝；产生明确诊断 |
| EVD-03 | ref 路径逃逸 | pytest、path guard | 拒绝项目外、state 外或 symlink 逃逸路径 |
| EVD-04 | digest 正确 | pytest、sha256 | 文件内容与记录 digest 精确相等 |
| EVD-05 | digest 被替换 | pytest | verify 失败；不能读取替换内容作为有效证据 |
| EVD-06 | 旧 attempt artifact | pytest | stale artifact 不能满足当前 gate |
| EVD-07 | 错误 commit artifact | pytest、临时 Git repo | candidate/verify 拒绝错误基线 |
| EVD-08 | 缺失测试报告 | pytest | Task/Run 不得 terminal completed |
| EVD-09 | sidecar 原子写入 | pytest、failpoint | 中断后不出现伪完整 descriptor |
| EVD-10 | verification payload non-empty | pytest | required 字段存在且非空；空转报告失败 |
| EVD-11 | operation result replay | pytest | operation 结算可恢复且不重复副作用 |

### 9.2 通过判据

```text
正确 Task
+ 正确 Attempt
+ 正确 Run
+ 正确 commit
+ 正确 artifact ref
+ digest 校验成功
+ required 字段非空
+ verifier/judge 事件合法
= 允许进入终态
```

### 9.3 域通过清单

- [ ] 不能只用“文件存在”作为证据。
- [ ] ref、digest、Task、Attempt、Run、commit 的绑定均被检查。
- [ ] evidence 缺失、为空、陈旧、被替换和路径越界均 fail-closed。
- [ ] 每个终态都有完整的 evidence 和 verifier 证据。
- [ ] 侧车正文、descriptor 和 digest 的写入具有可恢复性。

## 10. 测试域六：Provider、Transport、Workdir 和 tmux

### 10.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| PRV-01 | adapter 命令构造 | pytest | model、reasoning、环境和安全参数正确 |
| PRV-02 | Provider 不存在 | pytest、monkeypatch | preflight 明确失败；不污染 canonical state |
| PRV-03 | 正常 spawn/send/receive | pytest、Scripted Provider | briefing、session、transcript 和结果可回读 |
| PRV-04 | stream-json 正常输出 | pytest-asyncio | JSON 逐条解析；终态和 transcript 正确 |
| PRV-05 | 坏 JSON/截断/重复输出 | pytest | 只记录事实；不推进未验证终态 |
| PRV-06 | Provider 非零退出 | pytest | 产生失败/恢复事件；保留 stderr/transcript |
| PRV-07 | timeout/stuck | pytest-timeout、Mock Provider | 触发业务 timeout 或 recovery；不永久挂死 |
| PRV-08 | session resume | pytest、subprocess | 正确 session ID 恢复；不创建无关新任务 |
| PRV-09 | tmux pane 消失 | pytest、host marker | 产生 worker crash/respawn/recovery 事件；旧结果受控 |
| PRV-10 | workdir 初始化失败 | pytest、临时目录权限 | dispatch fail-closed；错误指向 workdir |
| PRV-11 | worktree 隔离 | pytest、临时 Git repo | source root、candidate root、state dir 不串用 |
| PRV-12 | worktree cleanup | pytest、cleanup checker | 成功/失败/取消后无错误残留 |
| PRV-13 | token/time/cost budget | pytest、fake usage | 超预算被拒绝或停止；不伪造完成 |

### 10.2 Provider 结果分类

测试报告必须区分：

- ZaoFu 代码缺陷；
- Provider 返回失败；
- Provider CLI/凭据环境缺陷；
- tmux/宿主能力缺陷；
- 测试基础设施缺陷。

Provider 说“任务已完成”而没有 evidence 时，预期结果是“记录 Provider 事实但不完成 Task”。

### 10.3 域通过清单

- [ ] Mock Provider 覆盖全部协议异常，不依赖真实模型输出。
- [ ] stream-json、tmux、resume、超时、异常退出都有独立 case。
- [ ] Provider 故障不会直接修改 canonical terminal state。
- [ ] workdir/worktree/state dir 互相隔离且能清理。
- [ ] 真实 Provider 只运行显式 smoke/canary，并保存 transcript。

## 11. 测试域七：恢复、重启和故障注入

### 11.1 命名 failpoint

建议至少提供以下测试专用故障点：

```text
before_event_append
after_event_append_before_projection
before_store_replace
after_store_replace
after_artifact_write_before_digest_event
after_external_effect_before_result_event
before_archive_rotation
after_archive_rotation
after_lock_acquire
before_recovery_commit
```

### 11.2 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| REC-01 | 正常运行 baseline | pytest、Mock Provider | 保存最终 Store、EventLog、evidence 和副作用快照 |
| REC-02 | append 前崩溃 | failpoint、subprocess | 重启后无未提交事件；状态等于合法前缀 |
| REC-03 | append 后 projection 前崩溃 | failpoint、subprocess | 重启 replay 后与 baseline 等价；不重复 dispatch |
| REC-04 | Store replace 前崩溃 | failpoint、subprocess | 旧 Store 完整；恢复后可重放到正确状态 |
| REC-05 | artifact 后 digest event 前崩溃 | failpoint、subprocess | artifact 可验证或被明确标记未提交；不能伪完成 |
| REC-06 | external effect 后 result event 前崩溃 | fake sink、subprocess | 重启后 operation 不重复；最终结果可结算 |
| REC-07 | EventWatcher 重复唤醒 | pytest | 同一事件只触发一次有效动作 |
| REC-08 | Orchestrator 中途退出 | subprocess、signal | 重启延续原 Run/Task/Attempt；不创建无关实体 |
| REC-09 | stale worker 晚到 | pytest | 旧结果被拒绝/记录，不能覆盖新 attempt |
| REC-10 | corrupted canonical store | pytest | fail-closed；不从低权威 projection 猜测完成 |
| REC-11 | partial JSONL/archive | pytest、真实文件 | 已提交事件保持可读；损坏边界有诊断 |
| REC-12 | shutdown cleanup | subprocess、cleanup checker | inflight work 按规则 requeue/suspend；无孤儿进程 |

### 11.3 恢复判据

```text
故障运行恢复后的最终 canonical state
== 无故障 baseline canonical state

故障运行恢复后的有效 EventLog 因果链
== baseline 允许的事件前缀/补偿链

已完成外部副作用次数
<= 1
```

### 11.4 域通过清单

- [ ] 每个持久化和外部副作用边界都有至少一个 failpoint case。
- [ ] 故障使用独立 subprocess，不能只在同一进程捕获异常。
- [ ] 重启前后的 Run/Task/Attempt/operation 身份延续关系有断言。
- [ ] 恢复不能依赖页面缓存、内存标志或自然语言日志。
- [ ] 所有恢复 case 都验证无重复副作用和无状态倒退。

## 12. 测试域八：并发、幂等和竞态

### 12.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| CON-01 | 多进程 EventLog append | pytest、multiprocessing、serial | 每条合法事件保留；无交叉损坏 |
| CON-02 | Store 并发更新 | pytest、barrier | 不丢更新；锁超时明确失败 |
| CON-03 | 两个 watcher 同时唤醒 | pytest、subprocess | 只有一个有效 dispatch/action |
| CON-04 | 同一 action 重复提交 | pytest、effect spy | operation ID 相同；副作用最多一次 |
| CON-05 | fanout child 并发完成 | pytest、barrier | parent 只在完整条件满足时完成；fanin 一次 |
| CON-06 | retry/rework race | pytest、barrier | 只接受一个合法 generation；其余结果 stale |
| CON-07 | archive rotation 与 query 并发 | pytest、serial | 查询不丢、不重复、不读半写文件 |
| CON-08 | SSE 多客户端 cursor | pytest-asyncio、Playwright | 每个客户端 cursor 独立；gap 处理一致 |
| CON-09 | xdist 隔离验证 | pytest-xdist | 并行 case 不共享 state、端口、tmux 和环境变量 |
| CON-10 | lock timeout | pytest-timeout | 超时有界并产生诊断；不会永久挂死 |

### 12.2 并发测试特别规则

`pytest-xdist` 只用于加速互相隔离的 case，不等于并发正确性测试。真正的并发测试必须使用 barrier、multiprocessing 或 subprocess，使多个执行者明确竞争同一个受控资源。

### 12.3 域通过清单

- [ ] 共享 state dir 的 case 默认串行并有 `serial` marker。
- [ ] 并发测试有固定同步点，不依赖随机 sleep 碰撞。
- [ ] 每个 race case 都检查事件数量、Store 状态和副作用次数。
- [ ] 失败能保存操作顺序、进程日志和 state snapshot。
- [ ] xdist 并行运行后仍能在单进程复现失败。

## 13. 测试域九：Hypothesis 性质和状态机测试

### 13.1 适合的模型

先建立小型 reference model，再让模型和真实实现执行同一动作：

| 模型 | 生成动作 | 永真性质 |
|---|---|---|
| EventLogModel | append、duplicate、archive、replay、非法事件 | ID 唯一、追加顺序、replay 等价 |
| TaskAttemptModel | dispatch、timeout、retry、late result、generation rollover | attempt/generation 单调，stale 不推进 |
| OrchestratorModel | dependency、fanout、fanin、rework、cancel、restart | 依赖未闭合不推进，fanin 一次，retry 有界 |
| ControlledActionModel | request、approve、apply、result 丢失、replay | 未授权不执行，operation 副作用 ≤1 |

### 13.2 测试方法和工具

- 工具：Hypothesis + pytest。
- 首个试点：EventLog replay 或 Task 状态迁移。
- 每次失败保存 Hypothesis 操作序列、seed、state dir 和事件日志。
- 最小反例确认后转成普通的固定回归测试。
- 对有外部副作用的模型只使用 fake sink，不调用真实 GitHub、Feishu 或 Provider。

### 13.3 域通过清单

- [ ] 生成输入有明确策略边界，不生成无意义噪声。
- [ ] 每个 invariant 在动作序列每一步检查，而不是只看最终状态。
- [ ] 失败反例可独立重放。
- [ ] Hypothesis case 不取代关键协议的可读回归 case。
- [ ] CI 运行预算、样本数和失败保存策略固定。

## 14. 测试域十：CLI、FastAPI、SSE、WebSocket 和 Web

### 14.1 CLI 测试

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| CLI-01 | `--help` 和参数错误 | pytest、subprocess | 帮助可用；错误参数非零且无状态副作用 |
| CLI-02 | validate/doctor/preflight | pytest | 返回码、JSON/文本报告和字段路径正确 |
| CLI-03 | emit/query/trace | pytest、临时 state dir | 使用 EventWriter/Store；查询结果可回读 |
| CLI-04 | start/stop/restart | subprocess、cleanup | 进程生命周期、state dir 和恢复关系正确 |
| CLI-05 | archive/recover/status | pytest | 只处理指定项目和 state dir；不跨项目读写 |
| CLI-06 | 未授权 action | pytest | 拒绝；不写 canonical state、不调用外部 sink |

### 14.2 API/Schemathesis 测试

ZaoFu 的 FastAPI 应用从 `/openapi.json` 暴露 schema。Schemathesis 用于生成合法、非法和边界请求；每个生成 case 必须使用隔离 state dir。

检查：

- 缺失、超长、Unicode、错误枚举和错误类型；
- 未知字段、错误 content type 和空 JSON；
- 错误 token、错误 project scope 和重复请求；
- response body 与 OpenAPI schema 一致；
- 服务器不产生未分类 500；
- mutation API 仍经过 token-gated ControlledAction；
- API 返回结果与 Store/EventLog 联合一致。

API case 只有同时满足以下条件才通过：

```text
状态码符合合同
+ response schema 符合合同
+ 无未分类 500
+ Store 没有越权变化
+ EventLog 有正确事件或明确无副作用
```

### 14.3 Web/SSE/WebSocket 测试

使用 Playwright + Docker，测试：

- Kanban、Task、Run、Trace、Graph、Loop、Inbox 投影；
- 多项目切换和 project scope；
- SSE cursor replay、archive、gap、断线重连；
- WebSocket origin、token、subprotocol 和 session scope；
- loading、empty、error、stale response；
- token-gated action、取消、重试、暂停和恢复按钮；
- 浏览器 network、console、trace 和截图。

浏览器 case 必须联合断言：

```text
EventLog
→ Canonical Store
→ API JSON/SSE
→ DOM
```

页面文字或 toast 不能单独作为 pass。

域通过清单：

- [ ] 所有关键 API 路由至少有正常、错误、越权和跨项目 case。
- [ ] SSE gap、cursor、archive 和断线恢复均有测试。
- [ ] Web mutation 都能关联到准确 action/event/operation。
- [ ] 浏览器测试在 Docker 中执行，失败保留 trace。
- [ ] 失败重试不掩盖 flaky；merge gate 默认不靠 retry 变绿。

## 15. 测试域十一：安全、权限和不可信输入

### 15.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| SEC-01 | 错误/过期/伪造 token | pytest、API client | 401/403 或明确拒绝；副作用为 0 |
| SEC-02 | project scope 越界 | pytest、双 project fixture | 不能读取、修改或投影另一项目状态 |
| SEC-03 | source root/state dir 逃逸 | pytest、path guard | `..`、绝对路径、symlink、TOCTOU 均拒绝 |
| SEC-04 | secret redaction | pytest、SSE/日志读取 | token、密钥、环境变量不出现在日志、DOM、trace 或 sidecar |
| SEC-05 | EventLog 篡改 | pytest、签名/hash | 检测到篡改并 fail-closed |
| SEC-06 | 外部 Issue/Feishu 恶意内容 | pytest、攻击 fixture | 内容作为不可信数据；不执行命令、不写 canonical state |
| SEC-07 | Provider transcript 恶意指令 | pytest、Mock Provider | 不能绕过 evidence、权限或 action gate |
| SEC-08 | artifact 文件名和内容注入 | pytest、临时文件 | 不触发 shell/path 越界；内容不会成为控制命令 |
| SEC-09 | 重复 ControlledAction | pytest、effect spy | operation 幂等；远端写操作最多一次 |
| SEC-10 | WebSocket origin/subprotocol | pytest-asyncio、Playwright | 非法 origin/token/session 被关闭 |
| SEC-11 | ZAP 动态扫描 | OWASP ZAP Docker | 隔离环境无 required 高危告警；告警可分类 |

### 15.2 安全通过判据

```text
任何未授权、越界或不可验证的外部副作用 = 0
任何 canonical state 越权修改 = 0
任何 secret 泄漏 = 0
```

ZAP 只能补 HTTP 层动态扫描，不能替代 token、scope、EventLog、ControlledAction 和 prompt injection 的精确 pytest 断言。

## 16. 测试域十二：可观测性、诊断和证据可回读

### 16.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| OBS-01 | Event/Task/Run/Attempt 关联 | pytest、event graph oracle | 关键事件可通过 ID 链接到同一运行上下文 |
| OBS-02 | Provider transcript 保存 | pytest、Mock Provider | 成功/失败/截断均有 transcript 或明确缺失原因 |
| OBS-03 | recovery diagnostics | pytest、subprocess | 崩溃 signal、恢复动作、旧状态和新状态可回读 |
| OBS-04 | trace/graph/loop projection | pytest、API client | projection 与 EventLog/Store 一致 |
| OBS-05 | error classification | pytest | ZaoFu、Provider、host、infra 错误分开分类 |
| OBS-06 | secret redaction | pytest、日志扫描 | 诊断中无敏感值 |
| OBS-07 | evidence manifest | pytest | case、commit、config、state、artifact、digest 全部可追踪 |
| OBS-08 | failure bundle | pytest、cleanup | 失败时保留足够证据；清理不删除必要诊断 |

### 16.2 域通过清单

- [ ] 任一失败都能从 case_id 找到 Run/Task/Event/Attempt。
- [ ] 诊断不能覆盖或修改 canonical truth。
- [ ] 日志、transcript、trace、artifact 和 snapshot 有明确来源和 digest。
- [ ] 证据目录不会被下一次运行复用污染。
- [ ] secret redaction 在日志、SSE、Web DOM、trace 和 sidecar 全链路生效。

## 17. 测试域十三：性能、容量、长期运行和资源泄漏

### 17.1 性能测试方法

性能层必须在功能正确性稳定、有基线之后执行。建议使用 k6 或现有性能脚本：

| 方法 ID | 场景 | 工具 | 预期结果判定 |
|---|---|---|---|
| PERF-01 | 多客户端读取 Kanban/Trace | k6 | 错误率、p95/p99 不超过批准阈值 |
| PERF-02 | SSE 连接和重连风暴 | k6、Playwright | 断线恢复率达标；无事件遗漏或重复 |
| PERF-03 | 大 EventLog/archive 查询 | k6、临时大 fixture | 延迟、内存和 cursor 正确；不返回半写数据 |
| PERF-04 | 大 fanout/fanin | pytest、Mock Provider | child 数达到目标；无重复 dispatch、状态倒退 |
| PERF-05 | 长时间 mock run | soak runner | 无内存、文件句柄、tmux、端口和任务泄漏 |
| PERF-06 | provider timeout pressure | pytest/k6 | 超时有界；恢复队列不无限增长 |

k6 的 `check` 需要绑定 `threshold`，否则单个 check 失败不一定使整个执行失败。性能通过必须同时检查：

- HTTP/SSE 错误率；
- p95/p99 延迟；
- SSE 断线恢复和事件完整性；
- EventLog/Store 最终一致性；
- 进程、tmux、端口、锁和临时目录清理。

### 17.2 资源清理清单

- [ ] 测试前记录已有 tmux、端口和进程，测试后只允许回到基线。
- [ ] 每个 run 使用 `/tmp/zf-<purpose>-<utc-timestamp>/`。
- [ ] 每个 Web/Provider 进程有 owner、PID、启动时间和 cleanup handler。
- [ ] SIGTERM、SIGKILL、pytest timeout 和 assertion failure 后都有外层清理。
- [ ] 不删除当前开发 `.zf/`、活动 worktree 或用户文件。
- [ ] `simulation.done` 只用于模拟运行，并在结束后关闭模拟资源。

## 18. 测试域十四：安装、兼容、技能和文档入口

### 18.1 测试方法和工具

| 方法 ID | 测试方法 | 工具 | 预期结果判定 |
|---|---|---|---|
| CMP-01 | wheel/sdist 构建 | uv/build、临时 venv | 构建成功；产物可安装 |
| CMP-02 | 干净环境 CLI smoke | uv、subprocess | `zf --help/validate/doctor` 返回正确 |
| CMP-03 | extras 组合 | CI matrix + uv 或 Nox | dev/web/stream-json/feishu 依赖闭合 |
| CMP-04 | Python 版本矩阵 | CI matrix | 支持版本上的 T0 smoke 一致 |
| CMP-05 | 旧 zf.yaml | pytest fixture | 明确迁移或明确拒绝；不静默启用旧行为 |
| CMP-06 | 历史 events/archive replay | pytest fixture | 新版本可读取或给出可诊断迁移错误 |
| CMP-07 | skills source/materialization | pytest、provenance tests | `skills/` 为源；分发副本可验证同步 |
| CMP-08 | 文档命令一致性 | manual-docs、pytest | 命令、路径、schema 与当前代码一致 |

### 18.2 域通过清单

- [ ] 安装测试在干净环境运行，不依赖当前 editable 状态。
- [ ] extras 缺失时错误明确，不出现 import 半成功。
- [ ] 旧配置、旧事件和 archive 的兼容策略有测试和证据。
- [ ] 文档中的命令不会默认写入真实 state dir。
- [ ] 缺失的规范文档不被假定为存在；测试 oracle 以当前代码、schema 和契约为准。

## 19. 测试域十五：完整 TestKit 运行流程

一次 ZaoFu 自身完整测试 run 按以下顺序执行：

### Step 0：冻结测试输入

- [ ] 记录 ZaoFu commit、dirty worktree 状态、`zf.yaml` digest 和 `uv.lock` digest。
- [ ] 分配唯一 `case_id`、run ID、临时 state dir、端口和 tmux 前缀。
- [ ] 固定 Python、Node、Playwright、Docker image 和 Provider mock 脚本版本。
- [ ] 启用外部 sink spy，默认禁止真实 push、merge、远端写入和部署。

### Step 1：T0 配置和静态预检

```bash
uv sync --extra dev --extra web
uv run ruff check src tests
python3 -m compileall -q src tests
uv run zf validate --path zf.yaml
uv run zf validate --cold-start
uv run zf skills doctor
uv run zf doctor
python scripts/dev-verify.py plan --base dev
```

判定：所有 required 检查成功；配置错误、依赖错误和静态错误不能进入后续昂贵层。

### Step 2：T1 Kernel 和 EventLog/Store

```bash
uv run pytest <affected-tests> -q --no-cov -m 'not host and not real_provider'
```

覆盖 schema、EventLog、Store、state transition、evidence、ControlledAction 和直接调用者。共享边界变更时补跑：

```bash
bash scripts/dev-premerge-gate.sh
```

判定：状态、事件、evidence 和 forbidden effect 全部符合合同。

### Step 3：T2 Runtime 和 Mock Provider

- [ ] 运行正常 workflow、失败 workflow、fanout/fanin、rework/retry、cancel/pause/resume。
- [ ] 使用 scripted provider，禁止真实模型和远端服务。
- [ ] 验证完整 terminal gate 和不允许的 self-completion。
- [ ] 保存 transcript、EventLog、Store snapshot 和 diagnostics。

### Step 4：T3 Recovery、故障注入和并发

- [ ] 先执行 baseline。
- [ ] 逐个启用命名 failpoint。
- [ ] 使用独立 subprocess 崩溃并重启。
- [ ] 运行 barrier/multiprocessing 竞态 case。
- [ ] 比较恢复结果与 baseline，检查副作用次数和资源清理。

### Step 5：T4 API/Web/CLI

- [ ] 运行 Schemathesis API schema/negative tests。
- [ ] 运行后端 EventLog → Store → API/SSE truth-chain tests。
- [ ] 使用 Docker Playwright 运行关键用户操作和 SSE/WebSocket case。
- [ ] 检查 DOM、network、console、trace 与后端 canonical truth。

### Step 6：T5/T6 显式层

- [ ] 需要时运行 tmux/host tests。
- [ ] 需要时运行真实 Provider canary。
- [ ] 发布或定时运行 k6、ZAP、安装矩阵和 mutation audit。
- [ ] 这些层失败必须分类为 provider/host/perf/security/compatibility，不得改写成普通功能绿灯。

### Step 7：统一聚合和清理

- [ ] 聚合 JUnit/JSON、coverage、Hypothesis 反例、Playwright trace、ZAP/k6 报告。
- [ ] 检查 canonical state、EventLog、artifact/digest 和 projection。
- [ ] 检查 forbidden events、forbidden states 和 external effects。
- [ ] 检查遗留进程、tmux、端口、锁、worktree 和临时 state。
- [ ] 只有所有 required case 通过且无 flaky/infra error 才生成绿色结果。

## 20. 失败结果的处理

### 20.1 失败分类

| 分类 | 说明 | 是否阻断 | 处理方式 |
|---|---|---:|---|
| `test_fail` | ZaoFu 行为违反预期 | 是 | 保存证据并创建 Self-Issue |
| `contract_fail` | schema/event/store/gate 合同违反 | 是 | 优先修复；跑 premerge gate |
| `recovery_fail` | 重启后状态不等价或副作用重复 | 是 | 保存 crash point 和恢复快照 |
| `security_fail` | 越权、泄密或危险 sink 非零 | 是 | 立即隔离并创建安全 Issue |
| `flaky` | 重试后通过或结果不稳定 | 是 | 按失败处理，增加并发/时序证据 |
| `provider_fail` | Provider/凭据/网络失败 | 按层级 | 不改写为 ZaoFu 功能通过 |
| `host_fail` | tmux、Docker、浏览器或宿主能力缺失 | 按层级 | 记录环境证据，单独重跑 host tier |
| `infra_error` | 测试基础设施本身错误 | 是 | 修复 runner/fixture/清理问题 |
| `not_run` | required case 未执行 | 是 | 不能生成完整绿色结果 |

### 20.2 ZaoFu 自身问题进入 `/issue`

测试失败后至少保存：

```text
case_id
ZaoFu commit / dirty state
zf.yaml digest
state_dir 路径和快照
Run/Task/Attempt/Generation/Operation ID
复现命令
Given/When/Then/Forbidden
期望状态与实际状态
EventLog 和 Store 证据
artifact/ref/digest
Provider transcript
stdout/stderr/trace/screenshot
失败分类和是否稳定复现
```

然后进入：

```text
测试失败
→ 保存可重放 fixture 和 evidence
→ /issue
→ 人工确认 Self-Issue
→ 只读 triage
→ proposal approval
→ Fix Run
→ 受影响测试 + 直接调用者 + 共享契约
→ independent verify
→ judge
→ verified candidate
→ owner review
→ 人工执行 push/PR/merge
```

Agent、Provider、Web 页面或测试报告不能自行把失败改成完成，也不能直接修改 canonical state。

## 21. P0/P1/P2 测试清单

### P0：每次核心变更必须保护的边界

- [ ] 合法和非法 `zf.yaml` 验证。
- [ ] 自定义 `project.state_dir` 全入口隔离。
- [ ] EventLog append/schema/causation/duplicate/replay。
- [ ] Task/Feature/Session/Role Store 所有权和原子写入。
- [ ] 重复唤醒不重复 dispatch。
- [ ] stale attempt/generation 不覆盖当前状态。
- [ ] 缺失或错误 evidence 不能完成。
- [ ] fanout/fanin/rework/retry 有界且幂等。
- [ ] baseline + commit-point crash + restart 等价。
- [ ] ControlledAction 未授权和重复副作用为零。
- [ ] API/SSE/Web DOM 与 EventLog/Store 一致。
- [ ] 测试结束无遗留进程、tmux、端口、锁和临时状态。

### P1：日常或合并前增强

- [ ] Hypothesis EventLog/TaskAttempt/Orchestrator/Action 状态机。
- [ ] Schemathesis API 正负向和状态序列。
- [ ] 多进程锁、archive、watcher、fanin 竞态。
- [ ] Provider/transport/workdir/tmux mock 生命周期。
- [ ] 损坏文件、半写事件、只读目录和磁盘错误。
- [ ] WebSocket origin、SSE gap 和断线恢复。
- [ ] 完整 failure bundle 和诊断关联。
- [ ] Docker Playwright 关键 Web 流程。

### P2：发布或周期性审计

- [ ] k6 性能、压力、spike、soak。
- [ ] OWASP ZAP baseline 和认证后扫描。
- [ ] mutmut 核心模块 mutation audit。
- [ ] Python/extras/wheel/sdist 安装矩阵。
- [ ] 真实 Codex/Claude Provider canary。
- [ ] 多服务部署成熟后再引入 Chaos Toolkit。

## 22. 最终通过标准

以下标准裁决被测 ZaoFu 的完整测试 run 是否通过，不是“框架已搭建”的定义。框架能如实输出 fail、缺证、环境阻断也有价值；harness failure 则仍需独立定位。一次完整 run 只有同时满足以下条件才算通过：

```text
所有 required case 的 Then 成立
+ 所有 Forbidden 结果均未发生
+ Canonical Store、EventLog、artifact 和 projection 证据一致
+ replay/restart 后最终状态与 baseline 等价
+ stale/duplicate/乱序场景没有非法推进
+ 未授权和重复外部副作用为 0
+ 没有仅靠 retry 通过的 flaky case
+ 没有未分类 infrastructure_error 或 not_run
+ 测试资源全部清理
```

覆盖率、测试数量、耗时、token 成本和 Provider 成功率都只能作为辅助信息，不能单独把 ZaoFu 判定为正确。

本方案的最小可执行闭环是：

```text
固定输入
→ pytest/Playwright 执行
→ EventLog + Store + evidence + projection 取证
→ 明确 pass/fail
→ 失败保存可重放证据
→ /issue → Fix Run → 测试 → verify → candidate → 人工 merge
```
