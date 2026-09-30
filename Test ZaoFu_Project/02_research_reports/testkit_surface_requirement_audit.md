# TestKit 外部表面：原要求与实现边界审计

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/zap_launcher.py`。ZAP/k6/host 后续能力已有增量；早期工具范围保留作历史审查，当前长样本和实机验收仍按 K613。

审计日期：2026-09-14。读取时 HEAD 为 `142a62a`，共享工作树包含尚未提交的 TestKit 改动。本报告按 research 技能追到测试函数、工具实现、注册表和 CI；只读源码，没有运行测试、容器、Provider 或网络，没有引用 `steps.md` 的 done 作为证据。父线程阶段 J 的执行结果由父线程单独报告，不能当成本报告执行过这些域的证明。

范围限定为原 [§10](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:428>)、[§14](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:583>)、[§15](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:651>)、[§17](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:702>)、[§18](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:734>)及 §21 对应条目；不重新审查 Kernel/Hypothesis。下面 `WEB-*`、`API-*` 是给原文无编号的检查项加的本报告索引，不是新增要求。

## 判定口径

- **已实现**：已读到具体调用和断言；不等于本次通过。
- **已有红灯探针**：已有明确生产边界断言及 strict xfail；本次不复现、不修复，不能把新增探针当作新增需求，也不能仅凭装饰器断言当前生产一定仍失败。
- **显式入口，本次未运行**：实现可调用，host/release 的实际结果未在本次核验；不写成缺实现，也不写成发布通过。
- **部分/明确缺口**：已审查入口确实只实现了原要求的一部分；限定到这些入口，不声称全仓库绝无其他测试。
- **证据未闭合**：已存在相关测试，但断言或注册范围不足以推出整个条目；不是新增生产 Issue，也不自动启动补测。

[S5 注册表][S5]、[S6 注册表][S6]、[S7 注册表][S7]、[S10 注册表][S10]采用显式路径；[runner][runner]清空 pytest 默认 addopts 后只执行选中路径，所以 S10 的 perf 测试实际会进入 runner。普通 pytest 的 host/perf/real_provider 排除规则见 [pyproject.toml](/home/uisee/zaofu_v7/pyproject.toml:43)。[CI][CI]只有 `--extra dev --extra web` 和 `--stage all`，没有 Docker browser、真实 Provider、ZAP/k6 host 或 Python/extras matrix job。注册与未注册是执行选择的区别，不是测试存在与否的区别。

## §10 Provider / transport / workdir

| 原要求 | 实际测试/调用及断言 | 状态与边界 |
|---|---|---|
| PRV-01 adapter 命令参数 | [test_model_reasoning_effort_overrides_global_codex_config](/home/uisee/zaofu_v7/tests/test_backend_adapters.py:52)、[restricted sandbox/path 参数](/home/uisee/zaofu_v7/tests/test_backend_adapters.py:111)校验模型推理配置及权限 argv；[role-scoped env](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:286)校验各角色环境不串用。 | 已实现，[S5]。无需再造 adapter 测试框架。 |
| PRV-02 Provider 不存在 | [test_doctor_provider_reports_missing_codex](/home/uisee/zaofu_v7/tests/test_cli_doctor.py:53)mock `which=None`，调用真实 CLI main，断言非零和 `codex command not found`。 | 仓库已有诊断测试；该文件未在当前 SUITES。此函数没有 canonical 前后快照，不能单独证明全部副作用为零。 |
| PRV-03 正常 spawn/send/receive | [session/resume 测试](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:381)实际调用 spawn/send_task 并核验 prompt/session；[capture_log](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:493)回读输出文本。 | 已实现 mock transport 调用，[S5]；[ScriptedMockProvider.invoke](/home/uisee/zaofu_v7/tests/testkit/providers.py:40)只是返回预置 receipt，不能单独作为生产 transport 证明。 |
| PRV-04 正常 stream-json | [text block](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:523)、[tool-use](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:540)、[result usage](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:561)通过注入 SDK message 验证事件和字段。 | 已实现，[S5]；这里是 SDK message 层，不能称为完整真实 CLI 字节流 canary。 |
| PRV-05 坏 JSON/截断/重复 | [malformed stream → blocked](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s5.py:115)校验 task identity/error 且无 terminal event；[provider completion fact](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s5.py:64)经过 drain/run_once 后 Task 仍 in_progress。[partial delta 去重](/home/uisee/zaofu_v7/tests/test_headless_claude_partial_stream.py:70)、[max_tokens incomplete](/home/uisee/zaofu_v7/tests/test_headless_claude_partial_stream.py:94)、[tailer malformed](/home/uisee/zaofu_v7/tests/test_session_tailer.py:168)另有具体断言。 | 部分覆盖已实现。后两个文件不在 [S5]；S5 的 `duplicate-output` 只是固定字符串/receipt 分类，不是重复帧穿过终态 gate 的联合证明。协议异常完整闭包未证明；不得误写成“全部缺测试”。 |
| PRV-06 非零退出 | [test_background_dispatch_preserves_claude_api_policy_error](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:226)注入 `Process exited with code 1` 及 transcript API error；[exception event](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:189)断言 blocked/context/transport_error。 | 已实现代表性失败分类，[S5]；不是每种 Provider 非零退出的真实进程执行证据。 |
| PRV-07 timeout/stuck | [test_timeout_emits_agent_timeout](/home/uisee/zaofu_v7/tests/test_transport_stream_json_status.py:239)挂起 query、0.1s timeout，断言 agent.timeout 和 is_alive=false；[stuck heartbeat](/home/uisee/zaofu_v7/tests/test_transport_stream_json_stuck_parity.py:45)核验进展标识。 | 已实现，[S5]。未运行真实 Provider 不构成此 mock 要求缺口。 |
| PRV-08 resume | [first session / second resume](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:381)及 [crash respawn same session](/home/uisee/zaofu_v7/tests/integration/test_pane_crash_recovery.py:240)断言原 session ID、`--resume`、无新 `--session-id`。 | 已实现，[S5]，mock/隔离 session 文件。 |
| PRV-09 tmux pane 消失 | [crash_emits_worker_respawned_event](/home/uisee/zaofu_v7/tests/integration/test_pane_crash_recovery.py:289)经真实 run_once 断言 respawn 和 worker state；[真实 pane 探针](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s5_host.py:94)校验 terminate_window 后 pane 不存活、session 仍存活。 | mock 已实现，[S5]；host 显式入口且 ZF-TEST-017 strict xfail，本次未运行。已有红灯探针，不是 tmux 工具缺实现。 |
| PRV-10 workdir 初始化失败 | [test_worktree_mode_runs_declared_setup_and_fails_closed](/home/uisee/zaofu_v7/tests/test_workdirs.py:1318)创建临时 Git/worktree，setup=`exit 7`，断言带角色的 workdir 错误。 | 已实现 setup 失败，[S5]；不能将这个测试扩大为所有权限故障与 dispatch 状态快照均已覆盖。 |
| PRV-11 worktree 隔离 | [derived state root](/home/uisee/zaofu_v7/tests/test_workdirs.py:1159)、[root outside state 拒绝](/home/uisee/zaofu_v7/tests/test_workdirs.py:1144)、[non-default state root](/home/uisee/zaofu_v7/tests/test_workdirs.py:199)核验配置和路径边界。 | 已实现，[S5]。 |
| PRV-12 cleanup | [test_clean_removes_only_own_state_dir_worktrees_and_branches](/home/uisee/zaofu_v7/tests/test_stop_clean_workdirs.py:40)实际调用 CLI stop 的 cleanup helper，验证本项目删除、其他项目保留；[可重新使用 branch](/home/uisee/zaofu_v7/tests/test_stop_clean_workdirs.py:56)覆盖残留冲突。 | 仓库已有具体清理测试，未纳当前 SUITES；成功/失败/取消三类触发到 cleanup 的完整闭包未由这两个函数证明。不要新增另一套清理工具。 |
| PRV-13 token/time/cost | [global budget 阻断 dispatch](/home/uisee/zaofu_v7/tests/test_cost_budget_enforcement.py:201)核验 Task 仍 backlog 和 budget/skip events；[token stop 幂等](/home/uisee/zaofu_v7/tests/test_workflow_budget_guard.py:537)核验仅 terminate 一次。 | 仓库已有实现；这些文件未直接纳当前 SUITES。成本统计本身不是预算强制断言。 |

§21 P2 的真实 Codex/Claude canary 也有现成 [generic_workflow_real_provider_drill.run](/home/uisee/zaofu_v7/tests/e2e/generic_workflow_real_provider_drill.py:304)，显式 `--confirm-real`、backend 二选一、单次 Provider turn、timeout 和 cleanup。本次未执行，且不能把 S5 host 的 `--version/--help` 称为真实任务 canary。此 drill [写 provider-raw.jsonl 后清理临时根](/home/uisee/zaofu_v7/tests/e2e/generic_workflow_real_provider_drill.py:280)，输出 safe_audit 排除了 raw_stdout/stderr；仅运行它并保存 stdout，不足以兑现原 §10.3 的持久 transcript 证据要求。

## §14 CLI / API / SSE / WebSocket / Web

| 原要求 | 实际测试/断言 | 状态与边界 |
|---|---|---|
| CLI-01 help/参数错误 | [test_zf_help_smoke_has_success_receipt_without_state_side_effect](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10.py:16)调用 subprocess CLI，校验退出码/receipt/输出；[restart unknown role](/home/uisee/zaofu_v7/tests/test_cli_restart.py:70)校验非零。 | help 已纳 [S10]；错误输入已有具体测试，但不是所有 CLI 选项矩阵。 |
| CLI-02 validate/doctor/preflight | [validate missing project](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:191)、[doctor custom state](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:257)核验返回码、输出及无默认 `.zf`。 | 已实现，S2。 |
| CLI-03 emit/query/trace | [emit/events](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_cli.py:25)回读事件及 event_index；[trace causation](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:475)核验自定义 state 与因果链。 | 已实现，S6/S2。 |
| CLI-04 start/stop/restart | [start lock/events/stop](/home/uisee/zaofu_v7/tests/test_cli_start.py:983)、[restart state-dir](/home/uisee/zaofu_v7/tests/test_cli_restart.py:82)、[tmux boot failure cleanup](/home/uisee/zaofu_v7/tests/test_cli_start.py:1072)已有入口断言。 | 仓库已有，多为 dry-run/注入；文件未直接纳当前 SUITES。不可声称完整真实 CLI 进程生命周期已由 S6 执行。 |
| CLI-05 archive/recover/status | [retention/reconcile/clean](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:331)核验指定 state 及保留 truth；[recover 显式 state mismatch](/home/uisee/zaofu_v7/tests/test_cli_recover_workflow.py:263)拒绝不匹配。 | S2 已覆盖多命令，recover 文件另有测试；这属于复用现有测试/选择执行层。 |
| CLI-06 未授权 action | [unknown authorized action 拒绝](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:322)经 API 检查 unknown_action/无新事件；[全注册 action 无 token 拒绝](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:141)保护公共 action 路径。 | 公共授权 gate 已实现，[S7]；这些是 API client 测试，不单独证明 CLI 授权调用者。 |
| API-01 schema 正负向/边界请求；§21 P1 状态序列 | [test_read_only_api_schema_cases_are_valid / test_events_api_schema_inputs_are_read_only](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_schemathesis.py:54)调用 `case.call_and_validate`，校验业务字段、200/422、events bytes 不变。 | **明确部分**：仅 GET `/health`、`/api/provider-health`、`/api/state`、`/api/events`，max_examples=3，coverage phase disabled；未实现 mutation 状态序列。module fixture 共用一个临时 state，满足跨 checkout 隔离，但不是原文每个生成 case 独立 state。不能把工具已安装算成该条 done。 |
| API-02 token/scope/repeated mutation 及 Store/EventLog 一致 | [update 幂等](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:246)、[archive](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:185)、[capture/replay](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:315)断言 401/403、成功、同 key replay、不同 payload 409、canonical 结果和精确事件数量。 | 已实现 [S6]；不要为此重建 API 授权框架。 |
| API-03 跨项目 | [same task id 隔离](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:120)比较两个 state；[project action envelope](/home/uisee/zaofu_v7/tests/test_workspace_projects.py:288)在同一 app 中拒绝错 project_id，写 B 后 A 无该 Task。 | 已实现，[S6]。 |
| API-04 所有关键路由正常/错误/越权/跨项目 | [OpenAPI metadata 检查](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:31)遍历 operationId/response/path-param；[S7 action registry](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:137)遍历缺 token 拒绝。 | **证据未闭合**：metadata 遍历不是逐路由行为测试，缺 token 全覆盖不是每个路由四类完整覆盖。原文未给“关键路由”清单，应先固定有限清单、复用现有函数，不自动要求全 API 组合穷举。 |
| API-05 freshness/read-only 投影 | [bounded/fresh events](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_projection_contract.py:72)核验 watermark、lag、ledger 不变；[joiner failure/timeout](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_projection_contract.py:175)及 [fallback metadata](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_projection_contract.py:276)有严格回归。 | 已实现；ZF-TEST-022/012 属已有红灯探针，[S6]。 |
| WEB-01 EventLog → Store → API/SSE → DOM | [test_docker_drag_matches_canonical_store_event_api_sse_and_dom](/home/uisee/zaofu_v7/tests/e2e/test_testkit_browser_truth_chain.py:34)核验一次 task.updated、action.event_id/causation、TaskStore、API、两浏览器 SSE、DOM/reload；[真实 spec](/home/uisee/zaofu_v7/web/tests/testkit-truth-chain.spec.ts:7)原生拖拽、第二页观察、Runs regression 显示。 | **已有实质真值链，显式 host，本次未运行**。不是单纯 toast 测试；无需再补一条同义链。 |
| WEB-02 Kanban/Task/Run/Trace/Graph/Loop/Inbox | 上述 host case 已含 board 状态矩阵、task detail、Runs regression artifact/事件；[Trace spec](/home/uisee/zaofu_v7/web/tests/traces-page.spec.ts:5)和 [Loop spec](/home/uisee/zaofu_v7/web/tests/loop-page.spec.ts:200)另有 mock API 渲染断言。 | 真值链的代表面已实现；[专用 config](/home/uisee/zaofu_v7/web/playwright.testkit.config.ts:5)仅选一个 spec。不能从 mock UI 或 backend snapshot 推出原列表每一页面都已有原生真值闭包；有限面清单的完成证据仍不足。 |
| WEB-03 SSE cursor/archive/gap/reconnect | [cursor replay](/home/uisee/zaofu_v7/tests/test_web_sse.py:98)、[archive](/home/uisee/zaofu_v7/tests/test_web_sse.py:138)、[gap](/home/uisee/zaofu_v7/tests/test_web_sse.py:177)、[rotation sequence](/home/uisee/zaofu_v7/tests/test_web_sse.py:211)断言真实 `_tail_events` 输出；browser spec 首次连接强制中断后实际 EventSource 重连。 | 后端已实现 [S6]；原生浏览器重连为显式 host。后端 `_collect` 用 fake Request，不能等同多客户端重连风暴（那属于 PERF-02）。 |
| WEB-04 WebSocket origin/token/subprotocol/session | [valid ticket / single-use](/home/uisee/zaofu_v7/tests/test_web_terminal_routes.py:364)接受 `zf-terminal-v1`，重放关闭 1008；[wrong origin/scope](/home/uisee/zaofu_v7/tests/test_web_terminal_routes.py:407)明确 1008；[query token 拒绝](/home/uisee/zaofu_v7/tests/test_web_terminal_routes.py:352)。 | 具体协议授权已实现，[S6]；不能称为浏览器真实 terminal transport E2E。 |
| WEB-05 loading/empty/error/stale；多项目切换 | [Trace empty/按需加载 spec](/home/uisee/zaofu_v7/web/tests/traces-page.spec.ts:5)、[stale persisted project](/home/uisee/zaofu_v7/web/tests/stale-project-selection.spec.ts:6)已有 UI 测试。 | 仓库有测试但不在专用 TestKit browser config/CI 中；完整关键面状态清单尚未闭合，不应写“前端无测试”。 |
| WEB-06 取消/重试/暂停/恢复按钮与 action/event/operation | [unauthorized action matrix](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:113)覆盖 workflow-cancel/run-pause/run-resume 等；现有原生 browser case 的实际 UI mutation 是 update-task 拖拽。 | 授权后端有覆盖；已审查专用 browser 入口不含四类按钮的完整原生真值链，原要求仍部分。无需为每个 alias 重复建旅程。 |
| WEB-07 network/console/trace/screenshots，禁止 retry 变绿 | [browser spec](/home/uisee/zaofu_v7/web/tests/testkit-truth-chain.spec.ts:13)收集 pageerror、console、HTTP 5xx；[config](/home/uisee/zaofu_v7/web/playwright.testkit.config.ts:3)retries=0、trace=on；host 校验 expected=1、unexpected/skipped/flaky=0、trace/screenshot、port/process/container 清理。 | 已实现显式 host。consoleErrors 被保存但不直接断言为空；原文要求保留诊断，不能自动增加“所有 console warning 必须为零”的新验收。 |

CLI workflow 初始化额外已有 [ZF-TEST-024/025/026 探针](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_cli_flow.py:16)，包装现有 `test_cli_flow` 断言并纳 [S6]。它们是已有产品行为红灯，不是 Schemathesis 状态序列缺口的替代品。

## §15 安全

| 原要求 | 实际测试/断言 | 状态与边界 |
|---|---|---|
| SEC-01 错误/过期/伪造 token | [wrong/missing/Bearer matrix](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:194)断言拒绝和 ledger 不变；[cookie unlock/rate limit](/home/uisee/zaofu_v7/tests/test_web_server.py:3063)覆盖 passcode；[ticket reuse](/home/uisee/zaofu_v7/tests/test_web_terminal_routes.py:364)覆盖单次 capability。 | 错误 token 与重放已实现。已审查测试未推进时间验证实际 TTL 过期；[ticket unit](/home/uisee/zaofu_v7/tests/test_web_terminal_runtime.py:889)的 `invalid or expired` 是已消费后的错误文字，不是时间过期实验。过期要求证据未闭合。 |
| SEC-02 project scope | [project envelope/snapshot/action isolation](/home/uisee/zaofu_v7/tests/test_workspace_projects.py:288)及 [same task id](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:120)。 | 已实现代表性读写隔离，[S6]。不能据此声称所有 route 的 scope 已穷举。 |
| SEC-03 path/symlink/TOCTOU | [PathGuard 二次检查](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:31)；[真实 binary/fanout/directory sink swap](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7_toctou.py:32)在 guard 后换 symlink，核验外部目标不得被改写。 | guard 与真实 sink 探针已实现，[S7]；ZF-TEST-014/015/016 为已有 strict xfail。无需增加新 security 框架以代替修复。 |
| SEC-04 secret redaction | [SSE secret](/home/uisee/zaofu_v7/tests/test_web_sse.py:256)、[task detail](/home/uisee/zaofu_v7/tests/test_web_server.py:1005)、[diagnostic sidecar](/home/uisee/zaofu_v7/tests/test_redacted_diagnostics.py:67)、[action nested secret](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7.py:347)均断言敏感值消失。 | 多 sink 已实现，S6/S7/S10；专用 browser case 未播种 secret 做 DOM/trace 联合否定断言，所以原列出的全部载体仍不能一概判 pass。 |
| SEC-05 EventLog 篡改 | [test_verify_tampered](/home/uisee/zaofu_v7/tests/test_security_signing.py:24)验签失败；[SSE tampered signed event skipped](/home/uisee/zaofu_v7/tests/test_web_sse.py:272)验证输出不含篡改事件。 | 已实现精确签名/消费边界，S7/S6。本报告不重新审计 ledger 模型。 |
| SEC-06 Issue/Feishu 不可信输入 | [external issue sink](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7_untrusted.py:51)断言恶意文本仍是 sidecar、信任标签、无 shell/push；[Feishu note](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7_untrusted.py:101)断言 human.note 原样数据、sentinel 不出现。 | 已实现，[S7]。 |
| SEC-07 Provider transcript 恶意指令 | [test_provider_output_is_stored_as_evidence_and_never_executed](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7_untrusted.py:140)强制走 raw-output sidecar，回读恶意文本并检查无执行事件/sentinel；PRV-05 的 transport→Task 测试另护 terminal。 | 代表性数据/控制边界已实现，[S7]/[S5]；不把它包装成模型语义免疫承诺。 |
| SEC-08 artifact filename/content 注入 | [test_regression_case_id_cannot_escape_artifact_root](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s7_untrusted.py:342)调用实际 ControlledAction capture，测试 ../ 和 absolute case_id 不得在根外生成 JSON。 | 已有 ZF-TEST-021 strict xfail，[S7]；属于产品红灯。 |
| SEC-09 重复 ControlledAction / remote effect 最多一次 | [API update 幂等](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6.py:246)真实受控 action/event/store 一次；[operation identity](/home/uisee/zaofu_v7/tests/test_action_orchestrator.py:11)绑定 attempt/result；[local-only publication spy](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s8_remote.py:16)禁止执行 push/merge/PR create。 | 核心现有覆盖 S6/S7/S8；remote publication 的零调用不能充当“获准远端写后重复请求最多一次”证明。详细 action 崩溃/幂等模型归父线程，本报告不扩展。 |
| SEC-10 WebSocket | WEB-04 的 origin/ticket/session/subprotocol 断言。 | 已实现后端协议测试，[S6]；浏览器执行层另算。 |
| SEC-11 ZAP；§21 P2 baseline + 认证后扫描 | [run_zap](/home/uisee/zaofu_v7/tests/testkit/zap_launcher.py:210)已实现隔离目标、固定 digest、owned cleanup、receipt/report/实际 access log/真值 bytes 校验；[host test](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10_zap_host.py:20)是实际入口。 | **工具实现存在，本次未运行；原 baseline crawl 与认证扫描仍缺**。[fixed_plan](/home/uisee/zaofu_v7/tests/testkit/zap_protocol.py:39)只有 requestor→passiveScan-wait→report，固定两个 GET，无 spider/认证步骤。不能因启动器写了 token env 就称认证扫描。 |

ZAP 的 [test_owned_launch_risk_gate_and_cleanup](/home/uisee/zaofu_v7/tests/testkit/test_zap_launcher.py:79)与 [test_launch_faults_never_produce_green](/home/uisee/zaofu_v7/tests/testkit/test_zap_launcher.py:108)明确调用 FakeDocker，断言 `execution_kind=fake`；它们证明 launcher 判定及故障清理，不能证明 ZAP 实际请求、真实网络隔离或安全零告警。该已实现工具无需再重复建设；真实执行与缺失的 baseline/auth 场景应分别记账。

## §17 性能与资源

原文要求功能正确性稳定且有批准阈值后进入性能层；因此下列 release 缺口不能暗中塞入普通 P0 修复批次。[k6_smoke.js](/home/uisee/zaofu_v7/tests/testkit/k6_smoke.js:1)只有 1 VU、1 iteration、`latency.add(1)` 和布尔 check，完全没有 HTTP/SSE；[host test](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10_k6_host.py:30)使用 `--network=none`，校验正常 0/故意失败 99。因此它是 k6 运行/summary/threshold 证据，不是应用压测。

| 原要求 | 实际测试/断言 | 状态与有限剩余 |
|---|---|---|
| PERF-01 多客户端 Kanban/Trace，error/p95/p99 | [test_perf_budgets](/home/uisee/zaofu_v7/tests/test_web_perf_smoke.py:52)对 3,000 events/12 tasks 的 kanban、snapshot、task detail 本地调用计时。 | 已有宽松性能 smoke，[S10]；缺已审查入口中的多客户端 HTTP Trace/看板与 p95/p99 场景，不能用合成 k6 latency 填充。 |
| PERF-02 SSE 重连风暴及事件完整性 | WEB-03 有游标与一次浏览器重连；[browser SSE exact-once](/home/uisee/zaofu_v7/tests/e2e/test_testkit_browser_truth_chain.py:240)验证两个页面收到同一事件。 | 有功能正确性基线；当前 k6/专用 browser 入口无 storm workload、恢复率阈值。与“工具未运行”不同，场景仍部分。 |
| PERF-03 大 EventLog/archive 查询 | [10×10,000 read soak](/home/uisee/zaofu_v7/tests/test_web_perf_smoke.py:67)断言长度和 8 秒预算，SSE archive/cursor 已有独立功能测试。 | 已有容量读取 smoke；未联合实际 HTTP archive 大 fixture、内存、cursor 与半写数据断言。复用这些基线即可，不需要新压测平台。 |
| PERF-04 大 fanout/fanin | [test_fanin_waits_for_all_children_and_deduplicates_completion](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s3_fanout.py:20)实际两个 child，重复完成后只 synth 一次。 | 有功能基线；此入口不证明“达到批准大 fanout 目标”。原文未给 child 规模，不应擅自指定巨大规模或所有 fanout 变体。 |
| PERF-05 长时间 mock run/资源泄漏 | [1000-cycle restart](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4.py:297)断言一次 dispatch、稳定状态/事件、无 child leak；[4-task×250-cycle](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s4.py:335)已有 ZF-TEST-018 strict xfail；[projection repeated-read](/home/uisee/zaofu_v7/tests/test_web_perf_smoke.py:96)已有 ZF-TEST-011 strict xfail。 | 有 bounded soak，S4/S10；原文长运行内存/FD/tmux/port/Task 全资源监控不是这些循环测试已有结论。已知红灯与缺少长时资源场景分开。 |
| PERF-06 timeout pressure / recovery 队列有界 | [transport timeout](/home/uisee/zaofu_v7/tests/test_transport_stream_json_status.py:239)和 [background dispatch queue](/home/uisee/zaofu_v7/tests/test_transport_stream_json.py:147)已有 timeout 与排队上限断言。 | 已有基础边界，[S5]；当前 k6/性能入口未形成批量 timeout 到恢复队列容量的压力场景。 |

§17.2 的 cleanup 方法已有 [SIGTERM 忽略后 SIGKILL](/home/uisee/zaofu_v7/tests/test_testkit_s1_lifecycle.py:80)、[descendants cleanup](/home/uisee/zaofu_v7/tests/test_testkit_s1_lifecycle.py:116)、[保留 preexisting scope files](/home/uisee/zaofu_v7/tests/test_testkit_s1.py:430)以及原生 browser/ZAP 各自的 finally。它们证明具体所有权清理，不应再次从零造 cleanup checker；也不能升级成“每个长期负载后全部资源都已实测回到基线”。

[evaluate_k6_summary](/home/uisee/zaofu_v7/tests/testkit/performance.py:174)已校验有限数值、非零样本、显式 threshold、报告值与重算一致、exit code 0/99；threshold gate 已实现。§21 的压力/spike/soak 是尚未接上的应用场景，并非还缺一个 summary parser。

## §18 安装 / 兼容 / skills / docs

| 原要求 | 实际测试/断言 | 状态与有限剩余 |
|---|---|---|
| CMP-01 wheel/sdist 构建可安装 | [test_wheel_and_sdist_build_then_import_from_clean_target](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10.py:87)实际 uv build，两种包均 `uv pip install --no-deps --target`，在源码 cwd 外 import 并核验 zf.__file__ 来自 target。 | 已实现产物构建/安装/import，[S10]；不是完全无依赖污染的独立 venv 验证。 |
| CMP-02 干净 CLI help/validate/doctor | 同一 [安装测试](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10.py:164)调用 target 包 `main(['--help'])`；S2 validate/doctor 则运行当前环境源码。 | **明确部分**：target help 已有，干净依赖环境 validate/doctor 未在安装测试执行；两类测试不能拼成同一次干净安装闭包。 |
| CMP-03 extras dev/web/stream-json/feishu | [METADATA extra 声明检查](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10.py:121)只检查 Provides-Extra；[CI][CI]只安装 dev+web；[声明](/home/uisee/zaofu_v7/pyproject.toml:13)列出四组。 | **明确缺口**：当前 CI/S10 没有独立 extras 安装/依赖闭合矩阵，也没有缺 extra 时具体入口失败合同矩阵；`--no-deps` 不能证明依赖可解析。原要求不等于 2⁴ 组合穷举，应先固定支持组合。 |
| CMP-04 Python 支持版本 T0 一致 | [test_wheel_clean_install_matrix_covers_available_supported_pythons](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10.py:190)使用 `sys.executable` 加 `which('python3.12')`，缺 3.12 直接 fail；每个只安装 no-deps target 和 import。 | **明确部分**：没有 [CI][CI] strategy.matrix/setup-python，未验证每个版本 T0 smoke；当前解释器未被此测试固定为 3.11，因此不能无条件写成“3.11/3.12 完整矩阵”。原 `>=3.11` 未界定上界，先列支持版本，不自动扩展所有未来 Python。 |
| CMP-05 旧 zf.yaml 迁移/明确拒绝 | [legacy singular backend](/home/uisee/zaofu_v7/tests/test_config_loader_backends.py:121)核验旧 backend+replicas 仍解析；[removed native backend](/home/uisee/zaofu_v7/tests/test_config_loader.py:398)断言 ConfigError 带字段路径。 | 仓库已有具体兼容策略测试；两文件未直接纳当前 SUITES。不能把一个可 skip 的历史 example 代替全部兼容证据。 |
| CMP-06 历史 events/archive | [unsigned legacy 默认拒绝/显式 allow](/home/uisee/zaofu_v7/tests/test_events_log.py:196)分别断言 malformed 和可读取历史事件；[SSE archive replay](/home/uisee/zaofu_v7/tests/test_web_sse.py:138)保持 global cursor。 | 已有明确兼容用例，S2/S6；不由此要求维护所有历史 schema 的无限版本交叉积。 |
| CMP-07 skills source/materialization/provenance | [hash change detected](/home/uisee/zaofu_v7/tests/test_skill_provenance.py:270)、[enabled role materialization](/home/uisee/zaofu_v7/tests/test_skill_provenance.py:320)、[Claude project target](/home/uisee/zaofu_v7/tests/test_skill_provenance.py:481)校验来源、digest、manifest 和物化位置。 | 仓库已实现具体 provenance 测试；未直接纳当前 SUITES。不能只按 `.codex/skills` 目录存在判同步。 |
| CMP-08 文档命令一致 | [test_manual_cli_examples_and_execution_matrix_track_parser](/home/uisee/zaofu_v7/tests/test_manual_docs.py:76)及 [unknown option](/home/uisee/zaofu_v7/tests/test_manual_docs.py:82)调用 manual tooling；[currentness](/home/uisee/zaofu_v7/tests/test_manual_docs.py:62)校验无偏差。 | 已有明确工具与测试；未直接纳当前 SUITES，当前 [CI][CI]未单列 manual-docs。属于选择并执行既有验证入口，不是缺实现。 |

## 有限收口与退出判据

本次不能判“原方案全部实现/通过”，也没有依据把每个未见组合都转成新的工作项。后续仅保留以下有限差额；原文更高强度的发布要求若暂不纳本批，明确 defer 即可，不伪装 done：

1. **API/浏览器**：固定原 §14 的关键 route/页面/按钮清单，映射现有测试；补差额只围绕 Schemathesis mutation 状态序列与 case 隔离、缺失的关键真值链、明确 TTL/secret 载体断言。退出判据：清单各项有真实调用与合同断言；代表性链复用 WEB-01，不按 alias/页面视觉变体无限复制。
2. **Provider/CLI**：保留上表已经存在但未入 SUITES 的测试，决定适用执行层；PRV-05 的异常到终态 gate、PRV-12 的 cleanup 触发链等只列“证据未闭合”，先核对已有直接调用者再定是否补测。退出判据：原 PRV/CLI 条目都有有限测试节点和执行类别；不是每发现一个文件就全部纳普通 runner。
3. **ZAP**：固定 GET passive launcher 已完备到可执行入口，真实执行属于 host 待验证；baseline 与认证后扫描是原 P2 尚未实现的场景。退出判据：若本批只承诺工具能力，声明两类扫描 defer；若承诺原 P2 完整覆盖，须有实际隔离扫描/认证覆盖证据，不能用 FakeDocker pass 代替。
4. **k6/性能**：summary/threshold 和单次 Docker smoke 已实现；PERF-01..06 所列应用 load/spike/soak 与资源收敛只需一个批准的有限负载合同，不建设新通用平台。退出判据：功能基线稳定后，有实际应用请求/连接、批准 error/p95/p99 等指标及真值/资源终检；否则保留 release defer。
5. **安装**：CMP-01 现有 wheel/sdist target 测试复用；CMP-02..04 补齐或明确 defer 干净依赖环境 CLI smoke、受支持 extras/Python CI 组合。退出判据：每个承诺组合可安装、依赖闭合、指定 T0/help/validate/doctor 行为明确；METADATA/import-only 不再写为完整矩阵。
6. **已有红灯和执行证据**：012/022、024..026、014..016/021、017、011/018 分别保留现有回归节点；产品修复另行授权，不通过继续增加 TestKit 基建“解决”红灯。[runner verdict](/home/uisee/zaofu_v7/tests/testkit/runner.py:117)已把 strict xfail/skipped/not_run 当 fail；本报告静态审计不是一次通过 run。

§21 唯一在这些域明确写为后置条件的工具扩展是“多服务部署成熟后再引入 Chaos Toolkit”（[原文](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:925>)）。它不属于本次缺口，不据此追加工具愿望清单。以上收口不要求重新审查 Kernel/Hypothesis，也不授权运行任何 host/release 或外部副作用。

[S5]: /home/uisee/zaofu_v7/tests/testkit/suites.py:109
[S6]: /home/uisee/zaofu_v7/tests/testkit/suites.py:149
[S7]: /home/uisee/zaofu_v7/tests/testkit/suites.py:177
[S10]: /home/uisee/zaofu_v7/tests/testkit/suites.py:245
[runner]: /home/uisee/zaofu_v7/tests/testkit/runner.py:160
[CI]: /home/uisee/zaofu_v7/.github/workflows/zaofu-testkit.yml:14
