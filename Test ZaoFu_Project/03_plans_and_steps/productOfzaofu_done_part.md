# ZaoFu 测试框架已完成的产品项目测试梳理

更新时间：2026-09-30（保留原有记录，补充通用产品测试待完成部分；未运行新测试）

## 统计口径

> 2026-09-30 对齐：通用方案为 [ZaoFu_Product_TestKit.md](ZaoFu_Product_TestKit.md)，实现进展为 [Product_steps.md](Product_steps.md)。本表同时保留通用产品 fixture 和 ZaoFu 宿主工作流的历史检测证据，后者只证明 P-18 接线/宿主行为，不是生成产品的业务测试覆盖。下表汇总编号统一改为 R-01～R-27，避免与正式 P-01～P-18 测试域混淆。历史报告未在本次重跑。

本文只统计“由 ZaoFu Product TestKit / E2E 测试入口实际执行的产品项目或产品工作流”。测试框架的职责是把产品问题稳定检出、分层、留证和归因，不以所有结果都为 `pass` 为目标。

- **产品通过**：被测产品/产品工作流在该测试合同下满足断言。
- **检出 ZaoFu 产品问题**：测试断言真实失败，且能在 `ZaoFu_issue.md` 找到对应 `ZF-TEST-*`；已修复问题仍保留原始红证据和回归证据。
- **测试框架问题**：入口、夹具、选择器、报告分类或清理逻辑有问题，不能归因给产品。
- **环境阻断/未执行**：缺少 Docker、Provider 登录、工具、历史证据或预算，不伪造为产品通过。
- 纯 EventLog、Store、Orchestrator、Kernel 单元/契约测试不作为“产品功能通过”；若它们是产品闭环的支撑，会在结果中明确标为“框架支撑合同”。

主要证据来源：`steps.md` 的 K419/K424/K428–K435、K533–K541、K573–K586；`tests/product_testkit/`；`tests/e2e/`；`ZaoFu_issue.md`。

## 已完成的产品测试清单

| 编号 | 被测产品项目/业务部分（尽量具体） | 使用技术与测试入口 | 结果及归因 | 证据/边界 |
|---|---|---|---|---|
| R-01 | 通用库型产品基础链：产品源代码固定在 target commit，执行 foundation smoke，覆盖 P-01～P-05、P-17、P-18；校验产品输出和 artifact digest。 | Python disposable Git fixture；`tests/product_testkit/test_foundation_flow.py::test_generic_foundation_fixture_runs_p01_to_p05`；`ProductManifest`、`run_product_plan`、artifact capture。 | **产品通过**；同时证明 target commit、领域覆盖和产物索引可判定。 | 这是合成产品 fixture，不代表某个真实业务产品已发布。 |
| R-02 | 通用 API 产品：认证/状态、输入校验、状态变更、JSON 响应结构，以及不完整响应和不可达端点的区分。 | `tests/product_testkit/test_api_fixture_flow.py`、`test_api.py`；本地 HTTP fixture、API probe、JSON schema 断言。 | **正向产品 fixture 通过**；不完整 schema 会被判为产品失败；不可达端点被判为 harness/environment，不伪装成产品失败。 | 验证了 API 产品测试结果的归因边界。 |
| R-03 | API service 产品生命周期：启动、health contract、请求、停止、cleanup；服务启动后死亡、health timeout、缺少 executable。 | `test_api_service_fixture_flow.py`、`test_service.py`、`test_health.py`；ThreadingHTTPServer/子进程、health probe、cleanup receipt。 | **产品 service fixture 通过**；服务契约错误可判产品失败；缺少工具/启动失败可判环境失败。 | 覆盖产品服务的可启动性和收尾，不把宿主缺工具归给产品。 |
| R-04 | 产品持久化：SQLite migration/约束、重复写拒绝、backup/restore、快照和幂等。 | `test_persistence_fixture_flow.py`、`test_persistence.py`；SQLite、独立临时数据库、持久化 receipt。 | **产品 fixture 通过**；唯一约束、恢复内容和顺序命令均被验证。 | 这是产品持久化场景 fixture，不是 ZaoFu EventLog 自身测试。 |
| R-05 | 产品发布物：wheel/sdist 类型、非空、大小、digest、缺失输出；install→upgrade→rollback 生命周期和每阶段 receipt。 | `test_release.py`、`test_runner.py`；本地构建脚本、artifact index、digest recheck。 | **产品 release fixture 正向通过**；空 artifact、缺输出、阶段失败会被明确检出，不被统一 runner 绿化。 | 证明产品发布证据可审计；未执行远端发布。 |
| R-06 | reference product 全闭环：产品 smoke、build、ZAP 报告、release artifact、restart recovery、并发 worker、persistence、cleanup，覆盖 P-01～P-18。 | `tests/product_testkit/test_reference_product_closure.py`；一次性 Git reference fixture、统一 runner、Product Acceptance report、delivery readiness、failure-bundle。 | **reference product 通过**：Product Acceptance passed、delivery readiness allowed、artifact/security/recovery/concurrency/persistence 全有结果；但 `merge_authorized=false`，不会把测试绿灯直接变成合并授权。 | 这是 reference/synthetic product，只证明通用框架合同，不代表任意实际产品已通过质量验收。 |
| R-07 | reference product 故障归因：故意让产品 smoke 退出码为 17，确认产品失败阻断 issue merge，而不是 harness failure。 | `test_reference_product_closure.py::test_reference_product_failure_is_product_verdict_and_blocks_issue_merge`；统一 runner、`IssueMergeLoop`。 | **产品问题路径通过**：失败被分类为 product verdict 并阻断交付；框架仍写可复现 failure bundle。 | 该用例验证“测试能测出产品问题”本身是成功结果。 |
| R-08 | Product Acceptance 输入与候选门禁：缺报告拒绝、完整 report 绑定 spec/report/digest、candidate identity、target commit、重启后 replay 幂等、stale candidate 拒绝。 | `tests/e2e/test_zaofu_testkit_s8_e2e.py`；`bind_product_acceptance_report`、candidate preflight/creation、sidecar digest。 | **产品交付合同通过**；缺失/错绑/旧 candidate 会被阻断；完整 acceptance 才能进入 candidate gate。 | 主要是产品交付链的框架合同；不是某个业务断言全绿的替代品。 |
| R-09 | Issue→candidate→controlled merge→post-merge：regression artifact、Issue ingress、Fix Run 顺序、ShipService merge/conflict/idempotency、post-merge regression、triage mirror。 | `tests/product_testkit/test_issue_loop.py`、`test_delivery.py`、S9 K430/K433；本地隔离 Git、candidate preflight、post-merge runner。 | **本地产品交付链通过**：K430/K433 为 79 reported、78 passed、1 个既有 `ZF-TEST-020` strict xfail；产品失败或 post-merge regression 会阻断/暴露，不改写 merge 结果。 | 远端 PR/merge/push 未执行，属于明确降级边界。 |
| R-10 | 产品 CI 结果聚合：每个 tier 独立 evidence，deferred/not-run 不伪装 pass，聚合不隐藏 product failure；fail→pass 重跑分类 flaky。 | `test_ci.py`、`test_stability.py`、`test_report.py`、`test_runner.py`；统一 JSON/JUnit、prior result、rerun 对照。 | **测试判定通过**：产品失败、flaky、not-run、environment、harness 各自保留；不会因第二次通过而绿化首次失败。 | 这是产品测试结果可信度的支撑合同，实际产品结果仍以各域报告为准。 |
| R-11 | General 产品 live workflow：浏览器批准→`zf start`/Watcher/tmux→workflow invoke→同一 Run 的 Task/terminal/delivery，含 restart/cleanup。 | `tests/e2e/test_testkit_browser_live_runtime.py`；Docker Playwright、真实本地 CLI/Watcher/tmux、脚本 worker、EventLog/DOM 断言。 | **产品工作流通过的链路**；同 Run、Task identity、terminal、restart、cleanup 均有证据。 | 脚本 worker 只证明 transport/生命周期，不外推真实 Provider 语义。 |
| R-12 | Issue 产品 live workflow：Issue 请求、计划/批准、执行、候选/交付和浏览器终态；后台任务交错时按 task/workflow identity 选中当前任务。 | `test_testkit_browser_live_issue.py` 的 `issue` family；Docker Playwright、真实 runtime、tmux、EventLog 关联。 | **主链通过；曾检出并修复测试选择器问题 `ZF-TEST-086`（测试框架）**。修复后精确关联 task_id/flow marker；不能将完整四流程历史结果写成全绿。 | `ZF-TEST-086` 明确是测试框架问题，不归因产品。 |
| R-13 | PRD 产品 live：browser approval→独立 CLI/Watcher/tmux→7 个 child + 1 个 synth→9 次准入→candidate→terminal→restart/cleanup。 | K533、`test_testkit_browser_live_issue.py` 的 `prd` family；Docker Playwright、stdin/PID/briefing SHA 收据、`zf result submit`。 | **产品工作流通过**：K533 PRD live 1 passed/160.79s，Docker 1 passed/94.49s；同时保留并修正过 F1–F4 测试接线/预算/断言问题，不把它们归为产品缺陷。 | 真实 Provider 未参与；历史 `K584/K585` 的握手稳定性仍单独开放。 |
| R-14 | Refactor 默认产品工作流：scan→plan→task-map→candidate→test→verify→parity→closure→terminal，真实 Git candidate、刷新和 stop/start。 | K534、`test_testkit_browser_live_issue.py` 的 `refactor` family；Docker Playwright、真实 CLI/Watcher/tmux、父子 Task/dossier/receipt。 | **产品工作流通过**：host 1 passed/200.45s，Docker 1 passed/126.55s，restart_verified=true、cleanup_ok=true。 | 默认 report-only 路线不等同 catalog 强策略或真实 Provider。 |
| R-15 | Refactor catalog strong 单 lane：signed blocking、Product Acceptance、candidate、local ship、restart/cleanup；强策略不能靠降级取得绿灯。 | K535；`refactor-strong` browser/CLI 入口，签名 CLI、candidate/ship gate、Docker Playwright。 | **产品强策略链通过**；K535 记录 Product Acceptance、candidate、local ship、restart/cleanup；未执行真实 Provider。 | 不能据此关闭 K530/K557-F2 或长期稳定性缺口。 |
| R-16 | 双 lane 产品交付：两个独立 Task/worktree/probe，互斥 scope，两个 writer/verifier barrier，同一 candidate SHA、组合 probe、两 lane terminal。 | K536；确定性 Kernel + recording transport + script worker；两 lane 顺序/反序/中途重建测试。 | **确定性产品交付通过**：最终 2 passed/21.82s；缺任一 verify 不产生 ship/terminal，重建后无重复关键事件。 | 这是确定性闭环，不替代真实 CLI/Provider 或外部 owner 收件。 |
| R-17 | 双 lane live 产品交付：CLI/Watcher/tmux/browser 同 Run 交付、物理 worker 收据、candidate/verification、restart/cleanup。 | K537；Docker Playwright、tmux、真实本地 runtime、十二物理收据/十三次准入。 | **live 链通过**：host 1 passed/241.86s，Docker 1 passed/162.42s，browser-gate pass、restart_verified=true、cleanup_ok=true；K557-F2 长时延迟仍 open。 | 单次 live 通过不等于长期稳定性通过。 |
| R-18 | 同一 shared runtime 的 General/Issue/PRD/Refactor 多 kind：FIFO、队列、资源隔离、合法并发、Task collision、重建和终态归属。 | K539、`tests/conformance/test_zaofu_testkit_s8_shared_kind_*.py`、Generic mock E2E；正式 CLI/ControlledAction、scope/identity/contract snapshots。 | **测试真实检出并推动修复产品问题**：`ZF-TEST-092`、093、094、095、096、097、098、099 均在该主线被发现并登记为 fixed；修复后正式矩阵/调用方通过，但外部 owner 实收仍未闭合。 | 这些编号代表 ZaoFu 产品缺陷，不是测试框架失败；原始红证据保留。 |
| R-19 | 浏览器 truth chain：canonical Store/EventLog→API→SSE→DOM，拖拽/刷新/重连后前端状态收敛；禁止 synthetic fanout 和空状态假绿。 | `test_testkit_browser_truth_chain.py`、`test_full_stack_validation.py`；Docker Playwright、FastAPI、SSE、DOM、scorecard。 | **框架能判定产品 truth chain**；真实链路断裂会失败，synthetic fanout/empty state 会被拒绝。 | `ZF-TEST-060` 仍记录 Traces 首屏旧 `/events` bootstrap；深层旅程可通过不代表该问题已修复。 |
| R-20 | Web/Event read model：`/api/events` malformed/cursor 分页、freshness/fallback、归档/重建、SSE reconnect、Kanban projection。 | `tests/e2e/test_event_read_model.py`、`run_event_read_model_e2e.sh`、`test_api.py`、browser truth chain；真实 FastAPI + EventLog/read model。 | **产品问题被稳定检出**：`ZF-TEST-009`（Event projection 偶发空列表）、`ZF-TEST-010`（WAL initialization database locked）、`ZF-TEST-011`（created_at drift）、`ZF-TEST-012`（fallback 缺 freshness/source）、`ZF-TEST-062`（malformed/cursor 滞后空页）均有对应台账证据；其中 062 仍 open，不能以单次通过关闭。 | 这是产品 Web/read-model 问题，不是因为测试有红灯就判框架失败。 |
| R-21 | Traces 和 Web 输入边界：scoped list/detail/history/spans/raw/deep-link/responsive；非法 query/path 应结构化 4xx。 | Docker Playwright、`tests/e2e/scripts/run_traces_page_regression.sh`、`tests/test_web_zap_active_input_boundaries.py`。 | **部分通过并检出产品问题**：Traces 深层旅程在三浏览器中通过，但首屏旧 `/events` 请求对应 `ZF-TEST-060`；非法 query/path 返回 500 对应 `ZF-TEST-061`，仍须修复后回归。 | 真实 active ZAP 结果不可被 synthetic scan 替代。 |
| R-22 | OpenAPI 产品接口：服务生命周期内读取 OpenAPI schema，测试 operation、状态码、响应 schema、非 server error，产出 JSON/JUnit/版本 receipt。 | `test_openapi_manifest_flow.py`、`test_schemathesis.py`；本地 API service、Schemathesis deterministic profile、OpenAPI manifest。 | **通用 API 产品 fixture 通过**；不符合 schema 或 accepted invalid input 会判产品 finding；工具配置/版本漂移会单独判 harness/environment。 | 当前主要覆盖只读 GET/正向合同；不声称覆盖所有真实生产 API。 |
| R-23 | 产品性能：HTTP health 负载、pressure/spike/stress、错误率、request count、p95/p99、threshold、summary/digest。 | `test_k6_manifest_flow.py`、`test_performance.py`、`tests/conformance/test_zaofu_testkit_s10_k6_workloads_host.py`；digest-pinned `grafana/k6` Docker。 | **有界性能测试可执行并正确判定阈值**；但 overnight/生产级长稳、K552-F2/PERF-05 长时分配窗口未闭合，不将缺口写成产品 bug。 | 当前证据不等同生产容量或两小时以上 soak。 |
| R-24 | ZAP 产品安全目标：baseline、OpenAPI unauthenticated/authenticated passive、spider、risk threshold、报告/manifest/source binding、failure-bundle。 | `test_security_scan.py`、`test_runner.py`、`test_manifest.py`、`test_reference_product_closure.py`；digest-pinned OWASP ZAP、统一 runner。 | **测试框架安全判定通过**：K428 合成报告 4 passed；K429 安全/runner/manifest 156 passed、closure/report/bundle 19 passed；High risk=0 的 baseline/passive 观察范围内通过；风险告警为 product fail，缺报告为 harness_failure。 | active/认证 active 曾出现 71%/exit 124、bounded exit 1，按环境/执行边界保留，不能写成真实 active scan 通过；`ZF-TEST-061` 仍 open。 |
| R-25 | 容器化产品依赖：ephemeral dependency container、端口映射、health probe、identity、启动/停止/删除；Docker/Playwright host-network 边界。 | `test_container_manifest_flow.py`、`test_container_runtime_flow.py`、`test_containers.py`、`test_docker.py`、`test_browser_manifest_flow.py`。 | **产品依赖容器 fixture 通过**；容器 capability 或启动错误明确分类为 environment/harness，不归因业务产品。 | 真实 Docker host 能力需显式 marker；未把 mock 当真实 Provider。 |
| R-26 | Product TestKit 外部工作目录/独立产品执行：checkout launcher、prior result、cumulative CI matrix、acceptance 输入、malformed acceptance 仍写 terminal evidence。 | `test_cli_entrypoint.py`、`tests/product_testkit/__main__.py`；独立 cwd、CLI subprocess、JSON/Markdown report。 | **产品测试入口通过**；外部目录可执行，malformed acceptance 不会静默绿化；首次失败与 rerun 均保留。 | 这是产品测试框架的可用性合同，不是某个产品业务功能结果。 |
| R-27 | 产品测试失败证据：失败身份、domain、OpenAPI finding、失败阶段、browser finding、artifact/failure bundle、报告完整性。 | `test_failure_bundle.py`、`test_report.py`、`test_artifacts.py`、`test_observability.py`。 | **框架支撑合同通过**：能将“产品失败”与“harness/environment/not-run”分开并保留可重放引用；报告缺命令、target commit drift、tamper 会 fail-closed。 | 该行只说明证据系统可用，不把框架自身测试绿灯算产品通过。 |

## 已明确检出的 ZaoFu 产品问题

下表只列“产品项目/产品工作流测试实际观察到的产品问题”。状态以当前 `ZaoFu_issue.md` 为准；`fixed` 只表示已有修复与回归证据，不删除原始红灯。

| Issue | 被测行为 | 测试观察 | 当前归因 |
|---|---|---|---|
| `ZF-TEST-060` | Traces canonical route 首屏请求范围 | 首屏仍触发旧 `/api/projects/<project>/events` bootstrap；后续 scoped Traces 深层旅程可通过，但请求契约仍失败。 | ZaoFu Web 产品问题，open。 |
| `ZF-TEST-061` | 非可信 artifact/profile query 边界 | 非法 `view`、超长 profile path 进入真实 route/service 后返回 HTTP 500，而不是结构化 4xx。 | ZaoFu Web 输入边界问题，open。 |
| `ZF-TEST-062` | `/api/events` malformed/cursor 分页与 read-model freshness | 20 次独立进程出现 `items=[]` 代替 seq `[1,2]`；间歇性裸红不能被单次通过关闭。 | ZaoFu Web/read-model 问题，open。 |
| `ZF-TEST-009/010/011/012` | Event projection 空列表、SQLite WAL 竞争、`created_at` 漂移、fallback freshness/source 元数据 | API/Web/read-model 调用方测试分别保留复现和 strict-xfail 映射。 | ZaoFu read-model/投影产品问题；不归因 TestKit。 |
| `ZF-TEST-092` | 排队请求覆盖 active contract | 在途 Run 的完成门禁串用后来请求合同，真实 shared-runtime 测试裸红。 | ZaoFu 产品问题，已登记 fixed；修复后调用方与四请求 FIFO 回归通过。 |
| `ZF-TEST-093` | 相同配置的不同 Run 被 snapshot SHA 误拒绝 | 配置 body 相同但 request metadata 不同导致合法并发被永久拒绝。 | ZaoFu 并发准入问题，fixed。 |
| `ZF-TEST-094` | 已确认 scope 未从 submit 透传到 invoke | 合法互斥请求被当成缺 scope 排队。 | ZaoFu workflow delivery 问题，fixed。 |
| `ZF-TEST-095` | 重建重放同一无效 invoke | 重建重复追加 `workflow.invoke.rejected`。 | ZaoFu replay/idempotency 问题，fixed。 |
| `ZF-TEST-096` | submit 覆写其他活动 Run bootstrap Task | 第二请求在准入前可改写已有活动 Task owner。 | ZaoFu Task ownership 问题，fixed。 |
| `ZF-TEST-097` | `target_root` 被误当作相对可写 scope | 合法 PRD/Refactor scope 被当成 invalid work scope。 | ZaoFu scope/准入问题，fixed。 |
| `ZF-TEST-098` | light task 未消费请求 scope | Task contract 获得整个工作树写范围而非已准入范围。 | ZaoFu 写权限隔离问题，fixed。 |
| `ZF-TEST-099` | planner 扩大已准入请求的写范围 | planner task_map 扩大 allowed_paths 并仍派出 writer。 | ZaoFu planner/write-scope 问题，fixed。 |

## 测试框架问题、环境阻断与降级项（不归因产品）

| 类别 | 具体问题/边界 | 处理结论 |
|---|---|---|
| 测试框架 | `ZF-TEST-086` 浏览器选择器把后台交付 Task 当作当前用户 Task；`ZF-TEST-081/082/083/084` 分别涉及报告分类、精确 nodeid、source-binding、premerge wrapper。 | 已按测试框架问题修正或保留审计，不写入产品缺陷。 |
| 测试框架 | K533 F1–F4：漏传 `target_root`、browser poll 预算过短、错误假设 synth origin、把 aggregate 当 worker ledger；K539-F1/F2/F3：fixture scope/验证命令/Task 结算检查错误。 | 已保留首轮红证据并修正测试入口；不归因 ZaoFu 产品。 |
| 测试框架 | K5 k6 parser、K40/K46 cleanup、K571-F6 receipt persistence、K585-D diagnostics、K590 QMP/UUID fixture、K596-F1 hook provenance 等历史测试支撑问题。 | 作为 TestKit backlog/审计项，不算产品失败。 |
| 环境 | K586 历史复查曾记录 CLI/认证阻断；K600/K610 后两项原生执行由操作者明确跳过，本次未复查重启后的登录或安装状态。 | 标记 Provider/environment blocked，不伪造为产品通过或产品 bug。 |
| 环境 | Docker、真实浏览器、真实 active ZAP、宿主 OS/VM、外部 owner 收件等均需显式能力/marker；active ZAP bounded timeout/exit 1 按 harness/environment 分类。 | 保留结果和边界；不把工具不可用写成产品失败。 |
| 降级/未执行 | `/issue-merge` 测试设计与实现、Feishu 联调、远端 PR/merge/push、overnight/生产级扩展负载、超过当前预算的长期稳定性。 | 明确列为未实现/未执行，不从已完成产品测试中扣成“测试失败”，也不宣称覆盖。 |

## 结论

本文件分两类保存已有检测能力：通用产品 TestKit 的 foundation/API/service/persistence/release、浏览器、性能、安全和报告合同；ZaoFu 宿主的 Product Acceptance、General/Issue/PRD/Refactor、双 lane 与交付边界。后者检出的 ZF-TEST 问题归宿主 ZaoFu，不外推为任意生成产品的业务缺陷。红灯必须按证据区分产品、ZaoFu 宿主、环境和测试框架问题；strict xfail 不能一概视为环境错误，harness failure 也不能算作产品检出成功。

本文件不对任何单一真实产品的全部功能、生产容量或发布 readiness 作整体保证；这里只对表中已经执行并留证的产品测试范围负责。

## 6. 按产品测试目标细化的执行矩阵

本节把前面的 27 个汇总项继续拆成“产品对象 → 具体步骤 → 判定 → 证据”的粒度。不同 K 阶段复用了相同的测试合同，数字不相加；同一个产品场景的首次红灯、修复回归和后续审计也不重复计算为多个产品功能。

### 6.1 Disposable/reference product fixture

| 产品目标 | 具体步骤和断言 | 使用技术/入口 | 结果归类 |
|---|---|---|---|
| Foundation smoke | 创建临时 Git 产品，提交 `product.py`，读取 immutable `target_commit`，执行 `python -c 'from product import main; print(main())'`，检查 stdout artifact、P-01～P-05/P-17/P-18 domain 覆盖和 digest。 | `test_foundation_flow.py::test_generic_foundation_fixture_runs_p01_to_p05`、`ProductManifest`、`run_product_plan`、`capture_artifact`。 | **产品 fixture 通过**；证明产品源版本和基本命令能被独立执行。 |
| API fixture | 对通用 API fixture 执行 auth/status、输入校验、状态变化、JSON 结构断言；故意提供 incomplete response，确认是 product failure；不可达 endpoint 单独判 harness/environment。 | `test_api_fixture_flow.py`、`test_api.py`、本地 HTTP server、API probe。 | **正向通过；负向归因正确**。不能把 endpoint 不可达写成产品 bug。 |
| Service lifecycle | 启动 declared entrypoint，轮询 health，发送请求，验证服务未在 health 后死亡，停止并确认 cleanup；分别注入 health timeout、missing executable、启动后死亡。 | `test_api_service_fixture_flow.py`、`test_service.py`、`test_health.py`、子进程和 health receipt。 | **生命周期通过**；服务合同错误是产品 finding，宿主缺工具是 environment failure。 |
| Persistence fixture | SQLite 表约束、重复写拒绝、backup/restore、migration/persistence commands 顺序、快照和 receipt。 | `test_persistence_fixture_flow.py`、`test_persistence.py`、SQLite 临时库。 | **产品持久化 fixture 通过**；这不是 ZaoFu EventLog 自身结果。 |
| Release artifact | 构建 wheel/sdist，检查 kind、非空、size、digest；执行 install/upgrade/rollback phases；缺 output、空文件、phase failure 必须保留失败证据。 | `test_release.py`、`test_runner.py`、artifact index/digest recheck。 | **发布合同通过**；失败不会被 runner 绿化。未执行远端发布。 |
| Reference product full closure | 同一临时 Git 产品同时执行 smoke、build、release artifact、ZAP report、restart、并发 workers、persistence、cleanup 和 Product Acceptance。 | `test_reference_product_closure.py`、统一 runner、`IssueMergeLoop`、acceptance report、failure-bundle。 | **reference product 全闭环通过**：acceptance passed、domain coverage 无缺口、delivery readiness allowed；`merge_authorized=false` 仍保持。 |
| Reference product failure | 将 `check.py` 改为退出码 17，确认 runner 仍写 terminal report/failure bundle，最终 verdict=product fail，并阻断 issue merge。 | `test_reference_product_closure.py::test_reference_product_failure_is_product_verdict_and_blocks_issue_merge`。 | **产品失败路径通过**：红灯说明产品被测失败，不说明测试框架失败。 |

### 6.2 Product Acceptance、candidate 和本地交付

| 产品目标 | 具体步骤和断言 | 使用技术/入口 | 结果归类 |
|---|---|---|---|
| Acceptance report | 校验 spec、journey、assertion、evidence ref、SHA、workflow_run_id、plan revision、candidate ref、target commit。缺 evidence、空 results、错误 digest 必须 reject。 | `test_acceptance.py`、`test_cli_entrypoint.py`、`build_acceptance_report`。 | **框架支撑合同通过**；它能阻止“命令绿但验收证据未绑定”。 |
| Candidate gate | 缺 Product Acceptance 时拒绝 candidate；完整 report 绑定 spec/report/digest；旧 candidate、错 commit、错 workflow identity 被拒绝；重启重放保持同一 binding。 | `test_zaofu_testkit_s8_e2e.py`：missing acceptance、complete binding、replay、actual candidate、preflight/creation identity。 | **交付门禁通过**；只允许证据完整的产品候选进入后续阶段。 |
| Candidate/delivery evidence | 核对 candidate branch、commit lineage、manifest、artifact index、target commit、tamper、mechanical gates；绿测试不能直接变成 merge authorization。 | `test_delivery.py`、`test_manifest.py`、`test_artifacts.py`、`test_report.py`。 | **证据合同通过**；目标是防止假绿，不是提高产品 pass 数。 |
| Issue loop | Issue ingress→regression artifact→Fix Run→candidate preflight→local controlled merge→post-merge regression；验证 approval、duplicate revision idempotency、corrupt state、persistence failure。 | `test_issue_loop.py`、K430/K433 S9、本地隔离 Git。 | K430/K433 **79 reported/78 passed/1 historical strict xfail**；`ZF-TEST-020` 已在台账标 fixed，历史红灯不被覆盖。 |
| CI/stability classification | 每个 tier 独立报告；deferred/not-run 保留；同命令 fail→pass 和 OpenAPI fail→pass 分类 flaky；missing receipt、target drift、tamper、cleanup failure fail-closed。 | `test_ci.py`、`test_stability.py`、`test_runner.py`、JSON/JUnit。 | **结果分类通过**；flaky 不是普通 pass，environment/harness 不伪装 product failure。 |

### 6.3 General、Issue、PRD、Refactor 产品工作流

| 产品工作流 | 具体产品步骤/物理证据 | 技术与实际结果 | 归因边界 |
|---|---|---|---|
| General live | 浏览器发起/批准 General；独立 `zf start`、Watcher、tmux；worker 消费正式 stdin；验证 proposal、plan、Task、terminal、delivery、DOM、restart 和 cleanup。 | `test_testkit_browser_live_runtime.py`、Docker Playwright、EventLog/DOM；同 Run 与 Task identity 通过。 | 脚本 worker 证明本地 transport/lifecycle，不证明真实 Provider 语义。 |
| Issue live | Issue 请求、plan/approval、执行、candidate、交付；交错后台 Task 时按 `source=kanban-agent`、task_id、flow marker 选择当前任务。 | `test_testkit_browser_live_issue.py` 的 `issue` family；`ZF-TEST-086` 曾发现测试选择器误选后台 Task，修复后精确关联通过。 | 086 是测试框架问题，不能归因产品。 |
| PRD live | 两次 scan、plan、synth、impl、verify、discovery、Judge；7 child + 1 synth、8 物理 stdin、9 admissions；candidate、父子 Task、terminal、dossier、receipt 同 workflow_run_id；stop/start 后 watcher heartbeat 和交付字节稳定。 | K533：direct **17 passed**；dev-verify **28 passed**；最终 PRD host **1 passed/160.79s**，Docker **1 passed/94.49s**。 | F1–F4 是测试接线/预算/断言修复；不归因 ZaoFu runtime。真实 Provider 未参与。 |
| Refactor default | 三 scan reader、planner、impl、verify、bridge、parity、Judge；真实 Git candidate；独立 verify ref/SHA；scan→plan→task-map→candidate→test→verify→parity→closure→terminal；刷新与正式 stop/start。 | K534：direct **29 passed**；host **1 passed/200.45s**；Docker **1 passed/126.55s**；restart_verified/cleanup_ok=true。 | 这是默认 report-only 路线，不外推 catalog strong、真实 Provider 或共享并发。 |
| Refactor catalog strong | signed blocking、Product Acceptance、candidate integration、ship/local delivery、main/tag ancestry、owner dossier；禁止用 report-only 降级取得绿灯。 | K535：direct **32 passed**；strong host **1 passed/210.31s**；Docker **1 passed/135.09s**；自动闭包 **48 passed**。 | scripted semantic live 通过，不等于原生 Provider/长期稳定性通过。 |
| Two-lane deterministic | 两个独立 Task/worktree、互斥 allowed_paths、lane affinity、writer/verifier 两道 barrier、同 candidate SHA；反序、缺 verify、重建、组合 probe。 | K536 最终 **2 passed/21.82s**；缺任一 verify 不产生 test.passed/ship/terminal，重建后无重复关键事件。 | 确定性 route 通过；无真实网络/Provider/外部 owner。 |
| Two-lane live | CLI/Watcher/tmux/Docker browser；十二物理 receipts、十三次 admission；两个 writer/verifier、candidate、ship、本地交付、restart/cleanup。 | K537 host **1 passed/241.86s**，Docker **1 passed/162.42s**；前序 live **2 passed**，dev-verify **52 passed**。 | 单次 live 通过不关闭 K557-F2 延迟稳定性。 |
| Shared runtime | General/Issue/PRD/Refactor 共享 runtime 的 FIFO、active contract、配置 identity、scope、容量、Task owner、writer/planner allowed_paths、终态释放和重建。 | K539-C1～C5 使用正式 CLI/ControlledAction、scope/contract snapshots、Generic mock E2E；092–099 均真实检出并随后 fixed。 | 这是 ZaoFu 产品并发/隔离问题，不是测试框架失败；外部 owner 实收仍未覆盖。 |

### 6.4 当前浏览器主链和审计结果

K573 用当前浏览器入口串行执行六条产品工作流，结果必须按节点拆开读取，而不能写成“3/6 通过”或“整体 50%”：

| 浏览器节点 | 实际结果 | 产品含义 |
|---|---:|---|
| General | pass / 139.703046s | 产品流程进入终态并完成 restart/cleanup 证据。 |
| Issue | pass / 100.861525s | Issue 流程进入终态并完成同 Run 交付证据。 |
| PRD | fail / 212.237775s | 150 秒时仍 `in_progress`；后台完成不被测试改写为通过，根因仍需单独归因。 |
| Refactor | pass / 299.701153s | 默认 Refactor 链完成，source stable、cleanup 通过。 |
| Refactor strong | fail / 246.414240s | 在终态等待窗口未满足，不以相似症状替代 K557-F2 根因。 |
| Refactor two-lane | fail / 252.297001s | `simulation.done` 前没有 `run.goal.completed`，保留产品闭环未达终态事实。 |

六条均有 source-before/source-after、phase、JUnit、log、failure bundle 和 cleanup 证据；统一审计为 **4 pass / 4 fail / 6 missing / 17 harness_failure**。这里的 missing/harness_failure 是证据或执行分类，不等于 17 个新产品缺陷；`same_revision_verified=false`、`release_authorized=false` 也不能被忽略。

K584/K585 进一步说明：双 lane 单次复验 **1 passed/367.295173s**；PRD 复验曾在 restart shell handshake 失败（K584），K585 补充握手诊断后同入口 PRD **1 passed/219.794489s**，但“未复现”不等于生产根因 fixed。它们是产品工作流证据和稳定性边界，不能用一次绿色复验覆盖历史红灯。

### 6.5 API、Web、OpenAPI 和浏览器真值链

| 产品面 | 具体断言 | 结果/归因 |
|---|---|---|
| API fixture | auth/status、JSON schema、state change、malformed/incomplete response、unreachable endpoint。 | 完整正向 fixture 通过；schema 缺字段判 product failure；网络不可达判 harness/environment。 |
| API service | health 200、服务生命周期、cleanup、API contract 与服务进程同一 run。 | `test_api_service_fixture_flow.py` 与 `test_openapi_manifest_flow.py` 通过；产出 Schemathesis JSON/JUnit/version receipts。 |
| OpenAPI | 从 manifest 读取 schema/base_url/entrypoint；operation discovery、status code、response schema、not-server-error、accepted invalid input。 | generic API fixture operation 通过；配置/版本漂移不伪装成产品 finding。 |
| `/api/events` | malformed EventLog、cursor 第一页 seq `[1,2]`、下一页 `[3]`、read-model freshness/fallback。 | K425 20 次独立进程 **16 passed/4 failed**；对应 `ZF-TEST-062`，仍 open。 |
| Traces | scoped list/detail/history/spans/waterfall/raw/empty/deep-link/responsive，首屏禁止旧 `/events` bootstrap。 | 深层旅程三浏览器 **6 passed**，但首屏旧 `/events` 仍为 `ZF-TEST-060`。 |
| Web 输入边界 | 非法 `view`、超长 path、结构化 4xx、不能泄露 traceback/500。 | `ZF-TEST-061` 裸红：3 xfailed / `--runxfail` 3 failed，真实 route 返回 500。 |
| Browser truth chain | canonical Store/EventLog→API→SSE→DOM；刷新、重连、DOM 收敛；empty/synthetic fanout 必须拒绝。 | `test_testkit_browser_truth_chain.py`、full-stack scorecard 可执行；红灯能保留产品链断裂位置。 |

### 6.6 安全、性能、容器和证据分类

| 产品面 | 具体执行 | 结果/边界 |
|---|---|---|
| ZAP launcher | digest/no-pull、固定 requestor/passive wait/report、origin、risk threshold、报告完整性、cleanup 注入。 | K426 **65 passed/3 strict xfailed**；061 作为产品 Web 输入边界保留。 |
| ZAP owned target | 隔离 app/ZAP 容器和 network；固定 `/health`、`/api/state` GET；scanner exit、报告 digest、无残留容器/网络。 | K427 host **1 passed/58.77s**，scanner exit=0、报告 SHA `2f112955…a670c`；仅 passive。 |
| ZAP runner classification | 零告警→pass；超过 max_risk→product fail；exit=0 无报告→harness_failure；报告/manifest/failure-bundle 绑定。 | K428 **4 passed/25 deselected**；K429 **156 + 19 passed**。不把 synthetic scanner 当真实 active scan。 |
| k6 workload | digest-pinned `grafana/k6`、HTTP checks、error rate、request count、p95/p99、threshold、summary/receipt、容器清理。 | 有界 workload 可执行；K552-F2/PERF-05 长时分配/overnight 未闭合，不写成产品缺陷。 |
| Container dependency | ephemeral container identity、port mapping、health probe、start/stop/remove、Docker capability。 | Product TestKit container fixture 通过；Docker/host 缺失分类为 environment。 |
| Failure bundle | failure identity、domain、phase、OpenAPI/browser findings、artifact index、target commit、tamper、report/JUnit。 | 产品失败、harness_failure、environment_failure、not_run 分层可回放。 |

## 7. 产品项目测试中实际检出的 Issue 明细

| Issue | 具体产品行为 | 测试证据/观察 | 当前状态 |
|---|---|---|---|
| `ZF-TEST-060` | Traces canonical route 首屏仍加载旧 `/events` bootstrap。 | Docker Playwright 观察到 forbidden request；深层 scoped Traces 可通过，但首屏请求合同失败。 | open。 |
| `ZF-TEST-061` | 非可信 artifact/profile query 被转成 HTTP 500。 | `--runxfail` 3 failed；合法输入对照通过，说明不是整个 Web 服务不可用。 | open。 |
| `ZF-TEST-062` | read-model 滞后时 malformed/cursor API 返回空页。 | 20 次独立进程 16/4；失败为 `items=[]` 而不是预期 seq `[1,2]`。 | open。 |
| `ZF-TEST-009/010/011/012` | Event projection 空列表、WAL lock、created_at drift、fallback freshness/source 缺失。 | Web/API/S6 projection 调用方保留各自断言，不合并为一个泛化问题。 | 当前台账均需按各自状态回归。 |
| `ZF-TEST-092` | 排队请求覆盖在途 Run active contract。 | Shared runtime 真入口中 completion gate 取到后一个请求的合同；修复后逐 claim immutable ref 回归通过。 | fixed。 |
| `ZF-TEST-093` | 相同配置因 request snapshot SHA 不同被误拒绝。 | 配置 body 相同、request metadata 不同的正式并发场景曾被拒绝；改为比较内容身份后通过。 | fixed。 |
| `ZF-TEST-094` | 已确认 scope 未透传 submit→invoke。 | 正式 scope 断言为 None，合法互斥请求永久排队；修复后 6 项正式矩阵和 95 项 caller closure 通过。 | fixed。 |
| `ZF-TEST-095` | 重建重复写 `workflow.invoke.rejected`。 | 普通重放和拒绝写入中断两条路径均出现重复事件；source_event_id 持久去重后通过。 | fixed。 |
| `ZF-TEST-096` | submit 可覆盖另一活动 Run 的 bootstrap Task。 | CLI/ControlledAction 均曾在准入前 accepted；修复后返回 workflow_task_stale/409 并保持原 Task 不变。 | fixed。 |
| `ZF-TEST-097` | 绝对 `target_root` 被当成相对 scope。 | 合法 PRD/Refactor 请求被 invalid work scope 阻断；项目根归属和相对 scope 分开校验后通过。 | fixed。 |
| `ZF-TEST-098` | light Task 忽略请求 scope，获得整个工作树写范围。 | 正式 General+Issue scope 测试发现 allowed_paths 过宽；Task/worker snapshot 接入声明 scope 后通过。 | fixed。 |
| `ZF-TEST-099` | planner 扩大已准入请求的 allowed_paths 后仍派 writer。 | 正式 planner scope-red 复现；writer 创建前做 admitted source_event_id→invoke.scope 校验后通过。 | fixed。 |

## 8. 产品测试框架问题与环境阻断的明确排除

| 排除项 | 为什么不能归因产品 | 处理方式 |
|---|---|---|
| `ZF-TEST-086` | 浏览器选择器取了后台交付 Task，不是产品没有创建当前 Task。 | 改为按 task_id/flow marker/计划 event 精确关联，保留首轮红灯。 |
| K533 F1–F4 | `target_root` 漏传、poll budget、origin 假设、aggregate ledger 断言都是测试接线错误。 | 修复测试入口和断言，不改生产 runtime，不把首轮红灯记成产品 Issue。 |
| K539-F1/F2/F3 | fixture 缺验证命令、scope 字段位置错误、过早检查 Task 结算。 | 修复夹具后重新跑正式入口；首轮报告仍归档。 |
| K590/K596/K585-D | QMP/UUID 参数、hook fixture 来源、握手诊断缺口属于测试观测/证据问题。 | 分别标 fixed/diagnostic fixed，不作为产品业务 defect。 |
| Provider/host | CLI/认证和 Docker/ZAP/QEMU 能力不足可能阻断真实层；历史观察不代表当前宿主状态。 | 分类 environment blocked/not_run；不以 script worker 或 synthetic scanner 冒充真实 Provider。 |
| 统一审计 missing/harness_failure | 缺失历史报告或来源 SHA 不等于产品执行失败。 | 保留 missing/harness_failure，要求同源报告后再裁决。 |

## 9. 产品项目测试的阅读边界

1. 本文件是“产品项目测试已完成部分”，不是 ZaoFu 自身主干完成百分比，也不是某个真实业务产品的 release authorization。
2. Product TestKit 的 reference product 是验证测试框架能否测出产品问题的 disposable fixture；不能把 fixture 通过外推到真实产品。
3. 产品测试红灯是有效结果：例如 `ZF-TEST-061/062` 的裸红、PRD/strong/two-lane 终态等待失败，均应保留并进入产品问题归因，而不是为了提高通过率删除节点。
4. `merge_authorized=false`、`release_authorized=false`、`same_revision_verified=false` 是不同门禁，不能由 Product Acceptance 或单次浏览器 pass 自动替代。
5. `/issue-merge`、Feishu、远端 PR/merge/push、真实 Provider 长任务、overnight 生产负载不是本文件已完成产品测试；这些边界继续单独标记，不从已执行的产品测试中倒扣成测试框架失败。

# ZaoFu 通用产品测试框架待完成部分梳理

补充日期：2026-09-30。本章面向所有由 ZaoFu 生成、修改或交付的产品，不绑定具体项目、语言或业务。仅补充待办分类，不新增测试实现、不执行测试、不解除构建暂停。

## 1. 完成口径与待办范围

依据 [Product_steps.md](Product_steps.md) 的“通用闭环完成审计”、其后增量和最新 need to do，以及 [ZaoFu_Product_TestKit.md](ZaoFu_Product_TestKit.md)：**通用 manifest、执行器、结果分类、报告、参考产品/故障夹具和 delivery readiness 已有完成记录**。不能因为某个产品尚未提供业务合同、环境或验收结果，就把整个通用框架重新标为未完成。

本章把剩余工作分为三类：通用宿主适配/证据运营接线、已有适配器的按能力验证、条件性扩展。后两类不是每个产品都必须实现一遍，更不能把所有可选工具设为框架完成前置。

- **框架检测能力完成**：测试可执行、断言独立、结果可分类、证据可回读、资源可清理；稳定检出产品 `fail` 也是有效成果。
- **产品质量验收通过**：适用的 mandatory 测试与 acceptance 满足该产品合同。此结论不能由参考夹具通过推导，也不能由框架自报。
- **待实现与待执行分开**：已有模块无需重建；只在缺少实际调用入口、适配器或必要断言时才新增代码。缺输入、缺环境、未运行、失败和不适用不得混写。
- 前文涉及的 ZaoFu 工作流、Traces、Provider、VM 故障属于宿主系统边界；它们的完整待办见 [zaofu_done_part.md](zaofu_done_part.md)，不作为通用产品业务测试缺口重复计数。

以下 P-01～P-18 指方案中的正式测试域；前文 R-01～R-27 只是能力汇总行号。本次为资料、入口和状态的对齐，不声称已完成产品业务或源码全量覆盖验证。

## 2. 通用接线与条件性扩展待办

| 项目/类别 | 已完成基础 | 仍需补齐或验证的内容 | 完成判据及边界 |
|---|---|---|---|
| 产品验收→ZaoFu 正式入口接线（宿主适配，P-18） | `tests/product_testkit/acceptance.py`、`delivery.py`、统一 runner 和已有 candidate gate 测试 | 核实正式入口能消费同一 run/candidate/target commit 的 verification-result、Product Acceptance、artifact index；验证错误身份、陈旧报告、摘要篡改、缺证和 cleanup 失败均阻断。不把已有模拟 gate 当完整宿主链证明 | 在隔离环境留下调用入口、实际读取的报告/摘要与拒绝或接受回执；优先复用已有入口。只产出 readiness，不执行远端写入 |
| T0～T3 CI 与证据运营（宿主接线） | `ci.py`、`tiers.py`、本地累计层级和跨层失败保留合同 | 在选定 CI 环境确认 job 选择、资源预算、退出码、跨 job 汇总、失败报告留存、artifact 上传/下载和保留策略；资料尚未证明远端 runner 验证完成 | 每个 tier 和 attempt 可独立回读；失败 job 仍保留报告，未运行层不冒充通过。不是重写本地 CI 调度器，本轮不部署 CI |
| Pact 协议报告（条件扩展，P-10） | 普通 `commands[]` 可调用协议测试，已有 fake service/有限重试合同 | 若需要专用适配，再补 consumer/provider 版本、状态准备、报告解析、协议差异与工具故障分类；无稳定服务边界时不引入 | Pact 专用解析器在实施记录中仍未实现；不是通用最小闭环必需项，也不强制每个产品采用 Pact |
| Toxiproxy 网络故障（条件扩展，P-10/P-12） | 已有声明式服务、依赖容器和恢复场景执行支撑 | 仅在声明代理拓扑、故障预算和 cleanup 后补自动注入/撤销、实际影响观测、超时/重试和副作用断言 | 自动注入器尚未实现；必须证明故障确实生效且代理已清理，不能用布尔标记代替实际证据；无此需求不强行建设 |

## 3. 已有方法，按产品能力仍需接入的测试

本表是通用的适配与验收清单，不是某个项目的执行计划。只选择 manifest 声明适用的能力；未发现能力缺口时不重复写适配器，不把产品自有业务断言硬编码进通用框架。

| 测试域 | 可复用的技术/入口（位于 `tests/product_testkit/`） | 按能力待提供或待验证的测试内容 | 应形成的证据与结果 |
|---|---|---|---|
| 身份/环境/需求闭包 P-01/P-02/P-05 | `manifest.py`、`matrix_adapter.py`、`planner.py`、`executor.py` | 固定 source_root/不可变 commit、capability/acceptance/domain 映射、runtime_paths、setup/cleanup、预算；验证悬空引用、路径越界、运行前后源码漂移和不适用理由 | manifest 与计划、Git 身份、准入拒绝或执行 receipt；已有门禁复用，不为每种产品复制 schema |
| 构建/静态/单元 P-03/P-04 | 普通命令执行器、`test_foundation_flow.py` | 接入声明的 build/lint/typecheck/unit/component 命令；产品负责预期输出和业务 oracle；覆盖必要命令失败与停止后续执行 | 精确命令/cwd、退出码、stdout/stderr、acceptance 归属；缺命令或缺 receipt 不能判通过 |
| CLI P-06 | `cli.py`、命令 runner | 按产品合同验证参数、输入输出、错误码、非法输入、非交互行为和资源清理 | CLI 输出/状态/退出码及断言；缺 executable 与业务断言失败分别分类 |
| API P-07 | `api.py`、`schemathesis.py`、`service.py` | 提供 OpenAPI/schema、entrypoint/health、权限与业务状态 oracle；验证请求/响应、输入拒绝、错误处理及必要状态变更，不仅测 health | operation、请求/响应、schema finding、JSON/JUnit、服务/工具版本；无 API 能力可明确不适用 |
| Web P-08 | `browser.py`、Docker Playwright | 提供关键用户旅程和独立 DOM/状态/视觉断言；隔离 API/UI 按浏览器网络要求启动；检查交互、刷新/重连、console/network、失败 trace | 截图/trace/日志、旅程和 assertion evidence 进入 acceptance；宿主浏览器/Docker 不可用不能记为产品通过 |
| 数据持久化 P-09 | `persistence.py`、`containers.py` | 依据声明的数据库/文件/队列验证 migration、约束、事务、CRUD、重复写、备份恢复和清理；补产品特有状态 reader，不重复实现已有命令调度 | 持久化步骤 receipt、依赖 health、前后数据快照；SQLite fixture 不替代其它数据库合同 |
| 外部集成 P-10 | `integrations.py`、`mocks.py`、依赖容器/普通命令 | 提供稳定协议及凭据来源；验证超时、有限重试、错误响应、重复调用、副作用次数与脱敏；按需求选择 fake、协议兼容依赖或授权外部环境 | 请求/响应和重试计数、协议结果、cleanup；不把 fake 成功写成真实第三方验收 |
| 安全 P-11 | `security.py`、`security_scan.py`、ZAP | 冻结隔离目标、扫描范围、镜像 digest、认证方式和风险阈值；按适用性执行 passive/active，并补业务授权/越权/路径与秘密边界断言 | 扫描器退出码、发现列表、目标/报告 SHA、证据索引；合成报告只证明解析器，不能证明安全扫描通过；主动扫描需显式范围 |
| 恢复/并发 P-12/P-13 | `recovery.py`、`concurrency.py` | 声明 ready/state reader、baseline、故障点和副作用上限；按能力验证进程重启、事务中断、消息乱序、重复请求、stale worker、多实例状态等价 | 注入 receipt、恢复前后状态、并发结果/副作用计数和进程清理；通用 SIGKILL fixture 不推断全部故障模型 |
| 性能/稳定性 P-14 | `k6.py`、`performance.py` | 产品提供负载脚本、吞吐/延迟/错误阈值、资源预算与窗口；按需要验证稳定负载、压力、突发、连接恢复和长时间资源释放 | summary、p95/p99、错误率、资源曲线、阈值及 cleanup；不内置统一业务 SLA，短 smoke 不替代容量基线；降级长稳暂不实施 |
| 发布/兼容 P-15 | `release.py`、artifact index、声明式发布场景 | 按声明产物与运行时在干净环境验证安装、启动、升级、回滚及数据兼容；产品提供版本矩阵与回滚 oracle | 真实构建产物 digest、安装/升级/回滚日志与状态断言；模拟 wheel 文件不证明可安装，不执行上传/发布 |
| 观测/诊断 P-16 | `observability.py`、`failure_bundle.py` | 接入产品日志/health/trace/审计事件，检查字段、关联身份、失败可定位、实际状态一致和 secret 脱敏 | 与 run/command/phase 绑定的日志和诊断；不能只校验字段存在而宣称所有产品日志合规 |
| 回归/重跑 P-17 | `stability.py`、`ci.py`、`test_issue_loop.py` | 变更后选择受影响域及其直接调用方；在可比较 commit/计划下检验回归；保留 fail→pass 的 flaky 与首次失败 | 独立 attempt/层级报告、回归案例及稳定性分类；只运行相关影响闭包，不要求每个小改动重跑全部域 |
| 最终证据/交付 P-18 | `acceptance.py`、`delivery.py`、`runner.py` | 汇总适用 mandatory journeys、报告/索引/摘要、run/candidate/commit 与 cleanup；验证缺证、错绑、产品失败不能进入 delivery allowed | verification-result、JUnit、failure bundle、Product Acceptance、delivery readiness；allowed 与 merge/release 授权独立 |

## 4. 不重复构建与暂不实施的边界

- 通用 manifest、参考产品成功/失败闭环、报告分类、artifact 索引、本地 CI 累计层级、恢复/并发/持久化/发布命令调度已有实现；未取得具体产品结果不意味着这些模块缺失。
- `IssueMergeLoop` 已有回调顺序、批准、失败阻断和 revision 去重合同。它不等于新 `/issue-merge` 功能；后者及 Feishu 仍只保留未来位置，不设计、不实现。历史资料中的真实 Issue→merge 待办不覆盖这一约束。
- Codex/Claude 原生执行、K530/K584-R 同签名事故、ZaoFu 宿主掉电与自身资源长样本，属于 ZaoFu 自身待办。原生两项当前按操作者要求跳过，不因完善本文而重新检查或执行，也不沿用历史 CLI/登录观察作为当前结论。
- 明确降级内容继续暂缓；没有对应能力的产品用可审计的不适用说明，不强制加入数据库、Web、第三方服务等依赖。
- TestKit 不直接执行远端 PR/merge/push 或发布；远端推送受仓库禁令约束，不列为自动恢复后可执行的任务。

## 5. 结果归因与后续完成标准

| 观察结果 | 正确结论 | 后续处理 |
|---|---|---|
| 业务断言稳定失败、证据完整 | 产品存在问题，对应框架检测能力有效 | 保留失败和回归入口，不为提高通过率修改阈值；不自动归因 ZaoFu |
| 证实失败来自 ZaoFu 正式入口/状态/交付逻辑 | ZaoFu 自身问题 | 引用或新增到 [ZaoFu_issue.md](../04_audit_and_issue_ledger/ZaoFu_issue.md) 的可复现条目；没有证据时不借用其它问题编号 |
| runner/夹具/选择器/报告绑定出错 | 测试框架缺口 | 修复最小支撑逻辑并补直接/调用方回归，保留原始误判 |
| 工具、凭据、隔离资源或输入缺失 | 环境、准入失败或未执行 | 记录具体缺项，不计通过、不推断业务缺陷；不据此撤销已有通用框架自证 |
| 产品 mandatory 项均通过且证据身份一致 | 对应范围的产品验收成立 | 独立评估交付就绪；不能推断其它产品、未执行能力或外部发布已获授权 |

后续只在明确选择的能力上补缺失接线与验证，使用通用最小参考产品、故障夹具和独立 oracle 证明可复用性，不绑定具体业务项目。完成判据是“可执行、可判定、可留证、可复现且不越权”，不是所有被测产品全绿。本次只追加待办章节并去除具体项目名称；测试构建和执行仍暂停。
