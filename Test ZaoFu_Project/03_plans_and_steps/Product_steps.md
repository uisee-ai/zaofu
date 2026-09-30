# 产品项目 TestKit 实施步骤

> 状态: done（通用 Product TestKit 闭环；真实产品质量认证按各项目 manifest 独立执行）
>
> 目标：按照 `ZaoFu_Product_TestKit.md`，为所有由 ZaoFu 生成、修改或交付的产品项目建立通用、可复现、可判定、可追溯的测试闭环。不针对某一个具体产品项目，不评价 Agent/Provider 能力。
>
> 阅读说明（2026-09-30 对齐）：正文中的 `[~]`/`[ ]`、命令和数字保留各阶段历史快照；“通用闭环完成审计”之后还有未跟踪文件、cleanup 后身份、CLI 输入错误、CI 域选择、外部启动器和 dev-verify 的已完成增量，不能只读较早审计就重做这些能力。当前测试构建仍暂停，本轮不复跑。

## 当前口径与历史记录边界

- 本文件记录通用框架实现，不保证任意真实产品质量通过。最新已记录联调是 Product TestKit 与 dev-verify 的 `381 passed, 1 skipped`，不是本次运行，也不是 381 个独立业务功能。
- 正式测试域为 P-01～P-18；机器层级为 T0=unit、T1=integration、T2=e2e、T3=security/perf/release，按层累计选择。不同测试域可声明不同 tier，不要求每个 T0 执行所有工具。
- 参考 fixture 自证、真实产品适配、ZaoFu 宿主接线分开；宿主 CI 上传、P-18 正式交付接线和条件性 Pact/Toxiproxy 扩展不能混作全部通用框架从零未完成。
- 下文真实产品历史样本的名称、包名及路径已按用户要求泛化为 `generated-product-project`、`generated_product` 等占位名。这些不是可直接执行的真实路径，不用它们定位历史原件或据此声明通用业务覆盖；历史结果和失败类别保留。
- IssueMergeLoop 的本地/mock 回调顺序不等于 `/issue-merge` 新功能。真实远端推送及等价引用更新对代理禁止；人工交付不由本测试框架授权。
- 当前补齐项见 [通用产品框架待办](productOfzaofu_done_part.md#zaofu-通用产品测试框架待完成部分梳理)，历史“下一步”不自动恢复执行。

## 总体骨干路线

1. 建立产品测试 manifest、能力矩阵和统一结果/证据合同。
2. 建立隔离 worktree、依赖初始化、命令执行和资源清理支撑层。
3. 实现 P-01～P-05 的通用基础测试：身份、环境、构建、单元入口和需求—能力—验收—测试闭包。
4. 按产品能力接入 P-06～P-10：CLI、API、Web、数据持久化和外部集成。
5. 接入 P-11～P-13：安全、不可信输入、故障恢复、并发和幂等。
6. 接入 P-14～P-16：性能、部署发布、观测和诊断。
7. 接入 P-17～P-18：Issue 回归和 ZaoFu 交付边界。
8. 建立统一 runner、Product Acceptance Report、verification-result 和 CI 分层门禁。
9. 用通用最小 fixture 做跨域联调，再对真实生成产品执行适配，不把 fixture 结果冒充真实产品质量。
10. 完成失败归因、`ZaoFu_issue.md` 记录和 Issue→Fix Run→测试→verify→candidate→merge 后回归闭环。

## 阶段 0：事实基线和范围冻结

- [ ] 完成并持续复核 `ZaoFu_Product_TestKit.md`、`product_test.md`、`product_testing_research.md` 与当前源码/测试入口的一致性（基线审查已完成，随实现继续更新）。
- [x] 明确测试对象边界：产品项目行为由产品契约负责；ZaoFu 只负责执行、证据、身份、路径和交付门禁。
- [x] 确定所有结果分类：`PASS`、`FAIL`、`NOT_APPLICABLE`、`ENVIRONMENT_FAILURE`、`HARNESS_FAILURE`、`FLAKY`、`NOT_RUN`。
- [x] 建立产品项目测试能力清单，不以缺少命令默认为通过。
- [x] 验证本阶段不修改 ZaoFu 生产代码；发现 ZaoFu 缺陷时只在 `ZaoFu_issue.md` 的 `issue found from product test` 区域登记，并保留检出测试。

**完成判据**：形成一份可执行的 capability/acceptance/test/evidence 闭包规则，且每个后续阶段都能引用。

**阶段证据（2026-09-14）**：完成文档、源码和现有产品验收契约盘点；未修改 `src/`，结果分类和通用能力边界已写入本文件与 `ZaoFu_Product_TestKit.md`。

## 阶段 1：Product Test Manifest 与矩阵

- [~] 定义并校验 `product-test-manifest.v1`：已实现项目类型、source/target commit、setup、commands、acceptances、capabilities、budgets、not_applicable、entrypoint/health-check 和 cleanup 准入；数据依赖、凭据来源及预算子结构仍需继续收紧。
- [~] 校验命令 ID 唯一、命令文本准确、tier/timeout/deterministic/mandatory 字段完整；已补 setup ID、跨 setup/command 冲突和有限 timeout，命令语义/上限仍待定义。
- [~] 校验 capability→acceptance→test command→evidence 的双向闭包和悬空引用；矩阵 helper 已有基础闭包，尚未接入统一执行入口和完整 evidence 闭包。
- [x] 校验 `not_applicable` 必须同时提供非空理由和可审计来源。
- [ ] 校验 manifest 与 ZaoFu run contract、candidate、target commit 一致（需接入真实产品 run/candidate fixture）。
- [x] 为 manifest 非法、缺失、重复、越界、非有限 timeout、重复 JSON key 和旧版本输入建立负向测试。

**拟新增代码/测试**：`tests/product_testkit/manifest.py`、`matrix.py`、`tests/product_testkit/test_manifest.py`。

**完成判据**：非法 manifest fail-closed；合法 manifest 可生成待执行测试计划；计划中没有未解释的能力空洞。

**阶段证据（2026-09-14）**：新增 `tests/product_testkit/manifest.py` 与 `tests/product_testkit/test_manifest.py`；调研记录见 `product_manifest_admission_research.md`。补强后执行 `uv run pytest -q --no-cov tests/product_testkit`，结果 **55 passed**。当前仍缺真实产品 run/candidate fixture，因此身份绑定和完整矩阵 admission 尚未完成。

## 阶段 2：隔离环境、命令执行与证据收集

- [x] 建立一次性产品测试 root、state/data/temp 目录和环境快照。
- [x] 实现跨语言 setup/build/test 命令的逐条执行，不拼接命令，不改变声明命令。
- [~] 记录 command receipt：现有 runner 已记录命令 digest、工作目录、退出码、超时、stdout/stderr refs；commit、环境、开始/结束时间和 artifact refs 的统一 receipt 尚未接入。
- [x] 实现进程、锁、临时目录和产品测试 root 清理检查；增加只读 Docker daemon 探测和临时端口分配能力，实际容器生命周期测试仍按产品能力放入后续阶段。
- [ ] 对命令超时、依赖缺失、Docker 不可用、workdir 不可写和 HEAD 漂移进行环境/测试基础设施分类。
- [x] 对产品 artifact 执行 digest 和路径边界校验；日志、报告、截图、trace、HAR 统一收集仍待后续报告层接入。

**拟新增代码/测试**：`tests/product_testkit/command_runner.py`、`evidence.py`、`environment.py`、`process_runner.py`、`cleanup.py`。

**完成判据**：同一输入在隔离目录中可重放；所有失败均能区分产品失败与 harness/environment 失败；无资源泄漏。

**阶段进展（2026-09-14）**：新增 `tests/product_testkit/executor.py`，复用现有 shell-free command runner，支持 target commit fail-closed、setup/command 独立执行、首个失败停止和 receipt 生成；本轮补充执行前重新准入与深拷贝快照，非法 manifest 不会执行产品命令。隔离 worktree、环境分类、cleanup 和 artifact digest 尚待下一步补齐。

**阶段 2 补充证据（2026-09-14）**：新增 `tests/product_testkit/environment.py`、`artifacts.py`、`verdict.py`、`capabilities.py`、`health.py` 和 `service.py`，复用现有环境快照、资源泄漏和 digest 实现；`uv run pytest -q --no-cov tests/product_testkit` 结果 **27 passed**，ruff 检查通过。产品测试私有 root、state/data 目录、环境 evidence、artifact 路径/摘要、清理、fail-closed verdict、Docker daemon 探测、临时端口分配、HTTP health 判定及本地服务启动/停止已验证；实际 Docker 容器生命周期和统一报告层仍待补齐。

**阶段 2 报告层证据（2026-09-14）**：新增 `tests/product_testkit/report.py` 与 `test_report.py`，将命令 receipt 汇总为 `verification-result.v1`；空 receipt/缺失命令输出 `NOT_RUN`，命令失败输出 `FAIL`，target commit 漂移输出 `HARNESS_FAILURE`，并支持原子写入 JSON 报告。全套 `tests/product_testkit` 结果 **58 passed**，ruff 检查通过。Product Acceptance journey/assertion 汇总、JUnit 聚合和 candidate 门禁接线仍未完成。

**阶段 2 acceptance 报告证据（2026-09-14）**：新增 `tests/product_testkit/acceptance.py` 与 `test_acceptance.py`，基于仓库 `product_acceptance_report.v1` 校验 journey/assertion 完整性、状态一致性和 evidence refs；缺少 evidence 或空 journey 结果会 fail-closed。全套 `tests/product_testkit` 结果 **61 passed**，ruff 检查通过。该模块尚未接入真实 candidate/run，也未实现 JUnit 聚合和远端门禁。

**阶段 2 JUnit 聚合证据（2026-09-14）**：在 `tests/product_testkit/report.py` 增加 `write_junit_report`，将命令 receipt 转换为标准 `testsuite/testcase/failure/error` XML，并保留失败与超时分类；新增测试后全套 `tests/product_testkit` 结果 **62 passed**，ruff 检查通过。JUnit 尚未接入 CI 上传和 failure bundle。

**阶段 4 能力规划证据（2026-09-14）**：新增 `tests/product_testkit/planner.py` 与 `test_planner.py`，按 manifest capability 稳定选择条件测试域，并始终加入 P-01～P-05、P-17、P-18 核心域；非启用能力输出 `not_applicable`，非布尔能力拒绝。全套 `tests/product_testkit` 结果 **65 passed**，ruff 检查通过。该规划尚未驱动真实命令执行，也未接入完整 capability/acceptance admission。

**阶段 4 执行接线证据（2026-09-14）**：`executor.run_product_plan` 现在在启动产品命令前重新校验 manifest 并调用 capability planner，将实际测试域计划写入 `ProductRun.domains`；非法 capability 会在执行前拒绝。与既有测试联调后全套结果仍为 **65 passed**，ruff 检查通过。当前尚未按域过滤/调度具体命令，仍需接入真实产品 manifest 的域映射和 acceptance admission。

**阶段 4 域映射证据（2026-09-14）**：在 `tests/product_testkit/planner.py` 增加 `commands_for_domains`，支持命令通过可选 `domain_ids` 显式绑定 P-01～P-18 域；未知域拒绝，未声明映射的命令不会被推断为覆盖任何域。新增测试后全套 `tests/product_testkit` 结果 **67 passed**，ruff 检查通过。按域实际过滤执行、未覆盖域门禁和产品 manifest schema 接入仍未完成。

**阶段 4 域契约证据（2026-09-14）**：manifest 校验现在正式检查命令 `domain_ids`：必须是非空、无重复且范围为 P-01～P-18 的字符串列表；非法域在执行前拒绝。修复测试插入错误后，全套 `tests/product_testkit` 结果 **72 passed**，ruff 检查通过。域覆盖完整性门禁和真实产品命令映射仍待完成。

**阶段 4 覆盖门禁证据（2026-09-14）**：新增 `require_domain_coverage`，对适用测试域要求至少存在一个显式 `domain_ids` 命令映射；缺失域输出明确 ID 并 fail-closed。与 planner、manifest、executor 相关测试联调后全套 `tests/product_testkit` 结果 **74 passed**，ruff 检查通过。该门禁尚未接入真实 runner 的最终执行路径。

**阶段 4 runner 接线证据（2026-09-14）**：`run_product_plan(..., enforce_domain_coverage=True)` 现在会在启动任何产品命令前执行适用域覆盖门禁；旧 manifest 默认保持诊断兼容，新 manifest 可显式启用严格模式。联调后全套 `tests/product_testkit` 结果 **75 passed**，ruff 检查通过。迁移所有真实产品 manifest 并默认启用严格模式仍待完成。

**阶段 4 统一入口证据（2026-09-14）**：新增 `run_manifest_file` 作为 CI/生成产品的标准文件入口：从磁盘加载并校验 manifest，强制启用域覆盖，再执行产品计划；新增严格入口联调测试后全套 `tests/product_testkit` 结果 **76 passed**，ruff 检查通过。真实 candidate/run contract、报告上传和 CI 接线仍未完成。

**阶段 4 域调度证据（2026-09-14）**：`run_manifest_file`/`run_product_plan` 增加 `selected_domains`，按命令显式 `domain_ids` 实际筛选执行，同时对选定域执行覆盖门禁；新增选择性调度联调测试后全套 `tests/product_testkit` 结果 **77 passed**，ruff 检查通过。全量域自动调度、未映射域最终报告和真实产品命令接入仍待完成。

**阶段 9 fixture 真实性证据（2026-09-14）**：将 `test_foundation_flow.py` 的基础 fixture 改为临时独立 Git 产品项目（独立 `product.py`、独立 commit、真实 import 执行），不再在 ZaoFu 仓库根目录运行 print 命令；fixture 显式覆盖 P-01～P-05、P-17、P-18，并通过严格统一执行路径。全套 `tests/product_testkit` 结果 **77 passed**，ruff 检查通过。该 fixture 仍是最小通用样例，尚未覆盖 API/Web/数据库/部署等产品类型。

**阶段 8 计划证据（2026-09-14）**：`verification-result.v1` 现在包含执行时的 `domains` 计划，保留 capability 选择与 not_applicable 裁决的可追溯信息；与报告、域调度和独立 fixture 联调后全套 `tests/product_testkit` 结果 **77 passed**，ruff 检查通过。报告尚未绑定真实 Product Acceptance/candidate 身份。

**阶段 8 failure bundle 证据（2026-09-14）**：新增 `tests/product_testkit/failure_bundle.py`，从 verification-result 提取失败/未运行命令、run/candidate/commit、domains 和 missing commands，原子写入 `product-test-failure-bundle.v1`；联调后全套 `tests/product_testkit` 结果 **78 passed**，ruff 检查通过。统一 runner 已自动写 bundle，但尚未接入 CI 上传。

**阶段 8 统一闭环入口证据（2026-09-14）**：新增 `tests/product_testkit/runner.py` 的 `run_manifest_and_collect`，一次完成 manifest 加载、严格域准入、命令执行、verification-result、JUnit 与 failure bundle 写入；新增独立临时 Git 产品 fixture 联调后全套 `tests/product_testkit` 结果 **79 passed**，ruff 检查通过。Product Acceptance report 已通过可选 spec/results 接入；candidate/run contract 和 CI 上传仍未接入该入口。

**阶段 8 acceptance 接线证据（2026-09-14）**：`run_manifest_and_collect` 增加可选 Product Acceptance spec、spec digest、candidate 和 journey_results 参数；提供时会调用严格 report builder 并写入 `product-acceptance-report.json`，缺失 evidence/结果会 fail-closed。新增独立 fixture 联调后全套 `tests/product_testkit` 结果 **80 passed**，ruff 检查通过。真实 ZaoFu candidate/run contract 和 CI 门禁仍待接入。

**阶段 8 acceptance 原子写入证据（2026-09-14）**：Product Acceptance 报告改为临时文件替换的原子写入方式，避免中断留下半写 JSON；与统一 runner 联调后当前全套 `tests/product_testkit` 结果 **86 passed**，ruff 检查通过。

**阶段 4 API fixture 证据（2026-09-14）**：新增 `test_api_fixture_flow.py`，启动独立临时 HTTP 产品服务，验证未授权请求拒绝、非法输入 400、合法创建 201、响应 schema 和状态变化；全套 `tests/product_testkit` 结果 **81 passed**，ruff 检查通过。该 fixture 尚未接入 manifest runner，也未覆盖真实 OpenAPI/Schemathesis。

**阶段 4 持久化 fixture 证据（2026-09-14）**：新增 `test_persistence_fixture_flow.py`，使用独立临时 SQLite 产品数据库验证 schema 初始化、NOT NULL/UNIQUE 约束、重复写拒绝、备份与恢复读取；全套 `tests/product_testkit` 结果 **86 passed**，ruff 检查通过。真实数据库/Testcontainers、多进程事务和迁移版本矩阵仍待完成。

**阶段 8 CI 分层证据（2026-09-14）**：新增 `tests/product_testkit/tiers.py` 与 `test_tiers.py`，按 manifest command tier 生成 T0（unit）、T1（integration）、T2（e2e）、T3（security/perf/release）命令计划；联调后全套 `tests/product_testkit` 结果 **82 passed**，ruff 检查通过。该计划尚未接入仓库 CI 和 dev-verify。

**阶段 8 报告分层接线证据（2026-09-14）**：`verification-result.v1` 新增 `ci_plan`，记录当前 manifest 命令对应的 T0～T3 最低执行层；与 report/runner 联调后全套 `tests/product_testkit` 结果 **82 passed**，ruff 检查通过。实际 CI 调度、dev-verify 选择和门禁失败策略仍未接入。

**阶段 8 失败闭环证据（2026-09-14）**：新增统一 runner 失败 fixture，验证产品命令退出非零时仍生成 `verification-result.json`、JUnit failure 和包含失败 command ID 的 failure bundle；全套 `tests/product_testkit` 结果 **83 passed**，ruff 检查通过。CI 上传、失败重试策略和远端门禁仍未接入。

**阶段性联调审计（2026-09-14）**：重新执行 `uv run pytest -q --no-cov tests/product_testkit`、`uv run ruff check tests/product_testkit` 和 `git diff --check`，结果分别为 **83 passed**、ruff 全部通过、无 diff 格式错误。该结果仅证明当前 TestKit 支撑层和最小 fixture 的组合正确，不代表 P-01～P-18 已对任意真实产品完整覆盖。

**当前续作审计（2026-09-15）**：重新执行 `uv run pytest -q --no-cov tests/product_testkit`，结果为 **124 passed, 1 warning, 20.07s**。该结果确认通用 manifest、域规划、命令执行、报告、失败包及最小独立 fixture 仍可联调。真实产品项目不是继续搭建通用 TestKit 的前置条件；但真实产品适配、服务/数据库/浏览器运行和业务验收断言必须以具体产品为输入，不能用本 fixture 的通过结果替代。

**产品输入分层约束（2026-09-15）**：

- 通用层：使用最小独立 Git fixture，验证 TestKit 的 manifest、runner、oracle、证据和失败分类。
- 适配层：使用至少一个真实生成产品（当前工作树可候选 `awesome-zaofu/generated-product-project`），验证其 manifest、技术栈命令、source root、target commit、entrypoint、health check 和 cleanup。
- 端到端层：使用真实产品执行适用的 P-06～P-16 及 P-18，取得产品自身的 API/Web/数据/部署/性能证据；没有这些输入时只能保持未完成，不能标记为通过。

**真实产品矩阵准入审计（2026-09-15）**：对候选 `awesome-zaofu/generated-product-project/planning/generated-product-v1/generated/test_matrix.json` 执行 `audit_test_matrix`，发现 **19 项准入问题**：17 条命令缺少 `domain_ids`，其中 2 条同时缺少 `acceptance_ids`。适配器按设计 fail-closed，未擅自推断能力覆盖关系，因此该真实产品目前不能进入严格 runner；下一步应在产品项目侧补齐每条命令的 acceptance/domain 绑定，再执行真实适配测试。

**真实产品自身测试基线（2026-09-15）**：在 `/home/uisee/zaofu_v7/awesome-zaofu/generated-product-project` 目录内执行 `uv run pytest -q --no-cov`，结果为 **188 passed, 1 failed, 104 warnings（67.69s）**。唯一失败为 `tests/release/compliance/test_runtime_compliance.py::test_runtime_sbom_is_closed_over_frozen_runtime_locks`：`sbom/runtime/index.json` 中记录的 `uv.lock` SHA-256 与当前 `uv.lock` 不一致。该结果归类为产品项目发布合规失败，不修改产品代码，也不登记为 ZaoFu 缺陷；测试保留作为产品测试应能检出的回归证据。此前一次从 ZaoFu 根目录误收集导致的 997 个导入错误已排除，不纳入产品结果。

**真实产品 Node/Web 测试基线（2026-09-15）**：在产品目录执行 `npm test`，结果为 **22 passed, 1 failed**；失败用例 `src/hmi/tests/web/hmi/contract.test.ts` 因其内部调用 `python -m pytest tests/api -q`，当前运行环境没有名为 `python` 的可执行文件（`spawnSync python ENOENT`），归类为产品测试入口/环境兼容失败，不修改产品代码。随后 `npm run typecheck && npm run build` 成功；构建仅输出 bundle 大于 500 kB 的 warning。该证据说明真实产品已具备可执行的 Web 静态检查和构建入口，但仍需在 manifest 中声明正确的运行前置和失败分类。

**真实产品 API 域补充基线（2026-09-15）**：在产品目录用其 `uv` 环境直接执行 `uv run pytest tests/api -q --no-cov`，结果为 **27 passed, 104 warnings（14.27s）**。这证明 API 测试本身可执行且当前通过；Web `npm test` 的失败是其跨语言子命令使用 `python` 名称造成的入口兼容问题，而不是 API 用例失败。该差异应在通用 TestKit 中分别记录为产品测试命令失败与 API 域通过，不能用聚合退出码掩盖域级结果。

**真实产品配置/注册/会话/仿真域基线（2026-09-15）**：执行 `uv run pytest tests/config tests/registry tests/session tests/simulation tests/strategies tests/training tests/assembly -q --no-cov`，结果为 **138 passed（66.52s）**。这些目录覆盖配置、注册表、会话控制、仿真、策略、训练和装配等已存在产品测试域；结果仅证明当前声明测试集合通过，不等于完整 acceptance 矩阵已闭包。

**真实产品浏览器 E2E 基线（2026-09-15）**：读取产品 `package.json` 确认 `test:e2e` 为 `playwright test`，在产品目录执行后，4 个用例中 1 个在 `beforeAll` 启动 `./scripts/start-demo.sh` 时超过 30 秒超时，另外 3 个未执行。该结果归类为产品 E2E 服务启动/运行环境失败，不能判为业务断言失败；必须在 TestKit 中保留启动日志、超时和未执行用例，并在服务可启动后重跑。

**Docker/浏览器环境前置核验（2026-09-15）**：在 ZaoFu 根目录执行 `docker info` 与 `docker image inspect mcp/playwright:latest` 均成功（Docker Server 29.7.1，Playwright 镜像存在）。因此当前 E2E 阻塞不是 Docker 不可用；应优先处理产品 `start-demo.sh` 对 `python` 可执行名的运行时依赖，再按 Docker Playwright 规范重跑。TestKit 不得把 Docker 已具备误报为 E2E 通过。

**真实产品 Python 发布构建基线（2026-09-15）**：在产品目录执行 `uv build` 成功生成 `dist/generated_product-0.1.0.tar.gz` 与 `dist/generated_product-0.1.0-py3-none-any.whl`。这仅证明当前源码可构建 wheel/sdist；尚未证明干净环境安装、升级/回滚、产物运行和 SBOM 摘要一致性（后者当前已有失败）。

**真实产品干净环境安装基线（2026-09-15）**：在临时 Python 3.11 虚拟环境中安装 wheel 并按产品元数据解析依赖，安装成功且 `import generated_product` 成功。单独使用 `--no-deps` 的导入失败（缺少 `gymnasium`）被确认是预期依赖缺失，不作为产物损坏；正式依赖安装路径通过。升级/回滚和多版本矩阵仍未执行。

**真实产品 Python 3.12 安装矩阵补充（2026-09-15）**：`uv python list --only-installed` 确认本机提供 CPython 3.12.3；在临时 3.12 虚拟环境安装同一 wheel 及正式依赖并导入 `generated_product` 成功。当前已验证 3.11/3.12 两个版本的安装导入；升级/回滚、3.13 和容器矩阵仍未执行。

**真实产品 release 回归复现（2026-09-15）**：单独执行 `uv run pytest tests/release -q --no-cov`，稳定得到 **2 passed, 1 failed**；失败仍为 `test_runtime_sbom_is_closed_over_frozen_runtime_locks`，明确指出 `sbom/runtime/index.json` 的 `uv.lock` SHA-256 与当前锁文件不一致。该复现确认发布合规失败可被 TestKit 稳定检出，而非偶发环境噪声。

**阶段 8 CI 层级执行证据（2026-09-14）**：`tests/product_testkit/tiers.py` 新增 `commands_for_ci_tier`，按目标层级返回包含所有低层依赖的命令集合，并拒绝未知层级；联调后全套 `tests/product_testkit` 结果 **84 passed**，ruff 检查通过。该函数尚未驱动实际 CI job。

**阶段 8 持久化报告联调证据（2026-09-14）**：统一 runner 测试现在读取实际写盘的 `verification-result.json`，确认其中同时保留 `ci_plan` 与 `domains`；全套 `tests/product_testkit` 结果 **84 passed**，ruff 检查通过。CI job、报告上传和远端门禁仍未接入。

**计数校正（2026-09-14）**：加入 SQLite 持久化 fixture 后，当前工作树重新执行 `uv run pytest -q --no-cov tests/product_testkit` 的真实结果为 **86 passed**；此前 84/85 为历史阶段快照，仅用于记录增量，不代表当前总数。

**阶段 8 CI 层级执行接线证据（2026-09-14）**：统一 runner 增加 `ci_tier` 参数，调用 `commands_for_ci_tier` 选择目标层及其低层依赖命令，并通过 executor 的 `selected_command_ids` 实际调度；新增 T0 选择性执行 fixture 后全套 `tests/product_testkit` 结果 **85 passed**，ruff 检查通过。报告对未执行的高层命令仍标记 `NOT_RUN`，真实 CI job 接线仍待完成。

## 阶段 3：通用基础域 P-01～P-05

- [x] P-01：在最小独立 Git 产品 fixture 中验证 source root、target commit、run identity、输出 root 和 artifact ref；candidate/run contract 的真实接入仍待完成。
- [x] P-02：在 fixture 中验证隔离产品 root、命令工作目录、环境 evidence 和清理；真实依赖安装、锁文件和跨目录写入审计仍待完成。
- [x] P-03：通过 manifest command runner 逐条执行声明命令并记录 receipt；跨语言真实 build/lint/typecheck 矩阵仍由产品 manifest 提供。
- [x] P-04：在 fixture 中执行声明的 smoke/unit 命令并以退出码和 receipt 判定；产品业务边界/非法/重复输入仍需产品自身测试命令声明。
- [x] P-05：通过 matrix helper 和严格域覆盖门禁验证 capability→acceptance→test command 闭包；真实产品 admission 尚待接入。
- [ ] 为上述每个域建立固定成功、固定失败、重复执行和错误环境 case。

**完成判据**：P-01～P-05 在没有具体业务项目时可由最小通用 fixture 验证 runner 和 oracle；真实项目接入时只替换 manifest 和产品断言。

**阶段 3 当前证据（2026-09-14）**：`tests/product_testkit/test_foundation_flow.py` 使用临时独立 Git 项目完成 P-01～P-05 的 runner/oracle 联调；`uv run pytest -q --no-cov tests/product_testkit` 当前为 **101 passed**。该证据证明通用基础设施可执行，不证明任意生成产品的业务测试已通过。

**阶段 3 预备证据（2026-09-14）**：新增 `tests/product_testkit/matrix.py`，校验 blocking capability→acceptance→test command 闭包及悬空引用；`uv run pytest -q --no-cov tests/product_testkit` 结果 **30 passed**，ruff 检查通过。P-01～P-05 的真实产品身份和业务断言仍需通用 fixture。

**阶段 3 联调证据（2026-09-14）**：新增 `tests/product_testkit/test_foundation_flow.py`，使用当前 commit 动态生成通用 library fixture，联调 manifest、matrix、target commit、命令 receipt 和 artifact ref；`uv run pytest -q --no-cov tests/product_testkit` 结果 **31 passed**，ruff 检查通过。该 fixture 不代表具体产品业务行为；真实产品接入仍需其 manifest 和 acceptance 断言。

**阶段 4 P-06 进展（2026-09-14）**：新增 `tests/product_testkit/cli.py` 和 `test_cli.py`，提供产品无关的 CLI 正常输出、错误退出码、超时和输出断言；全套 `tests/product_testkit` 结果 **33 passed**，ruff 检查通过。API/Web/数据/外部集成适配器尚待后续阶段。

**阶段 4 P-07 进展（2026-09-14）**：新增 `tests/product_testkit/api.py` 和 `test_api.py`，提供标准库 HTTP/JSON 请求、Bearer token、状态码、错误响应和 JSON 字段断言；全套 `tests/product_testkit` 结果 **34 passed**，ruff 检查通过。Schemathesis 自动生成、OpenAPI 状态序列和真实产品 API 状态持久化联调尚待后续阶段。

**阶段 4 P-08 进展（2026-09-14）**：新增 `tests/product_testkit/web.py` 和 `test_web.py`，复用现有 Web truth helper，验证 canonical/API/DOM/刷新状态收敛及 SSE 事件单次投递；全套 `tests/product_testkit` 结果 **36 passed**，ruff 检查通过。实际 Playwright + Docker 浏览器旅程仍需产品 manifest 和运行服务。

**阶段 4 P-09 进展（2026-09-14）**：新增 `tests/product_testkit/persistence.py` 和 `test_persistence.py`，提供文件状态快照、JSON 状态精确断言、符号链接拒绝和重复操作幂等校验；全套 `tests/product_testkit` 结果 **38 passed**，ruff 检查通过。真实数据库/Testcontainers、迁移和备份恢复适配尚待后续阶段。

**阶段 4 P-10 进展（2026-09-14）**：新增 `tests/product_testkit/integrations.py` 和 `test_integrations.py`，提供外部 sink 调用计数、合成失败、有限重试和失败不伪造成功断言；全套 `tests/product_testkit` 结果 **40 passed**，ruff 检查通过。真实第三方协议、Pact 和 Testcontainers 适配尚待后续阶段。

**阶段 5 P-11 进展（2026-09-14）**：新增 `tests/product_testkit/security.py` 和 `test_security.py`，提供产品根目录边界、符号链接解析和 secret-like 文本扫描断言；全套 `tests/product_testkit` 结果 **41 passed**，ruff 检查通过。认证授权矩阵、命令注入和真实 ZAP 扫描尚待后续阶段。

**阶段 5 P-12 进展（2026-09-14）**：新增 `tests/product_testkit/recovery.py` 和 `test_recovery.py`，提供恢复后与无故障 baseline 状态等价断言及可忽略运行时字段处理；全套 `tests/product_testkit` 结果 **43 passed**，ruff 检查通过。真实进程崩溃、重启和依赖故障注入尚待后续阶段。

**阶段 5 P-13 进展（2026-09-14）**：新增 `tests/product_testkit/concurrency.py` 和 `test_concurrency.py`，提供业务键唯一性和状态单调性断言，覆盖重复记录与乱序回退；全套 `tests/product_testkit` 结果 **44 passed**，ruff 检查通过。真实多进程/并发请求矩阵尚待产品适配。

**阶段 6 P-14 进展（2026-09-14）**：新增 `tests/product_testkit/performance.py` 和 `test_performance.py`，复用 k6 摘要阈值 oracle，要求 metric 样本、threshold 报告和 exit code 一致，区分预算失败与 harness failure；全套 `tests/product_testkit` 结果 **45 passed**，ruff 检查通过。真实 k6 pressure/soak 仍需发布环境执行。

**阶段 6 P-15 进展（2026-09-14）**：新增 `tests/product_testkit/release.py` 和 `test_release.py`，提供 wheel/sdist/npm/container/binary 产物类型、扩展名、非空和 SHA-256 digest 校验；全套 `tests/product_testkit` 结果 **47 passed**，ruff 检查通过。真实构建、干净环境安装、升级/回滚矩阵尚待后续阶段。

**阶段 6 P-16 进展（2026-09-14）**：新增 `tests/product_testkit/observability.py` 和 `test_observability.py`，提供结构化日志必填字段和敏感字段脱敏断言；全套 `tests/product_testkit` 结果 **48 passed**，ruff 检查通过。真实产品日志/trace/health 关联、错误分类和诊断可回读仍待运行链路接入。

## 阶段 4：产品表面和依赖域 P-06～P-10

- [~] P-06 CLI：TestKit 通用 CLI oracle 已完成；真实产品 CLI 入口尚未声明/验证。
- [x] P-07 API：通用 manifest 已接入真实 Schemathesis/OpenAPI stateless runner、版本/phase/operation 语义判定、正向 HTTP 与负向输入 finding、域/CI/报告/证据绑定；stateful 链式写操作归入后续隔离恢复/并发 profile。
- [~] P-08 Web：typecheck/build 已通过；Playwright E2E 因服务启动超时，Docker 浏览器和完整旅程尚未通过。
- [~] P-09 数据：TestKit SQLite fixture 和产品内部持久化测试已覆盖部分行为；真实迁移/Testcontainers/恢复矩阵仍待完成。
- [~] P-10 外部集成：mock/contract、超时、重试、错误响应、凭据脱敏和副作用计数已由 fake 服务/spy 覆盖；Pact/Toxiproxy/真实第三方协议仍按产品边界条件接入。
- [x] 每个适用域必须有能力声明；不适用域输出带理由的 `NOT_APPLICABLE`。

**完成判据**：产品表面输出、真实状态、外部交互记录和测试报告相互一致；非法输入不能变成成功。

## 阶段 5：安全、恢复和并发域 P-11～P-13

- [ ] P-11：越权、路径逃逸、命令注入、secret 泄漏、恶意文本、错误 token 和 project scope。
- [~] P-12：通用 manifest recovery scenario 已支持超时、进程退出、重启和 baseline 等价；产品 commit-point/依赖故障仍需项目适配。
- [~] P-13：通用 manifest concurrency scenario 已支持多 worker、有限墙钟预算、退出码和结果证据；乱序消息/事务幂等仍需产品适配。
- [x] 为危险输入到 shell、文件系统、数据库、git 和远端 API 建立统一 sink spy。
- [x] 记录恢复前后状态快照、调用次数、重试次数和禁止副作用。

**完成判据**：未授权成功次数为 0；恢复后与无故障 baseline 等价；重复输入不产生重复业务对象或副作用。

## 阶段 6：性能、发布和观测域 P-14～P-16

- [~] P-14：TestKit 的性能阈值 oracle 已完成；真实产品 k6/压力/soak 基线尚未执行。
- [~] P-15：产品 Web 构建已通过，Python 全量含发布合规失败；干净安装、升级和回滚矩阵尚未完成。
- [~] P-16：TestKit 脱敏/结构化日志 oracle 已完成；真实产品日志、trace、health 关联尚未形成统一 evidence。
- [x] 性能和安全扫描必须有明确阈值，只有观测数据不能判定通过。
- [x] 将长时性能、真实 ZAP 和发布矩阵放入 release/nightly tier，不阻塞普通快速测试但不能伪装成已完成。

**完成判据**：阈值结果可机器判定；产物不依赖源码目录或隐式环境；关键失败可定位且无敏感信息泄漏。

## 阶段 7：回归、交付和 Issue 闭环 P-17～P-18

- [~] P-17：通用 IssueMergeLoop 已提供稳定 Issue revision、测试失败阻断和合并后回归顺序；具体产品 Issue 的重现命令由产品项目提供。
- [~] P-18：通用 delivery-readiness oracle 已验证 verification、candidate、commit、artifact digest、Product Acceptance 和 cleanup 绑定；ZaoFu run contract、state dir、remote policy 与 ControlledAction 的实际接线仍待完成。
- [~] 通用层已实现并测试 `Issue ingress → triage → approval intent → Fix Run → product tests → evidence → candidate → regression → merge → post-merge regression`；真实 ZaoFu ingress/ControlledAction/远端 merge 仍需适配器。
- [~] merge 前的产品测试、身份、域覆盖和 evidence 机械门禁已经具备；Issue regression/impact closure 输入和实际 merge sink 的零调用联调仍待完成。
- [x] ZaoFu 自身缺陷只记录到 `ZaoFu_issue.md` 的 `issue found from product test`，不记录具体产品业务缺陷或通用产品缺陷。

**完成判据**：交付证据绑定正确 commit；未授权 push/merge 为 0；Issue 修复后能自动重跑受影响闭包。

## 阶段 8：统一 runner、报告和 CI 门禁

- [x] 实现按 manifest 能力选择 P-01～P-18 的 runner，并支持按域和 T0～T3 层级筛选实际命令。
- [~] 汇总 command receipts、verification-result、Product Acceptance Report、JUnit/JSON、artifact digest 和 failure bundle；本地生成与一致性已接通，CI 上传和 ZaoFu candidate/run 绑定尚未完成。
- [x] 建立 T0 快速正确性、T1 能力测试、T2 合并前、T3 发布/定期的计划与本地执行选择。
- [~] 已完成失败分类、禁止缺失执行伪绿、资源清理和本地证据持久化；CI 证据上传与跨 job 聚合尚未完成。
- [~] 通用 delivery-readiness 已固定 `merge_authorized=false` 并拒绝 Agent 自报完成；ZaoFu candidate/goal closure 的真实运行入口仍需接线。

**完成判据**：同一 manifest 在独立进程中可重放，输出唯一、可审计的 pass/fail/not_applicable/infrastructure_error。

## 阶段 9：通用 fixture、跨域联调和最终审计

- [x] 已建立不针对具体业务的最小 Git 产品 fixture，覆盖入口/health、命令、API、Web、持久化、恢复/并发、证据和清理。
- [~] P-01～P-18 的通用 manifest→runner→产品状态→evidence→acceptance→delivery-readiness 链路已联调；真实 candidate/run contract 仍需 ZaoFu 适配器。
- [x] fixture 已注入正常、失败、重复、乱序、并发、重启、不可信输入、发布和安全报告等可控场景。
- [x] `NOT_APPLICABLE`、`NOT_RUN`、`HARNESS_FAILURE`、`ENVIRONMENT_FAILURE` 和 `FAIL` 均有结构化 verdict、JUnit 与 failure bundle，禁止静默忽略。
- [x] `ZaoFu_Product_TestKit.md`、本文件和 TestKit receipt 已形成覆盖矩阵、未完成项、环境前置条件和复现证据。

**完成判据**：通用参考产品 fixture 的端到端闭环通过；真实产品项目只需提供 manifest、产品断言和项目命令即可接入，不需要重建 TestKit。真实产品为兼容性 canary，不是闭环建设或阶段 9 完成的前置条件。

## 失败处理与变更纪律

- 产品项目自身 bug：由产品项目维护者处理，不写入 ZaoFu Issue；测试必须保留并失败。
- ZaoFu 生产 bug：不修改 `src/`；在 `ZaoFu_issue.md` 增加 `issue found from product test` 条目，并保留最小可复现测试。
- 通用产品缺陷：不为单个项目建立 Issue；把可复用的测试断言保留在通用 TestKit 中。
- 每完成一个阶段，必须执行该阶段测试与此前阶段的联调，并记录命令、commit、结果、证据路径和资源清理结果。
- 任何真实环境专项未执行时，必须标记为 `NOT_RUN` 或环境缺口，不能以 mock/适配器结果替代。

## need to do：当前未完成项

> 范围说明：下列事项是通用 TestKit 之外的真实产品适配、宿主环境验证或发布运营接线。它们必须在有对应产品输入和环境时完成；缺少这些条件时应输出 `NOT_RUN`、`ENVIRONMENT_FAILURE` 或 `admission_failure`，不得用参考产品 fixture 的结果冒充真实产品通过。它们不否定通用 TestKit 已完成的独立闭环。

- [ ] **真实产品准入与矩阵**：为每个产品提供并审计 `product-test-manifest.v1`，包括 `source_root`、不可变 `target_commit`、项目类型、capabilities、`domain_ids`、`acceptance_ids`、`not_applicable` 理由、setup/cleanup、entrypoint/health、`runtime_paths`、测试命令、预算和阈值；确认命令、能力、验收和证据无悬空引用。
- [ ] **真实产品业务测试与验收**：在目标 commit 上执行产品自身的 build/lint/typecheck、unit/component、CLI/API/Web 和 mandatory Product Acceptance journeys；每个结果必须绑定 acceptance、run/candidate、commit、artifact ref/digest，并由独立 oracle 判定，不能由实现 Agent 自报通过。
- [ ] **真实 Web 浏览器链路（P-08）**：对实际产品启动 API/UI 于 `0.0.0.0`，用 Docker Playwright 执行关键旅程、DOM/interaction/state/visual 断言、刷新收敛、network/console/trace；将旅程结果接入产品 acceptance schema。浏览器或 Docker 不可用时记录环境失败。
- [ ] **真实数据库/队列（P-09）**：按产品声明启动 PostgreSQL/MySQL/Redis/消息队列等依赖，执行 migration、schema/约束、事务、CRUD、重复写、消息确认、备份恢复和清理；保留容器 health、数据快照、协议 receipt 和恢复前后状态证据。
- [ ] **真实外部服务协议（P-10）**：对存在稳定 Consumer/Provider 边界的产品接入 Pact 版本契约；需要网络故障时接入 Toxiproxy；对真实第三方协议验证超时、重试上限、错误响应、凭据脱敏和失败不伪造成功。无此边界的产品须声明 `not_applicable` 及理由。
- [ ] **产品特定恢复/并发/故障点（P-12/P-13）**：补齐数据库事务中断、消息乱序、commit-point 崩溃、重复 webhook、stale worker、进程重启、多实例副作用等价和状态 reader；证明恢复后状态等于无故障 baseline，副作用次数不超过合同上限。
- [ ] **真实性能与长期稳定性（P-14）**：在稳定隔离环境执行产品声明的 k6 throughput/latency/error thresholds、压力/突发和长时间 soak；建立 p95/p99、资源占用、句柄/端口/进程泄漏和连接恢复预算。单次 smoke 不得替代性能基线。
- [ ] **动态安全扫描（P-11）**：冻结 digest-pinned ZAP scanner、扫描目标、认证账号、范围和风险阈值，在隔离产品环境执行 passive/active scan；高风险问题或超过阈值必须失败，扫描器不可用必须归类环境/未运行。
- [ ] **发布安装与兼容矩阵（P-15）**：在干净虚拟环境验证 wheel/sdist/npm/container 安装、启动、升级、回滚和清理；执行声明的 Python/Node/Go/Rust 运行时矩阵，并为每个产物保存 digest、安装日志和回滚证据。
- [ ] **真实观测与诊断证据（P-16）**：将产品日志、health、trace、审计事件和错误诊断关联到统一 evidence；验证失败可定位、日志与实际状态一致且无 secret 泄漏。
- [ ] **ZaoFu 交付边界接线（P-18）**：把 Product Acceptance、verification-result、candidate/run contract、target commit、state dir、artifact digest、remote policy 和 `ControlledAction` 接入真实 ZaoFu 运行入口；TestKit 只报告 delivery readiness，不直接执行 push/merge。
- [ ] **真实 Issue→merge 闭环（P-17/P-18）**：在真实 ZaoFu ingress、triage、审批意图、Fix Run、产品测试、candidate、ControlledAction、merge callback 和 post-merge regression 环境中联调；覆盖重复 webhook、旧 revision、未授权操作、前置失败阻断和跨实例持久化去重。
- [ ] **真实 CI 与证据运营接线**：在 `.github/workflows` 或等价 CI 中接入 T0～T3 job、资源配额、失败重试禁止伪绿、跨 job 汇总，以及 verification-result/JUnit/Product Acceptance/failure-bundle 上传和保留策略；在远端 GitHub Actions runner 上至少完成一次验证。
- [ ] **真实产品 canary 与回归维护**：产品契约变更后重新执行 manifest admission、受影响域、T0/T1、Product Acceptance 和发布层 canary；产品自身失败应保留为产品 verdict，ZaoFu 生产缺陷才按约定登记到 `ZaoFu_issue.md`，不得互相混淆。

### 阶段 6/9 增量：通用外部服务契约联调（2026-09-15）

- [x] `product-test-manifest.v1` 新增可选 `mock_services[]`：固定服务 ID、不可重复的 method/path、响应状态/body 和有限 transient failure；服务 ID 冲突、空路由、非法 method/path/status、重复路由和负重试次数均 fail-closed。
- [x] 新增 `tests/product_testkit/mocks.py`：启动隔离 localhost fake HTTP 服务，按声明返回响应，记录请求 method/path、脱敏 header 和 body SHA-256，注入 `ZF_MOCK_<ID>_BASE_URL`，并在 finally 中停止线程、写入 requests/receipt 证据。
- [x] executor 接线到 `dependency_containers → mock_services → entrypoints → 产品测试`；mock receipt 接入 ProductRun、verification-result、JUnit、failure bundle 和 artifact index。fake 服务停止失败归 harness failure，不被重试掩盖。
- [x] 新增固定 transient 503→成功的 fake 服务测试，以及真实 runner 产品 fixture：产品命令通过注入 URL 读取响应，报告保留请求计数和脱敏证据。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit`：**256 passed, 3 warnings**；`uv run ruff check tests/product_testkit`：**All checks passed**。警告仍为既有 Hypothesis 配置和 integration marker。
- [ ] 尚未把 Pact consumer/provider 版本契约、Toxiproxy 网络故障和真实第三方协议接入通用 runner；这些需要项目声明稳定服务边界，不影响 fake 契约闭环和通用 TestKit 独立验收。

### 阶段 7/9 增量：真实进程恢复与并发执行设施（2026-09-15）

- [x] 扩展 `tests/product_testkit/recovery.py`：`run_crash_restart` 启动产品 worker、等待显式 ready 文件、读取 JSON baseline、发送进程组 `SIGKILL`、重新启动并比较恢复状态，最终强制清理；返回 `CrashRecoveryReceipt`，区分重启、清理和状态不一致。
- [x] 扩展 `tests/product_testkit/concurrency.py`：`run_concurrent_processes` 在独立进程中并发启动多个产品 worker，使用有限墙钟预算、超时进程组清理和逐进程退出码；与 `assert_unique_keys`、`assert_status_monotonic` 组合验证重复对象和状态回退。
- [x] 新增真实临时 worker fixture：硬杀后重启仍保持 state JSON；4 个并发 worker 的记录全部落盘且业务 ID 唯一。该 fixture 验证 TestKit 设施，不假设任何具体产品业务语义。
- [x] 公共入口导出恢复/并发 receipt 和执行函数；阶段局部回归 `7 passed`，Ruff 通过。
- [x] 产品声明的 recovery/concurrency scenario 已纳入 manifest runner，并有统一 receipt、verdict、JUnit、failure bundle、artifact index 和稳定性映射；仍未覆盖数据库事务、消息乱序、ZaoFu commit-point failpoint 和多实例副作用等价，这些需产品提供 state reader、ready/cleanup 和依赖拓扑后接入。

### 阶段 8/9 增量：通用 k6 性能契约联调（2026-09-15）

- [x] `product-test-manifest.v1` 新增可选 `performance_tests[]`：固定脚本相对路径、digest-pinned/明确镜像、`perf` tier、acceptance/domain（必须含 P-14）、有限墙钟预算和非空阈值列表；路径、镜像、阈值运算符/数值和 ID 冲突均 fail-closed。
- [x] 新增 `tests/product_testkit/k6.py`：使用 Docker host-network、只读产品挂载、可写 evidence 挂载、显式环境变量和 `--summary-export`；解析 summary、threshold、退出码和超时，区分 `pass`、性能 `fail`、`environment_failure` 与 `harness_failure`。
- [x] 修正 k6 旧版 `--summary-export` 真实格式兼容：旧格式直接把统计项放在 metric 下，且 threshold boolean 表示“是否越过阈值”（`true` 为 breached）；TestKit 先规范化后与独立计算对账，避免把真实通过误判为 harness 错误。
- [x] performance 结果接入 executor、CI tier 选择、domain coverage、verification-result、JUnit、failure bundle、stability 和 artifact index；summary、stdout/stderr、receipt 和阈值判定均可追溯。
- [x] 使用本地 HTTP 服务和 `grafana/k6:1.5.0` 完成真实单迭代 smoke，验证 summary threshold 及产品命令环境隔离；P-14 真实 fixture 与已有 TestKit 其余域联调通过。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit`：**271 passed, 3 warnings**；`uv run ruff check tests/product_testkit`：**All checks passed**。性能/k6 与此前所有阶段联调通过。
- [ ] 尚未执行长时间 soak、压力级并发、资源泄漏预算和真实产品声明的性能基线；没有声明阈值或工具不可用时必须输出 `NOT_RUN`/环境失败，不能用一次 smoke 推断发布性能达标。

### 阶段 5/9 增量：通用依赖容器生命周期联调（2026-09-15）

- [x] `product-test-manifest.v1` 新增可选 `dependency_containers[]`：固定稳定 ID、镜像 token、单行启动/health 命令、端口映射、有限超时和 mandatory；与测试单元 ID 冲突、换行/空白注入、非法端口和非布尔字段均 fail-closed。
- [x] 新增 `tests/product_testkit/containers.py` 的一次性 Docker 生命周期适配器：`docker run --detach --rm`、容器内 health probe、宿主 UID 无关的命名、`ZF_CONTAINER_<ID>_NAME/ID` 环境注入、日志/health/receipt 证据、强制移除和清理失败记录。该层保持 Testcontainers 可替换，不引入第二套测试执行框架。
- [x] executor 接线顺序为 `setup → dependency_containers → entrypoints/health → 产品命令/API/Web → 服务停止 → 容器移除 → 顶层 cleanup`；容器启动/health 故障分类为 `environment_failure`，容器 evidence/移除异常分类为 harness error，不将依赖故障伪报为产品业务失败。
- [x] 容器结果已接入 `ProductRun`、verification-result、JUnit、failure bundle 和 artifact index；成功运行要求每个声明容器 healthy、stopped 且无错误。
- [x] 新增 manifest/runner 单元测试及真实 Docker Alpine 产品 fixture：产品命令读取 TestKit 注入的容器身份，验证健康、停止、receipt 和移除后 `docker inspect` 失败；本 fixture 不代表任何具体产品业务质量。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit`：**246 passed, 3 warnings**；`uv run ruff check tests/product_testkit`：**All checks passed**。警告仅为既有 Hypothesis 配置及两个既有 integration marker。
- [ ] 尚未在真实产品声明的 PostgreSQL/MySQL/Redis/队列上执行 migration、事务、备份恢复、协议和数据约束测试；该项需产品 manifest 提供真实镜像、端口、初始化/迁移/清理命令，不构成通用闭环的前置条件。

### 阶段 5/8/9 增量：Docker Playwright 浏览器真值链（2026-09-15）

- [x] 新增 `browser_tests[]` manifest 一等测试单元：固定 `entrypoint_id`、spec、owner-facing `base_url`、Docker image、`e2e` tier、安装/外层预算、acceptance/domain 映射和 deterministic 选项；路径、URL、镜像 token、ID 冲突与布尔/预算字段均 fail-closed。
- [x] 新增 `tests/product_testkit/browser.py`：构造遵循 `zf-browser-e2e-contract` 的 Docker 命令（`mcp/playwright:latest`、host network、宿主 UID/GID、只挂载项目/证据目录、`NODE_PATH`、bounded install 可选、容器预置浏览器探针、JSON reporter、输出目录）；不执行宿主机浏览器。
- [x] 新增 Playwright JSON 判定：无测试/跳过为 `not_run`；浏览器断开、runner error、报告损坏或非零但无产品 finding 为 `harness_failure`；Docker/外层超时为 `environment_failure`；DOM/旅程断言 unexpected 为产品 `fail`；flaky 不得伪绿；只有完整测试且无 finding 才 `pass`。
- [x] 浏览器结果接入 executor、CI T0～T3 计划、domain coverage、verification-result、JUnit、failure bundle、stability/FLAKY 判定和 artifact digest index；保留 config、JSON、stdout/stderr、receipt、截图/trace 输出目录引用。
- [x] 新增 `test_browser_manifest_flow.py` 通用参考 Web 产品 fixture：Python HTTP 服务监听 `0.0.0.0`，Playwright 在 Docker 中执行点击前后 DOM、截图和刷新后的状态断言；真实命令联调通过，服务结束后 `stopped=true`，artifact index 含浏览器 JSON 报告。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit`：**229 passed, 2 warnings**；`uv run ruff check tests/product_testkit`：**All checks passed**。警告为既有 Hypothesis 配置和本测试新加的可选 `integration` marker，不改变 verdict。
- [ ] 尚未把 Playwright 浏览器能力接入真实产品的 Product Acceptance journey schema；通用 runner 已独立可用，真实产品只需声明自己的 spec、service、assertions 和 evidence。

### 阶段 9 补充联调：能力准入与严格域覆盖（2026-09-15）

- [x] 新增 `tests/product_testkit/test_capability_admission_flow.py`，使用独立 Git library fixture 验证 `capabilities`、带理由和来源的 `not_applicable`、P-01～P-05/P-17/P-18 域映射、统一 runner 与写盘 `verification-result` 的一致性。
- [x] 通过 `uv run pytest -q --no-cov tests/product_testkit/test_capability_admission_flow.py tests/product_testkit`：**125 passed, 1 warning**（14.30s）。
- [x] 结果证明：API/Web 明确裁决为 `not_applicable` 时不会被当成成功命令；核心域全部有显式命令覆盖；写盘报告保留与内存一致的域裁决。
- [ ] 该联调仍是通用 fixture，不代表任意真实产品的业务验收通过；真实产品仍需提供并审计自己的 manifest、命令和 acceptance evidence。

### 阶段 8 补充修正：分层计划的缺失命令语义（2026-09-15）

- [x] `ProductRun` 记录本次实际计划的 `planned_command_ids`；`verification-result` 只对本次计划判定缺失命令，不再把 T0/T1 未选中的高层命令误判为 `NOT_RUN`。
- [x] 增加 T0 runner 回归断言：只执行 unit 命令时 verdict 为 `pass`，`missing_commands` 为空。
- [x] 联调命令：`uv run pytest -q --no-cov tests/product_testkit`，结果 **125 passed, 1 warning**（11.34s）。
- [ ] 未选中的高层命令仍需在跨层汇总/发布层执行；本修正不把选择性 T0 结果当作全量产品测试通过。

### 阶段 8 补充修正：首个失败与未执行命令的判定优先级（2026-09-15）

- [x] 修正 `verification-result` 判定顺序：已有命令/Setup 明确失败或超时时，优先输出 `FAIL`/`HARNESS_FAILURE`；只有没有失败且计划命令缺失时才输出 `NOT_RUN`。
- [x] 新增“首个命令失败、后续命令未执行”回归测试，确认失败不会被缺失 receipt 掩盖，同时保留后续命令在 `missing_commands` 中用于诊断。
- [x] 联调命令：`uv run pytest -q --no-cov tests/product_testkit`，结果 **126 passed, 1 warning**（10.87s）。

### 联调观察记录：conformance 长时运行（2026-09-15）

- [~] 尝试将 TestKit 与 `test_zaofu_testkit_s6_cli_parser.py`、`test_zaofu_testkit_s10_zap_host.py` 联合运行；进程持续运行超过 4 分钟，进度停在约 28%，且未产生 pytest 最终汇总。
- [x] 按测试基础设施清理规则终止本轮明确启动的 pytest 进程；未将该结果计为通过，也未归因于产品或 ZaoFu 生产代码。
- [ ] 后续应按模块/节点拆分该 conformance 组合，并为可能挂起的测试设置局部超时和独立预算；快速 `tests/product_testkit` 回归仍为 126 passed。

### 联调定位：CLI conformance 收集/执行预算（2026-09-15）

- [~] 单独运行 `tests/conformance/test_zaofu_testkit_s6_cli_parser.py`，在 90 秒预算内仍未产生最终汇总；随后确认收集阶段也超过 30 秒无输出，并终止本轮明确启动的进程。
- [x] 单独运行 `tests/conformance/test_zaofu_testkit_s10_zap_host.py`：`1 deselected`、退出码 5；该文件当前没有可执行用例，不能计为通过。
- [x] 结论：长时问题集中在 CLI conformance 的收集/执行路径，而非 ZAP host 探针；该结果归类为测试基础设施/预算问题，不修改 ZaoFu `src/`，也不登记为产品缺陷。
- [ ] 后续需要按测试节点进一步拆分 CLI 文件，降低参数生成规模或增加局部超时后再纳入门禁。

### 阶段 6/8 补充验证：ZAP host tier（2026-09-15）

- [x] 使用显式 host marker 运行 `tests/conformance/test_zaofu_testkit_s10_zap_host.py`。
- [x] 结果为 **1 failed**：`run_zap` 返回 `harness_failure`，原因是未提供 `ZF_TESTKIT_ZAP_IMAGE` 与 `ZF_TESTKIT_ZAP_APP_IMAGE` 的 digest-pinned 镜像。
- [x] 该失败正确证明 host tier 前置条件缺失；不是产品安全漏洞，也不是 ZaoFu 生产代码缺陷，不写入 `ZaoFu_issue.md`。
- [ ] 配置真实 digest-pinned scanner/target 镜像后，才能执行并判定 ZAP 被动扫描；普通 T0/T1 不应隐式运行该 tier。

### TestKit 静态质量门禁回归（2026-09-15）

- [x] 执行 `uv run ruff check tests/product_testkit`：**All checks passed**。
- [x] 执行 `uv run pytest -q --no-cov tests/product_testkit`：**126 passed, 1 warning**（10.74s）。
- [x] 该结果确认当前通用 manifest、planner、executor、runner、报告、证据索引及跨模块 fixture 没有静态检查错误或回归失败。
- [ ] 该门禁只证明 TestKit 自身确定性支撑层；不替代真实生成产品的能力域测试和发布/安全专项。

### 阶段 8 补充实现：CLI harness failure 结构化输出（2026-09-15）

- [x] `python -m tests.product_testkit` 捕获可预期的 manifest、域准入、目标 commit 和文件系统错误，统一输出 JSON `harness_failure` 并返回码 1。
- [x] 新增非法 manifest 子进程回归测试，确认不会只输出不可解析 traceback。
- [x] 联调 `uv run ruff check tests/product_testkit` 与 `uv run pytest -q --no-cov tests/product_testkit`：**127 passed, 1 warning**（12.55s）。
- [ ] 真实 CI 仍需上传该 JSON、JUnit、verification-result 和 failure bundle，并按门禁策略消费 verdict。

### 阶段 8 CLI 最小可执行入口验收（2026-09-15）

- [x] `uv run python -m tests.product_testkit --help` 成功显示 manifest、project-root、output-dir、run-id 等参数。
- [x] 使用空 manifest 执行入口得到结构化 `{"verdict": "harness_failure", ...}`，退出码为 1；没有不可解析 traceback。
- [x] 合法 fixture 的完整成功路径已由 `test_runner.py` 子进程联调覆盖，并与本阶段 CLI 错误路径保持同一 JSON verdict 协议。

### 仓库影响闭包探针（2026-09-15）

- [x] `git diff --check` 对本轮 TestKit/conformance/实施记录路径未发现空白或补丁格式错误。
- [~] `uv run python scripts/dev-verify.py plan --base dev` 在 20 秒预算内无输出并以超时退出（124）；当前工作树包含大量未提交/未跟踪文件，不能把该探针结果当作验证通过。
- [ ] 后续应在干净或明确限定 pathspec 的工作树中运行影响规划，或为 planner 增加可观测的阶段性输出；本轮不修改 planner 生产代码。

## 当前完成度审计 checkpoint（2026-09-15）

### 已有直接证据

- [x] 通用 manifest、能力裁决、域覆盖、命令执行、目标 commit 校验、receipt、verification-result、JUnit、artifact digest、failure bundle 和 CLI 结构化错误路径均有测试。
- [x] TestKit 静态检查与专属回归：`ruff` 通过，`126/127` 个 TestKit 用例通过（仅有既有 Hypothesis 配置 warning）。
- [x] 独立 Git fixture 已验证 P-01～P-05、P-17、P-18 的统一执行闭环。

### 仅证明支撑层、尚未证明真实产品质量

- [~] API、Web、持久化、外部集成、安全、恢复、并发、性能、发布和观测模块目前主要是通用 oracle/fixture；必须接入真实产品 manifest 与实际服务/依赖后才能判定产品域通过。
- [~] T0～T3 选择和报告协议已实现，但尚未接入仓库 CI job、报告上传和 Candidate 门禁。

### 明确未完成/前置缺失

- [ ] 真实产品 matrix 的 acceptance/domain 绑定、entrypoint/health 声明和严格 admission。
- [ ] Docker Playwright、Testcontainers/Pact、真实恢复/并发、k6、digest-pinned ZAP、安装升级回滚。
- [ ] Product Acceptance → candidate/run contract → merge → post-merge regression 的真实端到端链路。

**审计结论（术语澄清）**：当前完成的是可复用的确定性产品测试支撑层，不是“所有生成产品已完成测试”。这里的“未完成”只指真实产品的逐项目适配/质量认证和发布环境验证，不指通用 TestKit 闭环本身依赖某个真实产品通过。通用闭环应由版本化合同、通用参考产品 fixture、故障 fixture 和跨域联调独立证明；真实产品输入缺失或产品自身失败时，只能使该产品的 verdict 为 `admission_failure`、`harness_failure` 或 `product_failure`，不能阻塞通用闭环建设。

### 轻量回归复核（2026-09-15）

- [x] `git diff --check -- tests/product_testkit Product_steps.md` 无输出（通过）。
- [x] 运行 manifest、planner、runner 核心回归：**42 passed, 1 warning**（1.75s）。
- [x] 未启动长时 conformance 或真实产品服务；本结果仅确认核心准入/执行/报告逻辑未回归。

### 下一阶段执行入口

- [ ] Docker/Web：执行 `docker info`；启动产品 API/UI 于 `0.0.0.0`；使用 `mcp/playwright:latest` + host networking；浏览器不可用必须记录 `ENVIRONMENT_FAILURE`。
- [ ] 数据依赖：用 Testcontainers 启动产品声明的数据库/队列；执行 migration、CRUD、重复写、备份恢复；容器健康、数据约束和清理均需有证据。
- [ ] API 契约：读取产品 OpenAPI；执行 Schemathesis 合法/非法请求；5xx、schema 漂移、越权成功均判失败。
- [ ] 性能安全：在隔离环境执行 k6 thresholds 和 ZAP baseline/active scan；阈值超限或高风险漏洞为失败，工具不可用为 `NOT_RUN`/环境失败。
- [ ] CI 接线：将 `run_manifest_and_collect --ci-tier T0..T3` 接入 job；上传 verification-result、JUnit、acceptance report 和 failure bundle；禁止失败重试伪装绿色。
- [ ] 交付门禁：以真实 run/candidate contract 校验 target commit、artifact digest、Product Acceptance 和 remote policy；未通过不得进入 merge sink。

### 阶段 5 补充验证：Docker 能力探针回归（已完成）

- [x] 增加结构化 Docker probe 测试，验证无论 daemon 是否可用都返回明确的布尔状态和诊断信息。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit/test_docker.py tests/product_testkit/test_tools.py
6 passed, 1 warning
```

### 阶段 8 补充修正：验收报告持久化联动（已完成）

- [x] 修正统一 runner：生成 Product Acceptance Report 后重新持久化 `verification-result.json`。
- [x] 增加回归断言，确认内存结果和磁盘报告都包含验收 verdict。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
101 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 8 补充实现：artifact digest 索引（已完成）

- [x] 在 `tests/product_testkit/artifacts.py` 增加 `build_artifact_index`，统一校验证据文件必须是 run root 下的普通文件，并计算 SHA-256。
- [x] 在统一 runner 中对每条命令的 stdout、stderr 和 receipt 生成 `artifact-index.json`，并将索引嵌入 `verification-result.json`。
- [x] 增加正常索引、digest 长度和路径逃逸负向测试；与全套 TestKit 联调。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
86 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

本阶段只完成本地确定性证据索引；尚未宣称已完成真实产品的 API/Web/容器、性能、安全扫描或交付远端门禁。

### 阶段 8 补充实现：统一命令行入口（已完成）

- [x] 新增 `python -m tests.product_testkit`，接受 `--manifest`、`--project-root`、`--output-dir`、`--run-id`、`--candidate-ref`、`--target-commit` 和 `--ci-tier`。
- [x] CLI 使用与 Python API 相同的严格 manifest、目标 commit、域覆盖、证据和报告逻辑；通过返回码区分 pass（0）与其他 verdict（1）。
- [x] 使用独立临时 Git 产品 fixture 做子进程联调，确认 stdout JSON、verification-result 和 artifact 输出均生成。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
89 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 3/8 补充修正：环境失败分类（已完成）

- [x] `verification-result` 现在保留每条命令的 `environment_error` 字段。
- [x] 命令不存在、工作目录不可用等 harness 环境错误统一判定为 `harness_failure`，不再误报为产品 `fail`。
- [x] 增加环境错误回归测试，并重新联调全部 TestKit。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
90 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 4 补充实现：API 服务生命周期 fixture（已完成）

- [x] 使用临时产品 HTTP 服务验证 `start_service` 的启动与 health gate。
- [x] 通过 `request_json` 和 `assert_api_result` 验证 API 状态码、JSON 结构及业务字段。
- [x] 在 `finally` 中停止进程并断言进程已退出，防止测试遗留服务。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
91 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 4 补充实现：Schemathesis OpenAPI 适配入口（已完成，执行层待接入）

- [x] 新增 `build_schemathesis_command`，校验 HTTP(S) base URL、schema 文件边界和允许的契约检查项。
- [x] 生成可复现的 `schemathesis run` 命令，可作为产品 manifest 的 API contract command 使用。
- [x] 增加非法 URL、缺失 schema 和未知检查项的负向测试。
- [ ] 尚未将该命令自动注入任意产品 manifest，也未把真实 OpenAPI 服务扫描结果接入 acceptance gate；这仍属于后续 T1 能力接入项。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
93 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 8 补充实现：证据索引重放校验（已完成）

- [x] 新增 `verify_artifact_index`，对索引中的每一项执行路径边界、普通文件和 digest 重算校验。
- [x] 增加证据被篡改后的失败测试，确保审计/重放不能接受漂移文件。
- [x] 与统一 runner、报告和 API fixture 联调通过。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
94 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 5 补充实现：Docker/Playwright 环境适配（命令层已完成）

- [x] 新增 Docker daemon 探针 `probe_docker`，区分 executable 缺失、daemon 不可用和可用状态。
- [x] 新增固定 `mcp/playwright:latest`、host network、base URL 和 spec 的命令构造器。
- [x] 增加 URL、spec、镜像参数校验测试。
- [ ] 尚未在本轮启动真实产品 Web 服务并执行容器浏览器断言；真实 Playwright 运行仍需产品项目的 UI fixture 和启动命令。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
96 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 8 补充实现：artifact index 原子写入封装（已完成）

- [x] 将 artifact index 的持久化封装为 `write_artifact_index`，统一目录创建、JSON 编码和原子替换。
- [x] 增加 round-trip 与临时文件清理测试，并重新联调 runner 产物。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
97 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 6 补充实现：可选测试工具能力探针（已完成）

- [x] 新增 `probe_tool`，统一探测 k6、ZAP、Schemathesis 等可选工具是否存在。
- [x] 工具缺失明确返回 `not_run`，不会被当作产品失败；工具存在返回可审计 executable 路径。
- [x] 增加存在、缺失和非法名称测试，并与全部 TestKit 联调。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
100 passed, 1 warning
uv run ruff check tests/product_testkit
All checks passed!
```

### 阶段 8 补充实现：报告中的证据索引引用（已完成）

- [x] `verification-result.json` 增加稳定的 `artifact_index_ref` 字段，指向同一输出目录的 `artifact-index.json`。
- [x] 增加持久化报告回归断言，确保下游 CI 可按引用读取证据索引。

最新复核记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit
101 passed, 1 warning
git diff --check
passed
```

### 阶段 6 补充修正：可选工具名称安全校验（已完成）

- [x] `probe_tool` 拒绝空白、换行和多 token 名称，避免 manifest 工具探测产生命令歧义。
- [x] 增加换行注入负向测试。

验证记录（2026-09-14）：

```text
uv run pytest -q --no-cov tests/product_testkit/test_tools.py
3 passed, 1 warning
```

### 阶段 10：真实生成产品兼容性 canary（真实生成产品样本，非闭环前置）

本阶段与通用 TestKit 的完成判定相互独立：通用 TestKit 由版本化合同、通用参考产品 fixture、故障 fixture 和 P-01～P-18 跨域联调证明；真实生成产品样本 只用于证明一个真实生成产品能否按同一合同接入。它缺少 manifest/acceptance 输入时输出准入或 harness 失败，产品行为有缺陷时输出产品失败，这两类结果都是闭环应当正确产出的终态证据，不阻塞 TestKit 继续构建，也不能被解释为 TestKit 闭环尚不可构建。

- [x] 仓库扫描确认 `awesome-zaofu/generated-product-project` 是当前工作树中可运行的生成产品项目，具备 Python、Node/Vite、FastAPI 和 Playwright 入口。
- [x] 在产品项目自身目录执行 Python 测试：`188 passed, 1 failed`。
- [x] 记录真实失败：`tests/release/compliance/test_runtime_compliance.py::test_runtime_sbom_is_closed_over_frozen_runtime_locks` 检测到 `sbom/runtime/index.json` 中 `uv.lock` 摘要与当前锁文件不一致。该问题属于产品项目问题，按约定不修改产品代码、不写入 ZaoFu issue。
- [x] 执行 `npm run build`：构建成功；Vite 报告 bundle 大小提示，未作为失败。
- [x] 执行 `uv run --project . python scripts/verify-release.py`：`integrity_verdict=passed`，但 `upload_authorized=false`，并报告性能预算尚未冻结；这属于发布资格状态，不改写为通过。
- [x] 执行 Node 单测：22/23 断言通过，1 个失败因测试内部调用 `python` 而环境仅提供 `python3`（`spawnSync python ENOENT`），归类为测试环境/入口兼容问题，不据此判定产品业务失败。
- [ ] 尚未执行真实 FastAPI 长驻服务、Docker Playwright 浏览器断言、Schemathesis、性能、ZAP、崩溃恢复和多版本发布矩阵；这些需要在产品项目启动参数、服务端口和工具条件明确后继续。

验证命令与结果（2026-09-14）：

```text
cd awesome-zaofu/generated-product-project
uv run --project . pytest -q --no-cov       # 188 passed, 1 failed
npm test                                    # 22 passed, 1 failed (python executable missing)
npm run build                               # passed
uv run --project . python scripts/verify-release.py  # integrity passed; upload unauthorized
```

### 阶段 10 补充：真实服务 health smoke

- [x] 已确认产品服务入口为 `python -m generated_product`，静态资源要求 `dist/release-candidates/v1-rc1/runtime/web`。
- [x] 已完成前端构建后尝试在隔离端口启动服务并请求 `/api/health`。
- [ ] 服务未能启动：产品运行环境缺少 `gymnasium`（`ModuleNotFoundError`），因此 health 返回不可用，未继续伪造 API/Web 通过结果。该项归类为产品依赖/环境装配失败，未修改产品代码。

证据：`/tmp/product-canary-4327.log` 中记录了完整启动 traceback；后续需在产品锁定环境补齐依赖后重跑 health、API 和浏览器测试。

补充复核：执行 `uv sync --frozen --group dev` 后，`gymnasium` 已可导入，服务日志显示 Uvicorn 完成启动并监听 `127.0.0.1:4327`。但当前命令环境的 HTTP 代理配置把 localhost 请求转发为 502，且服务进程随后被测试 shell 清理；因此本次只确认“进程启动成功”，不把 health/API 判为通过。后续必须在清除代理（`NO_PROXY=127.0.0.1,localhost`）且保持服务存活的 harness 中重跑。

再次复测（端口 4328）：已清空大小写代理变量并设置 `NO_PROXY=127.0.0.1,localhost`。Uvicorn 仍显示启动完成，但 `/api/health` 连续返回 `Empty reply from server`，未得到 HTTP 200；因此 health/API 仍判定为未通过，需进一步在产品服务代码或其运行时配置中定位请求处理问题。未修改产品代码。

### 阶段 10 补充复测：真实 API/静态资源 smoke（通过）

- [x] 使用独立长驻进程（端口 4329）启动产品服务，并使用 `curl --noproxy '*'` 绕过代理。
- [x] `/api/health` 返回 HTTP 200，响应为 `{"status":"ok","bind_host":"127.0.0.1"}`。
- [x] `/` 返回 HTTP 200，`text/html`，资源非空。
- [x] `/api/presets/normal-v1` 返回 HTTP 200、`application/json`。
- [x] `/api/episodes` 返回 HTTP 200、`application/json`，包含产品 episode 列表和配额字段。
- [x] 请求结束后发送 SIGINT，Uvicorn 完成 graceful shutdown，无残留服务进程。

判定：服务启动、health、静态入口和三个只读 API smoke 全部通过；先前 502/Empty reply 确认为测试命令代理/进程生命周期问题，不是这些端点的产品失败。

### 阶段 11：Docker Playwright 产品 E2E

- [x] Docker daemon 与 `mcp/playwright:latest` 均可用；容器使用 host network 执行产品项目现有 `tests/e2e/assembly`。
- [x] 真实产品 E2E 共 4 条：2 条通过（桌面业务/视觉动态证据、移动端布局），证明页面、WebSocket 会话和画布动态变化可被浏览器验证。
- [x] 2 条失败：策略批量评估在 120 秒内未出现结果表；50 车辆 aggressive 场景在 60 秒内仍为 `running`，未到达 `pass`。按约定作为产品项目测试失败，不修改产品代码、不写 ZaoFu issue。
- [x] 服务进程在测试结束后已停止；Playwright 输出了失败 trace/context 证据。
- [ ] 产品自带 `scripts/run-e2e-docker.sh` 直接执行时仍因脚本硬编码 `python` 且当前环境仅提供 `python3` 退出；本轮用等价的 `uv run` 服务命令完成了 Docker E2E。该项是 harness 兼容性待修，不改产品代码。

验证结果（2026-09-14）：`2 passed, 2 failed`，运行约 3.6 分钟。

### 阶段 12：产品发布构建产物

- [x] `uv run python scripts/assemble-release.py` 成功重建 release candidate Web 资源；仅有 bundle 大小提示。
- [x] `uv run python scripts/verify-release.py` 返回 `integrity_verdict=passed`；同时保留 `upload_authorized=false` 与“性能预算未冻结”资格限制，未把资格限制误判为发布通过。
- [x] `uv build` 成功生成 `dist/generated_product-0.1.0.tar.gz` 和 `dist/generated_product-0.1.0-py3-none-any.whl`。
- [ ] 尚未完成干净虚拟环境安装、升级/回滚、多 Python 版本矩阵；这些属于发布层后续测试。

### 阶段 12 补充：干净环境 wheel 安装

- [x] 在 `/tmp` 新建 Python 3.11 虚拟环境，安装生成的 wheel 及其声明依赖（46 个包）。
- [x] 安装后成功导入 `generated_product`，并成功执行 `python -m generated_product --help`。
- [x] 该验证未修改产品仓库环境；临时环境位于 `/tmp/product-canary-release-full-skqvCO`。
- [ ] 尚未执行升级/回滚与多 Python 版本安装矩阵。

### 阶段 13：Python 3.12 发布兼容性

- [x] 当前环境发现 Python 3.11 和 3.12 两个可用解释器。
- [x] 在独立 `/tmp` Python 3.12 虚拟环境安装 wheel 及全部声明依赖成功（46 个包）。
- [x] Python 3.12 成功导入产品包并执行 `python -m generated_product --help`。
- [ ] Python 3.13 不在当前环境，未执行；升级/回滚仍未执行。

### 阶段 14：k6 性能 smoke

- [x] 检测到 Docker 镜像 `grafana/k6:1.5.0`，使用 host network 对产品 `/api/health` 执行 2 VU、3 秒稳定性 smoke。
- [x] 4,522 个 HTTP 请求全部成功，`http_req_failed=0.00%`，业务检查（HTTP 200、JSON status=ok）成功率 100%。
- [x] p95 延迟约 1.88ms；测试进程和产品服务均已清理。
- [ ] 这不是正式性能门禁：产品尚未声明吞吐/延迟预算，尚未执行压力、突发和长时间 soak。

### 阶段 15：产品 API 与安全单元集

- [x] 独立执行 `uv run pytest -q --no-cov tests/api`：27 passed。
- [x] 覆盖 API 静态资源、策略比较/评估和 loopback security boundary；未发现失败断言。
- [x] 测试结束无服务进程或临时状态残留。
- [ ] 动态 ZAP 扫描未执行：当前没有可用 ZAP 镜像/命令，且正式安全预算与扫描范围尚未在产品 manifest 中冻结。

### 阶段 16：前端 TypeScript 类型门禁

- [x] 执行产品项目 `npm run typecheck`（`tsc --noEmit`），退出码 0。
- [x] 与此前 `npm run build`、Docker Playwright 结果联调：类型检查和生产构建通过；浏览器业务场景仍保留 2 条独立失败，不被类型门禁掩盖。

### 阶段 17：装配与发布合规子集复测

- [x] 执行 `uv run pytest -q --no-cov tests/assembly tests/release`：8 passed、1 failed。
- [x] 装配入口和其他发布合规断言通过。
- [x] 唯一失败仍为 SBOM 中 `uv.lock` SHA256 与当前锁文件不一致；该问题属于产品项目发布合规缺陷，未修改产品代码。

### 阶段 18：策略与会话核心逻辑

- [x] 执行 `uv run pytest -q --no-cov tests/strategies tests/session`：41 passed。
- [x] 策略契约、比较/评估逻辑以及会话协议、恢复、回压和证据相关测试全部通过。

### 阶段 19：仿真、配置、库与训练资产

- [x] 执行 `uv run pytest -q --no-cov tests/simulation tests/config tests/library tests/training`：95 passed。
- [x] 仿真环境、配置解析、Episode 库/回放、训练模型契约与训练资产测试全部通过。

### 阶段性联调复核：TestKit 支撑层（2026-09-14）

- [x] 在当前工作树执行 `uv run pytest -q --no-cov tests/product_testkit`：**101 passed**，仅有 Hypothesis pytest 插件关于 `.hypothesis` 目录的非阻塞 warning。
- [x] 本次结果覆盖 manifest、矩阵闭包、域规划/筛选、隔离执行、CLI/API/Web/持久化/集成/安全/恢复/并发/性能/发布/观测适配器、verification-result、Product Acceptance、JUnit、failure bundle、CI tier 计划和最小独立 Git 产品 fixture。
- [x] 本次复核未执行真实产品的完整 P-01～P-18，也未将最小 fixture 的通过结果冒充任意产品项目质量证明。
- [ ] 下一步仍需真实产品 manifest/candidate/run contract、适用域命令映射、CI job 上传/门禁，以及发布、ZAP、Testcontainers、故障恢复和 Issue→merge 闭环环境。

### 阶段性联调复核：ZaoFu 全量 TestKit（2026-09-14）

- [x] 执行 `uv run python -m tests.testkit --stage all --output /tmp/zf-product-testkit-all-JsM6Qd --timeout 1500`。
- [x] 运行期间 pytest 输出推进至约 74%，进程句柄持续有效；最终在 1500 秒达到超时，退出码为 `-15`，TestKit 总 verdict 为 `harness_failure`。
- [x] 结果报告、JUnit 路径、pytest stdout/stderr 和未执行用例清单均写入 `/tmp/zf-product-testkit-all-JsM6Qd`；报告显示已执行用例通过，但仍有大量后续用例 `not_run`，因此不能将本次运行视为全量通过。
- [x] 该结果证明全量入口具备超时和证据保留能力；同时暴露出当前全量回归超过 1500 秒预算，需后续按阶段/模块分片并行或单独延长发布层预算。
- [ ] 未据此登记 ZaoFu 生产缺陷：目前证据只表明全量测试超时，尚不能归因于 ZaoFu 业务代码；后续若最小复现确认生产代码错误，再按约定登记 `ZaoFu_issue.md`。

### 阶段 1 补充：通用 Manifest 模板（2026-09-14）

- [x] 新增 `product-test-manifest.example.json`，提供与 `product-test-manifest.v1` 对齐的最小可复制模板。
- [x] 模板明确 `domain_ids`、`acceptance_ids`、命令 tier、deterministic、超时和 `not_applicable` 理由/来源，避免产品项目通过省略能力声明而静默跳过测试。
- [x] 模板只作为接入起点，不包含任何具体产品业务断言；接入项目必须替换占位命令、commit 和验收条件后再运行严格 runner。

### 阶段性复核：TestKit 支撑层回归（2026-09-14，再次执行）

- [x] 执行 `uv run pytest -q --no-cov tests/product_testkit`：**101 passed**，仅有 Hypothesis 配置 warning。
- [x] 结果确认通用 TestKit 支撑层当前可重复通过；该结果不等同于任意真实产品项目的端到端通过。

### 阶段 1/2 联调：真实产品 manifest admission 探针（2026-09-14）

- [x] 根据 `generated-product-project` 当前 `pyproject.toml`、`package.json`、README 和脚本，在 `/tmp` 生成临时 manifest；未修改产品源码。
- [x] Manifest 基础 schema 校验通过，并正确绑定产品当前 Git commit。
- [x] 首次执行 TestKit T0 被严格拒绝：`P-02、P-07、P-11、P-16` 没有命令映射。该结果证明 runner 会阻止未覆盖域静默通过。
- [ ] 尚未补齐这些域的真实命令或有依据的 `not_applicable` 声明；因此没有伪造 T0 通过结果。

- [x] 补齐临时 manifest 的环境/API 命令后再次运行 T0；TestKit 返回 `not_run`，setup `uv sync --extra dev` 以退出码 2 失败。
- [x] 失败证据显示产品自身 `pyproject.toml` 没有定义 `dev` optional extra（并伴随虚拟环境路径 warning）；该结果分类为产品项目环境装配失败，不归因于 ZaoFu，也未修改产品代码。
- [x] 将临时 setup 修正为真实存在的 `uv sync` 后重跑 T0：setup 与 `python -m pytest tests/registry -q` 通过；`npm test` 为产品测试失败（22 tests 中 1 failed）。
- [x] 失败用例为 Web HMI 的 API boundary 测试调用 `python -m pytest tests/api -q`，当前产品运行环境未能满足该嵌套命令；已保留 stdout/stderr、receipt 和 JUnit，未将其归因于 ZaoFu。
- [x] 复核执行 `uv run pytest tests/api -q`：**27 passed**。因此该 T0 失败可进一步分类为产品 Web 测试脚本的解释器/harness 兼容问题，而非 API 产品逻辑失败；TestKit 仍按原始 `npm test` 非零退出保持失败，不伪造绿色。
- [x] 独立执行产品 `npm run typecheck`：退出码 0。
- [x] 独立执行产品 `npm run build`：退出码 0；Vite 仅报告 chunk 大小 warning，不构成失败。
- [x] 检查 Docker：ServerVersion `29.7.1` 可用；执行产品 `GENERATED_PRODUCT_E2E_PORT=4329 bash scripts/run-e2e-docker.sh`。
- [x] Docker E2E 在服务启动阶段失败：`scripts/start-demo.sh` 硬编码调用 `python`，当前产品环境仅有 `python3`/uv，health 返回 502；已保留 `evidence/release/e2e/docker-server.*.log`。该项分类为产品 E2E harness/启动脚本问题，不修改产品代码。
- [x] 使用临时 PATH shim 提供 `python -> python3` 后重试 Docker E2E；脚本继续进入服务启动，但因运行环境缺少 `gymnasium`（`ModuleNotFoundError`）退出，health 仍为 502。
- [x] 该结果将解释器兼容问题与依赖装配问题分离；归类为产品环境/依赖失败，未继续伪造 Playwright 结果，也未修改产品代码。
- [x] 用产品 `.venv/bin/python3` 直接导入检查 `gymnasium==1.3.0` 成功，但 Docker E2E 启动脚本仍报告 `ModuleNotFoundError`；说明脚本启动时未继承产品 uv 环境，属于可复现的产品启动 harness 环境边界问题，仍未修改产品代码。
- [x] 使用 `PYTHONPATH=src uv run --project ... python -m generated_product --host 127.0.0.1 --port 4332` 直接启动服务成功；请求 `/api/health` 返回 HTTP 502，随后已正常停止服务。
- [x] 该证据表明服务进程可启动但 health 依赖未就绪，暂归类为产品运行依赖/健康检查失败；未将其归因于 ZaoFu。
- [x] 复核 `/api/health` 原始响应：稳定返回 HTTP 502 且响应体为空；服务进程无启动日志错误并已清理，进一步确认产品健康检查契约未满足。

## 阶段 1 增量记录（2026-09-14）

- [x] 收紧 `product-test-manifest.v1` 的 `not_applicable` 准入：每项不适用声明除非空 `reason` 外，必须提供非空 `source`，使裁决可追溯；缺失来源 fail-closed。
- [x] 新增缺失来源的负向测试，并修正通用 Git fixture 的声明；针对 manifest/runner 执行 `22 passed`，ruff 检查通过。
- [x] 联调全套 `tests/product_testkit`：**102 passed**（1 条 Hypothesis 配置 warning）；本次变更未修改 ZaoFu `src/` 生产代码。
- [x] 增加严格 runner 的能力裁决：manifest 中显式声明为 `false` 的已知能力必须同时提供带 `reason`/`source` 的 `not_applicable` 决策，否则在执行产品命令前 fail-closed。
- [x] 新增 planner 负向/正向测试并完成联调；`uv run pytest -q --no-cov tests/product_testkit`：**103 passed**，ruff 检查通过。
- [x] 扩展 `verification-result.v1`：增加 `domain_coverage.selected/executed/missing`，把能力域计划与实际收到 receipt 的命令映射持久化，避免报告只能显示命令结果而无法审计域覆盖。
- [x] 联调报告、runner 与基础 fixture；`uv run pytest -q --no-cov tests/product_testkit`：**103 passed**，ruff 检查通过。
- [x] 增加报告层域覆盖断言：验证已选域、已执行域和缺失域的集合计算不会因命令 receipt 缺失而静默通过；focused report/runner 测试 **10 passed**。
- [x] 补强 P-07 API 探针：不可达端点返回结构化 harness 错误，响应缺少必需 JSON 字段时断言 fail-closed；API fixture focused tests **5 passed**，ruff 检查通过。

## 阶段 1/2/8 联调：TestKit 基础回归复核（2026-09-15）

- [x] 重新执行 `uv run pytest -q --no-cov tests/product_testkit`：**106 passed**，仅有 Hypothesis 配置 warning。
- [x] 确认 manifest 准入、能力裁决、命令执行、API 探针、报告域覆盖、artifact/failure bundle 与 CI tier 选择在同一套测试中联调通过。
- [ ] 该结果仍只证明 TestKit 支撑层和通用 fixture 逻辑；真实产品项目的 Web、数据库/队列、外部服务、崩溃恢复、性能、ZAP、发布矩阵及完整 Issue→merge 闭环仍按“Need to do”执行，不能据此宣称产品级端到端闭环完成。

## 阶段 1/2 增量：产品 source_root 边界（2026-09-15）

- [x] 在产品命令执行前校验 manifest 的 `source_root`：必须存在、必须为目录且不得解析到 project root 之外；越界或缺失均以 harness failure fail-closed。
- [x] 增加存在、缺失和 `../` 越界测试；执行 `uv run pytest -q --no-cov tests/product_testkit/test_executor.py tests/product_testkit/test_runner.py`：**12 passed**。
- [x] 与全套 TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit`：**107 passed**，仅有 Hypothesis 配置 warning。

## 阶段 8 增量：cleanup 证据与失败分类（2026-09-15）

- [x] 统一 runner 现在会在产品测试命令结束后执行 manifest 声明的 cleanup 命令，并为每条清理命令生成独立 receipt。
- [x] 清理命令失败会将最终结果降级为 `harness_failure`，不会把测试主体的绿色结果伪装成完整通过。
- [x] 增加 cleanup 执行、receipt 和实际清理副作用测试；TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit`：**108 passed**。
- [x] 补齐 cleanup 失败证据一致性：failure bundle/JUnit 在最终清理判定后生成，bundle 保留 `cleanup_failures`，JUnit 包含 cleanup case；成功/失败清理参数化回归通过。
- [x] 全套 TestKit 联调 **109 passed**，修改文件的 ruff 检查通过；本次仅修改 TestKit 支撑层，不修改 ZaoFu 生产代码。

## 阶段 1 增量：未知产品能力 fail-closed（2026-09-15）

- [x] 审查 manifest planner 时发现，`capabilities` 中未被 TestKit 识别的键会被静默忽略，可能导致产品项目声明新能力却没有进入对应测试域；该问题属于 TestKit 准入逻辑，不归因于 ZaoFu 生产运行代码。
- [x] 为项目形态元数据（`library`、`worker`、`backend`、`frontend`、`fullstack`、`other`）保留兼容性，同时对未知可执行能力 fail-closed；新增 `mobile` 等未知能力的负向回归测试。
- [x] 联调 `uv run pytest -q --no-cov tests/product_testkit`：**110 passed**（仅有既有 Hypothesis 配置 warning）；`uv run ruff check tests/product_testkit/planner.py tests/product_testkit/test_planner.py` 通过。
- [x] 未修改 ZaoFu `src/` 生产代码，未新增 ZaoFu issue；该阶段只增强通用产品 TestKit 的准入边界。

## 阶段 8 增量：verification verdict 优先级（2026-09-15）

- [x] 复核统一报告判定发现：同一轮同时出现 target commit 漂移与产品命令失败时，原逻辑可能把 harness 问题降级成产品 `fail`；命令超时也应归为测试环境/执行 harness 失败，而不是产品断言失败。
- [x] 调整 `build_verification_result` 判定顺序：缺失执行仍为 `not_run`；commit 漂移、环境错误或超时优先归类 `harness_failure`；仅在执行环境可信且命令断言失败时归类产品 `fail`。
- [x] 新增 commit 漂移+命令失败、超时 receipt 的回归测试，避免错误归因。
- [x] 与此前 manifest、planner、executor、runner、报告和清理逻辑联调：`uv run pytest -q --no-cov tests/product_testkit`：**112 passed**；Ruff 检查通过。
- [x] 未修改 ZaoFu `src/` 生产代码，未新增 ZaoFu issue。

## 阶段 9：通用 TestKit 当前工作树完成审计（2026-09-15）

- [x] 在当前工作树执行 `uv run pytest -q --no-cov tests/product_testkit`：**127 passed**，仅有既有 Hypothesis 配置 warning。
- [x] 执行 `uv run ruff check tests/product_testkit`：**All checks passed**。
- [x] 执行 `git diff --check`（限定 TestKit 与本步骤记录）：通过。
- [x] 复核结果：通用 TestKit 的 manifest 准入、能力矩阵、命令执行、证据/JUnit/failure bundle、Product Acceptance verdict、清理和分层入口均有可执行回归。
- [x] 修正阶段判据：真实生成产品是否通过、是否补齐 manifest 均不阻塞通用闭环建设；阶段 9 继续由合成参考产品覆盖成功、产品失败、准入失败、环境失败和 harness 失败。

### 阶段 9 增量：现有生成项目矩阵只读准入（2026-09-15）

- [x] 审查 `awesome-zaofu/generated-product-project/planning/generated-product-v1/generated/test_matrix.json`，共发现 **19** 项准入问题。
- [x] 适配器明确报告：`commands[0..1]` 缺少 `acceptance_ids`，`commands[0..16]` 缺少 `domain_ids`；未猜测或补写产品语义。
- [x] 该结果证明 fail-closed 准入有效：当前矩阵不能直接生成可信的通用产品测试计划。
- [ ] 待产品项目补齐矩阵绑定后，重新执行 manifest 生成、T0/T1 测试和 Product Acceptance canary；该项不阻塞通用闭环完成。

## 真实产品 canary 后续清单（不作为通用闭环前置）

- [ ] 由产品项目补齐 `test_matrix.json` 每条命令的 `acceptance_ids` 与 `domain_ids`，并通过 `audit_test_matrix` 的零问题检查。
- [ ] 为真实产品生成 `product-test-manifest.v1`，声明 `source_root`、不可变 `target_commit`、entrypoints、health check、setup 和 cleanup。
- [ ] 修正产品 Web E2E 的跨语言命令入口（当前依赖 `python` 名称），然后在 Docker Playwright 环境重跑 `npm run test:e2e`。
- [ ] 重新生成或绑定产品 SBOM 的锁文件摘要，使发布合规测试对当前 commit 可复现。
- [ ] 在产品服务可启动后执行数据库/外部依赖/恢复/并发等适用域，并将每个域的 receipt、日志和 evidence 纳入统一 runner。
- [ ] 完成真实 Product Acceptance、candidate/commit 绑定和 Issue→Fix Run→测试→merge 后回归；真实 canary 结果单独报告，不决定阶段 9 的通用闭环完成状态。

## 阶段 8/9 联调回归：通用 TestKit 基础闭环复核（2026-09-15）

- [x] 后续在当前工作树重新执行 `uv run pytest -q --no-cov tests/product_testkit`，结果为 **124 passed**；仅有既有 Hypothesis 配置 warning。该结果覆盖当前目录下全部 TestKit 测试，更新此前 119/113 passed 的历史记录。

- [x] 在当前工作树重新执行 `uv run pytest -q --no-cov tests/product_testkit`，结果为 **119 passed**；仅保留既有 Hypothesis 配置 warning。
- [x] 执行 `uv run ruff check tests/product_testkit`，结果为 **All checks passed**。
- [x] 复核 manifest 准入、能力域规划、source root/target commit 身份、命令执行、cleanup、artifact index、JUnit、failure bundle、Product Acceptance 总 verdict 和 CI tier 选择均能在同一套回归中通过。
- [x] 当前证据仍只覆盖 TestKit 支撑层及通用最小 fixture；真实产品的服务依赖、浏览器、数据库迁移、外部协议、崩溃恢复、性能/安全扫描和发布矩阵必须在具备对应产品 manifest 与环境后执行。
- [x] 未修改 ZaoFu `src/` 生产代码，未发现需要新增到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

## 阶段 1 增量：产品 test-matrix 到 manifest 严格适配（2026-09-15）

- [x] 新增 `tests/product_testkit/matrix_adapter.py`，将 `test-matrix.v1` 的明确字段转换为 `product-test-manifest.v1`。
- [x] 仅映射已定义 tier（`static→unit`、`runtime→integration` 等）；未知 tier、缺少 `acceptance_ids` 或 `domain_ids` 时 fail-closed，不猜测产品语义。
- [x] 新增适配器正向、缺失绑定和未知 tier 负向测试，并导出公共入口。
- [ ] 仍需产品适配阶段补齐真实 `domain_ids`、能力声明、entrypoints、cleanup 和 target commit 后，才能执行真实项目闭环。
- [x] 对现有 `generated-product-project/planning/generated-product-v1/generated/test_matrix.json` 做只读准入试跑；适配器在第一个命令因缺少 `acceptance_ids` 明确拒绝（`ManifestError`），证明该产品矩阵尚不能被静默当作完整测试计划。
- [x] 增加 `audit_test_matrix` 结构诊断，一次返回全部缺失绑定和不可映射 tier；适配执行仍对任一问题整体 fail-closed，避免诊断增强导致不安全放行。

## 阶段 1 增量：target_commit 不可变标识准入（2026-09-15）

- [x] 收紧产品 manifest 的 `target_commit` 校验：必须是 40 或 64 位十六进制 Git SHA，兼容 SHA-1/SHA-256 仓库并拒绝分支名、缩写或任意字符串进入产品测试闭环。
- [x] 保留执行阶段的实际 `git rev-parse HEAD` 比对；manifest 准入与运行时身份均 fail-closed。
- [x] 新增分支名、短 SHA、非十六进制和错误长度的负向测试，并修正执行器测试夹具为合法 SHA。
- [x] 全套 TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit`：**117 passed**；Ruff 检查通过。
- [x] 未修改 ZaoFu `src/` 生产代码，未新增 ZaoFu issue。

### 阶段 1 增量修正：Git SHA-256 兼容性（2026-09-15）

- [x] 复核发现仅允许 40 位会误拒绝合法 SHA-256 Git 仓库；现改为接受 40/64 位十六进制标识，并新增 64 位正向回归测试。
- [x] TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit`：**119 passed**；相关 Ruff 检查通过。

## 阶段 8 增量：Product Acceptance 约束总 verdict（2026-09-15）

- [x] 发现统一 runner 在命令全部成功但 mandatory journey 被拒绝/阻塞时，可能仍保留 `pass`；这会把产品验收失败错误报告为通过。
- [x] runner 现在将非 `passed` 的 Product Acceptance verdict 降级为产品 `fail`（已有 harness failure 不被覆盖），并在附加验收结果后重新生成 JUnit、failure bundle 和 verification-result，保证三类报告一致。
- [x] 新增绿色命令+失败验收的端到端回归测试，验证持久化报告也为 `fail`。
- [x] 与既有 TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit`：**113 passed**；Ruff 检查通过。
- [x] 未修改 ZaoFu `src/` 生产代码，未新增 ZaoFu issue。

## 阶段 8/9 架构修正：闭环不依赖真实产品通过（2026-09-15）

- [x] 明确区分“通用 TestKit 闭环”和“真实产品 canary”：前者必须由可控参考产品自证，后者只验证兼容性；真实产品存在缺陷、契约缺失或测试失败时，应产生终态失败证据，不能成为 TestKit 建设的阻塞条件。
- [x] 新增程序化 `run_product_closure` 入口；底层 `run_manifest_and_collect` 保留严格抛错语义，闭环边界把 manifest 准入、产品执行、Product Acceptance 和 evidence 写入阶段的可预期错误转为结构化 `harness_failure`。
- [x] manifest 准入失败现在仍会落盘 `verification-result.json`、`junit.xml` 和 `failure-bundle.json`，报告包含 `failure_phase=admission`、异常类型和根因，且不会执行产品命令。
- [x] 修正零命令失败的 JUnit 语义：准入失败会生成 `testkit.admission` error，而不是 `tests=0, failures=0, errors=0` 的伪绿色报告。
- [x] evidence 输出目录不可用时，闭环保留标准输出/返回值这个最后终态通道，返回非零状态且不打印 traceback；同时记录 evidence persistence 失败，不把无法落盘伪装成完整证据。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **129 passed, 1 warning**；`uv run ruff check tests/product_testkit` 和限定 `git diff --check` 均通过。
- [x] `ENVIRONMENT_FAILURE` 已接入统一报告和 JUnit：测试工具缺失或命令超时不再误报产品失败或 TestKit 内部失败；commit/准入/evidence 管道错误仍保持 `HARNESS_FAILURE`。
- [x] 新增 P-18 `product-delivery-readiness.v1` 机械 oracle：检查 verification schema/verdict、run/candidate/commit、mandatory 命令、域覆盖、cleanup、artifact 实体 digest，以及 Product Acceptance 身份和 journey/assertion evidence 索引绑定。
- [x] 绿色测试只产生 `allowed=true` 的交付就绪结论，同时固定 `merge_authorized=false`；测试证据永远不能替代审批意图或 ZaoFu ControlledAction 授权。
- [x] 验收 evidence 缺失时统一 runner 输出 `failure_phase=delivery_readiness`、`DeliveryReadinessError`、失败 bundle 和非绿色 JUnit；相同 evidence 被 journey/assertion 重用时原因稳定去重。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **150 passed, 1 warning**；`uv run ruff check tests/product_testkit` 和限定 `git diff --check` 均通过。
- [x] `NOT_RUN` 已成为统一闭环的真实可达结果：请求 T0 而合法 manifest 仅声明高层命令时不执行产品命令、不误报 harness/product failure，报告保留 selection 原因，JUnit 使用 skipped 表达。
- [x] 最新阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **151 passed, 1 warning**；`uv run ruff check tests/product_testkit` 和限定 `git diff --check` 均通过。
- [ ] 下一步继续用通用参考产品覆盖恢复/并发、安全、性能、发布以及 Issue→merge 后回归，不等待 真实生成产品样本 修复或补齐契约。

## 阶段 2/4/8/9 增量：通用 entrypoint、health 与 cleanup 生命周期（2026-09-15）

- [x] 以现有 `product_acceptance_spec.v1` 为仓库权威，统一 manifest entrypoint 核心字段为 `entrypoint_id`、`start_command`、`health_check`、`owner`，并增加 TestKit 机械执行必需的正有限 `timeout_seconds`；不再把 entrypoint 当作只有 `id` 的空壳。
- [x] manifest 对缺字段、空字段、非法/重复 ID、零/负/NaN/Infinity timeout 全部 fail-closed；真实产品只提供声明值，不参与决定 TestKit 是否能形成闭环。
- [x] 新增通用 shell-free 健康命令生命周期：`setup → start → repeated health probe → product commands → process-group stop → cleanup`。健康探针不局限 HTTP，可适配 API、Web、worker、CLI/file-ready 等产品形态。
- [x] 为 entrypoint 保存 service stdout/stderr、health stdout/stderr、命令 digest、attempt 次数、时间、退出与停止状态和 `entrypoint.<id>.receipt.json`；全部文件进入统一 artifact index 并受 digest 绑定。
- [x] entrypoint executable 缺失、health 超时和启动前退出输出结构化 `environment_failure`；证据路径冲突输出 `harness_failure`，不再错误降级为 `not_run`。
- [x] 健康后自行退出会记录 `exited_before_stop=true` 并拒绝通过，避免把“服务已经死亡”误当成“TestKit 成功清理”。
- [x] cleanup 非零退出在 `verification-result`、failure bundle 和 JUnit 中一致归为 TestKit cleanup error；不再由 JUnit 错标为产品测试 failure。
- [x] entrypoint 证据冲突等发生在 setup 之后的 harness error 不再直接越过 runner；统一结果保留根因并继续执行顶层 cleanup，防止 TestKit 自身失败遗留产品资源。
- [x] 使用真实临时 Git 产品 fixture 联调 setup、服务启动、health、产品命令、停止、cleanup、JUnit、failure bundle、verification-result 和 artifact index；同时覆盖缺失 executable 与证据冲突两个终态失败分支。
- [x] 阶段回归：`uv run pytest -q --no-cov tests/product_testkit` 为 **168 passed, 1 warning**；`uv run ruff check tests/product_testkit` 为 **All checks passed**。
- [x] 本阶段只修改 `tests/product_testkit` 支撑代码和本方案文档，未修改 ZaoFu `src/` 生产代码；未发现需要登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。
- [ ] 下一步以同一通用 fixture 接入真实 HTTP/OpenAPI 命令和浏览器路径，并继续完成恢复/并发、安全、性能、发布、ZaoFu candidate/run contract 与 Issue→merge 后回归。

## 阶段 7/8/9 增量：隔离重跑与 FLAKY 机械裁决（2026-09-15）

- [x] 新增 `product-stability-report.v1` oracle；只有相同 `run_id`、candidate、`target_commit` 和完整 command ID/digest 计划的结果才允许比较，commit 或命令漂移 fail-closed。
- [x] `verification-result.v1` 现在同时保留每条已执行 receipt 的 `command_digest` 和本次完整 `command_plan`；首命令失败导致后续 receipt 缺失时，后续项以 `not_run` 参与稳定性诊断，不会让相同计划被误判为不可比较。
- [x] fail→pass 或 pass→fail 的产品命令进入 `flaky_commands`，总 verdict 固定为 `flaky`；重跑通过不能覆盖第一次失败。
- [x] runner 将每个 prior result 复制为 `stability-attempt.NNN.json`，生成 `stability-report.json`，并把二者纳入 artifact index 和 digest 绑定；failure bundle 同步保存稳定性结论。
- [x] JUnit 为 flaky 增加 `testkit.stability` failure；当前产品命令即使退出码为 0，CI 仍得到红色且 CLI 返回码为 1。
- [x] `python -m tests.product_testkit` 新增可重复 `--prior-result`；CLI 和 Python API 使用同一个 stability oracle，不另造判定逻辑。
- [x] 使用真实临时 Git 产品 fixture 受控制造首跑失败、二跑成功，验证同一 commit/命令计划下不会绿化，并保存两次独立 output/evidence。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **174 passed, 1 warning**；`uv run ruff check tests/product_testkit` 为 **All checks passed**。
- [x] 本阶段未修改 ZaoFu `src/` 生产代码，未发现需要登记的 ZaoFu 生产缺陷。
- [ ] 下一步完成真实 HTTP/OpenAPI runner 路径，以及 P-12/P-13 的独立进程崩溃、恢复、并发和副作用等价联调。

## 阶段 4/8/9 增量：真实 HTTP/OpenAPI 契约闭环（2026-09-15）

- [x] 修改前完成官方一手资料与本地 4.26.1 CLI/报告行为调研，结果落盘 `product_openapi_runner_research.md`；确认退出码不能单独裁决、JSON `complete`/operation/phase/failure/error/warning 必须联合判定，`stateful` 不得默认开启。
- [x] `product-test-manifest.v1` 新增显式 `api_contracts[]` 准入合同，固定 entrypoint、schema、base URL、Schemathesis 精确版本、tier、acceptance/domain、phase、mode、checks、worker、seed、用例/内部时间/请求/外层时间预算和重试策略。
- [x] API contract ID 与 setup/普通 command 共用唯一命名空间；未知 entrypoint/acceptance、越界 schema、非 HTTP URL、未知 check/phase、危险默认 stateful、非单 worker、非法 seed/timeout/retry 均在执行前 fail-closed。
- [x] 新增真实 `run_schemathesis_contract`：先保存工具版本 probe，再执行固定报告路径的 Schemathesis；解析原始 JSON 并区分 `pass`、产品 `fail`、`environment_failure`、`harness_failure` 和 `not_run`。
- [x] 只有外层进程完整、版本一致、JSON 完整、selected/tested operation 大于零、请求 phase 成功、退出码对账、无 finding/error/配置 warning时才通过；网络错误归环境失败，版本/报告/配置矛盾归 harness failure，零 operation 保持 `not_run`。
- [x] 使用通用临时 HTTP 产品完成两条真实协议联调：合法响应得到 `PASS`；产品错误接受非法 query 输入时由 `coverage + negative_data_rejection` 得到真实 product finding，而不是用合成退出码冒充。
- [x] OpenAPI contract 已进入 planner、P-07/P-11 domain coverage、T0～T3 CI 计划、`verification-result`、聚合 JUnit、failure bundle、跨尝试 FLAKY 判定和 artifact index；raw JSON/JUnit、版本/执行 stdout/stderr 与 receipt 均绑定 digest。
- [x] 修正通用 manifest 示例中 `not_applicable` 错用 P-domain 作为 key、基础命令漏映射 P-02 的问题，并增加“只替换占位值后可严格准入”的执行式回归测试。
- [x] 全套联调前发现 10 秒外层预算在资源竞争下会把 Schemathesis 正确归为环境超时；依据原始 receipt 将通用 fixture 外层预算调整为 30 秒，未放宽判据或重试伪绿。
- [x] 最终联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **214 passed, 1 warning**；`uv run ruff check tests/product_testkit` 为 **All checks passed**。唯一 warning 是仓库既有 Hypothesis `.hypothesis` 收集配置提示。
- [x] 本模块只修改 `tests/product_testkit/` 与测试方案/步骤/调研文档，未修改 ZaoFu `src/` 生产代码；未发现需写入 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。
- [ ] 下一步使用同一“通用 fixture 自证、真实项目独立 canary”原则实现 P-08 Docker Playwright 浏览器真值链；其后继续 P-09/P-10 真实依赖与 P-12/P-13 故障/并发闭环。

## 阶段 6/8/9 增量：发布产物准入与证据联调（2026-09-15）

- [x] 新增 `ProductManifest.release_artifacts` 和 `release_artifacts[]` 准入合同：每个产物必须声明稳定 ID、产品根目录内相对路径、`wheel/sdist/container/npm/binary` 类型、`release` tier、acceptance/domain 绑定、mandatory 和 deterministic；越界路径、扩展名类型、悬空 acceptance、重复 ID 和错误域均 fail-closed。
- [x] 新增 `run_release_artifact_checks`：在产品命令完成后检查 regular file、非空和类型，复制到独立 evidence root 后重新计算 SHA-256，源/副本 digest 不一致时拒绝；每个产物写独立 receipt。
- [x] 将 release artifact 结果接入 ProductRun、统一 verification-result、域覆盖、CI T3 计划、JUnit、failure bundle 和 artifact index；mandatory 产物缺失/无效时 verdict 为产品 `fail`，不能被 build 命令的绿色结果覆盖。
- [x] 新增 manifest、直接 oracle 和统一 runner 成功/缺失产物回归；发布产物证据只证明构建输出完整，不授予 merge/push 权限。
- [x] 本阶段 focused 回归：`uv run pytest -q --no-cov tests/product_testkit/test_manifest.py tests/product_testkit/test_release.py tests/product_testkit/test_runner.py`：**119 passed**（1 条既有 Hypothesis 配置 warning）。
- [ ] 尚未接入真实 wheel/npm/container 安装、升级、回滚和多运行时矩阵；这些仍需产品声明 release 命令并在 T3 环境执行，不能由 dummy artifact smoke 推断发布可用性。

## 阶段 6/8/9 增量：动态安全报告准入与统一门禁联调（2026-09-15）

- [x] 新增 `ProductManifest.security_scans` 和 `security_scans[]` 准入合同：固定 `tool: zap`、项目根目录内相对报告路径、扫描命令、`max_risk`、`security` tier、P-11/acceptance 绑定、有限超时及 mandatory/deterministic；未知工具、路径越界、风险值非法、域/验收悬空均 fail-closed。
- [x] 新增 `run_zap_contract`：逐条执行扫描命令，要求报告为 regular file，复制到独立 evidence root 后用现有 ZAP report oracle 解析；报告缺失/损坏为 harness failure，扫描器超时或不可用为 environment failure，风险超过阈值为产品 fail。
- [x] 将 security 结果接入 ProductRun、verification-result、domain coverage、T3 tier、JUnit、failure bundle、stability 和 artifact index；零退出码但无报告不能伪造通过。
- [x] 使用临时项目 fixture 联调绿色报告、风险告警和缺报告三条路径，并与发布产物、API、Web、容器、mock、k6 既有测试联调。
- [x] focused 回归：`uv run pytest -q --no-cov tests/product_testkit/test_security_scan.py tests/product_testkit/test_manifest.py tests/product_testkit/test_report.py tests/product_testkit/test_runner.py tests/product_testkit/test_stability.py`：**141 passed**；全套 TestKit 随后继续联调。
- [ ] 尚未使用 digest-pinned ZAP Docker scanner 对真实隔离产品目标执行 active/passive 扫描；该项必须在产品声明 target、镜像和安全阈值后进行，不能以 fixture 报告替代。

## 阶段 7/8/9 增量：Issue→测试→交付→合并后回归顺序联调（2026-09-15）

- [x] 新增 `tests/product_testkit/issue_loop.py` 的通用 `IssueMergeLoop`：固定 Issue `(issue_id, revision)` 身份，执行 `ingressed → triage → approval → tests → delivery readiness → merge → post-merge regression`，并只在所有前置门禁通过时调用 merge 回调。
- [x] 同一 Issue revision 重复到达只返回第一次结果并标记 `duplicate=true`，不重复 triage、测试、外部 merge 或 post-merge 副作用；Issue body 不会被解释为 shell/远端命令。
- [x] 增加成功、产品测试失败阻断 merge、未审批阻断、重复 revision 幂等、merge 后回归失败五类确定性 case；测试失败、交付未就绪、merge 非 merged 和 post-merge 非 pass 均保留明确终态。
- [x] 该 oracle 与 `ExternalSinkSpy`/产品 runner 的接口边界保持一致：实际 ZaoFu ControlledAction、candidate/run contract 和远端 API 由适配器回调负责，TestKit 不直接 push/merge。
- [x] 局部联调：`uv run pytest -q --no-cov tests/product_testkit/test_issue_loop.py tests/product_testkit/test_security_scan.py tests/product_testkit/test_runner.py`：**30 passed**；随后全套 TestKit 继续回归。
- [ ] 尚未执行真实 ZaoFu Issue ingress→triage→Fix Run→candidate→ControlledAction merge→post-merge 产品测试的跨仓库 CI job；现有 ZaoFu conformance route 可作为适配器验证来源，但不把真实远端写入纳入默认门禁。

## 阶段 8/9 增量：T0～T3 CI 矩阵执行与聚合（2026-09-15）

- [x] 新增 `tests/product_testkit/ci.py::run_ci_matrix`：按 manifest 的 tier 计划在 `T0/T1/T2/T3` 累计执行，每层使用独立 evidence 目录，保留 verification-result、JUnit 和 failure bundle 路径。
- [x] 聚合 verdict 固定风险优先级 `harness_failure > environment_failure > flaky > not_run > fail > pass`；任何层失败不会被后续层覆盖，矩阵报告写入 `ci-matrix.json`。
- [x] CLI 增加 `python -m tests.product_testkit --ci-matrix`，与单层/ prior-result 参数冲突时拒绝；矩阵命令只执行测试和证据收集，不执行 merge/push。
- [x] 新增成功矩阵、产品失败保留和非法层选择回归；CLI 矩阵用临时 Git 产品验证四层独立输出。
- [ ] 尚未在 `.github/workflows` 或 `scripts/dev-verify.py` 中接入真实产品项目 job、artifact 上传和资源配额；这属于仓库 CI 装配，不影响 TestKit API 的通用闭环自证。

## 阶段 5/8/9 增量：恢复与并发场景进入 manifest runner（2026-09-15）

- [x] `product-test-manifest.v1` 新增可选 `recovery_scenarios[]`：固定单行 shell-free 命令、产品根目录内 `state_path`/`ready_path`、integration/e2e/release tier、P-12/acceptance 绑定、正超时及 mandatory/deterministic；路径越界、命令引号错误、域错误、重复 ID 和非法预算均 fail-closed。
- [x] 新增可选 `concurrency_scenarios[]`：固定至少一个 shell-free worker 命令、integration/e2e/release tier、P-13/acceptance 绑定、正超时及 mandatory/deterministic；空命令、换行/引号注入、重复 ID 和域错误均 fail-closed。
- [x] `run_recovery_scenarios` 已接入统一 runner：按声明命令执行 baseline→SIGKILL→restart→state equivalence，写入 `recovery.<id>.receipt.json`；`run_concurrency_scenarios` 并行启动 worker、限制墙钟预算、清理进程组并写入 `concurrency.<id>.receipt.json`。
- [x] 恢复/并发结果已进入 ProductRun、domain coverage、verification-result、JUnit、failure bundle、artifact index、stability 和 CI T1/T2/T3 计划；mandatory 场景失败不能被后续阶段或普通命令绿色覆盖。
- [x] 缺少可执行程序、进程启动边界错误和并发超时记录为 `environment_failure`；进程正常运行但业务状态/退出码不满足契约记录为产品 `fail`，避免基础设施错误误归产品缺陷。
- [x] 新增恢复、并发、manifest、release 场景环境错误回归；全套 `uv run pytest -q --no-cov tests/product_testkit`：**321 passed, 3 warnings**；`uv run ruff check tests/product_testkit` 待本阶段收尾执行。
- [ ] 尚未在真实产品的数据库事务、消息乱序、ZaoFu commit-point failpoint、多实例副作用等价和业务 state reader 上执行；这些需要产品 manifest 提供明确状态/ready/cleanup 与依赖拓扑，不是通用闭环建设的前置条件。

## 阶段 6/8/9 增量：持久化/迁移场景进入 manifest runner（2026-09-15）

- [x] `product-test-manifest.v1` 新增可选 `persistence_scenarios[]`：固定有序 shell-free 命令列表、integration/release tier、P-09/acceptance 绑定、正超时及 mandatory/deterministic；空命令、换行/引号注入、悬空引用、重复 ID 和错误域均 fail-closed。
- [x] 新增 `run_persistence_scenarios`：按声明顺序执行迁移、约束、备份/恢复或一致性命令，在首个失败处停止，保存每步 stdout/stderr/receipt 及聚合 `persistence.<id>.receipt.json`。
- [x] 持久化结果已接入 ProductRun、域覆盖、verification-result、JUnit、failure bundle、artifact index、stability 和 CI tier 计划；环境不可用/超时与产品命令非零退出分开分类。
- [x] 新增持久化场景正向、停止后续命令和 manifest fail-closed 回归；补充域筛选的浏览器选择回归，并统一可选测试单元的 `mandatory` 非阻断语义；同时将 `.hypothesis` 纳入 pytest 排除目录；全套 `uv run pytest -q --no-cov tests/product_testkit`：**341 passed**；`uv run ruff check tests/product_testkit`：**All checks passed**。
- [ ] 尚未替产品执行真实 PostgreSQL/MySQL/Redis migration、事务、备份恢复和数据约束；这些依赖具体产品 manifest 的容器、凭据、初始化和清理命令，不构成通用 TestKit 闭环的前置条件。

## 阶段 4/5/8/9 增量：外部集成测试边界复核（2026-09-15）

- [x] 已有 `mock_services[]`、`ExternalSinkSpy`、有限重试和失败分类支撑 P-10 的确定性 fake 服务测试；mock 请求、重试次数、错误响应和清理 receipt 已进入统一 runner 证据链。
- [x] P-10 的通用 runner 不强行猜测第三方业务语义；产品可通过普通 `commands[]` 声明 Pact consumer/provider 验证命令，或在 `dependency_containers[]` 中启动协议兼容服务后执行产品自有契约断言。
- [ ] 尚未增加 Pact 专用报告解析器或 Toxiproxy 自动注入器；这两者需要产品声明 consumer/provider 状态、代理拓扑、故障预算和 cleanup，缺少这些输入时不能安全自动化，暂不作为通用闭环的必需前置。

## 阶段 7/8/9 增量：Issue revision 幂等状态持久化（2026-09-15）

- [x] `IssueMergeLoop` 增加可选持久化状态文件 `product-issue-loop-state.v1`；已完成的 `(issue_id, revision)` 在进程重启后仍只执行一次 triage、测试、merge 和 post-merge。
- [x] 状态采用原子 JSON 写入；损坏、错误 schema 或非法行不会被当成空状态，直接输出 `IssueLoopError`，避免重复远端副作用。
- [x] 新增跨实例重启幂等、损坏状态和持久化失败负向测试；本阶段 focused 回归 `7 passed`，全套 TestKit 回归 **342 passed**，Ruff 通过。
- [ ] 实际 ZaoFu Issue ingress、ControlledAction 和远端 merge 适配器仍需在真实运行环境接入；TestKit 不直接执行 push/merge。

## 阶段 9/10：独立参考产品证明通用闭环不依赖真实产品（2026-09-15）

- [x] 新增 `tests/product_testkit/test_reference_product_closure.py`，每次测试动态创建独立临时 Git 产品项目和不可变 target commit；测试不读取 真实生成产品样本，也不依赖任何真实产品仓库、Provider 或远端服务。
- [x] 参考产品走统一 `manifest → setup/build → product command → release artifact → security report → recovery → concurrency → persistence → verification-result/JUnit/failure-bundle → Product Acceptance/delivery readiness` 路径；同时确认 `domain_coverage.missing == []`、artifact digest 和 cleanup 证据完整。
- [x] 参考产品闭环输出 `pass` 且 `delivery_readiness.allowed == true`，并明确 `merge_authorized == false`；证明测试通过不会越权授予 merge/push 权限。
- [x] 同一参考产品的故障变体使产品命令退出码为 17，TestKit 判定为 `fail`（不是 `environment_failure` 或 `harness_failure`），并由 `IssueMergeLoop` 阻断 merge；证明产品缺陷是闭环的可观察终态，不是闭环建设阻塞条件。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **344 passed**；`uv run ruff check tests/product_testkit/test_reference_product_closure.py` 为 **All checks passed**。
- [x] 术语和完成边界修正：通用 TestKit 闭环由版本化 manifest、独立参考产品/故障夹具和跨域联调证明；真实产品项目只承担兼容性 canary 与自身质量认证，真实产品缺陷或契约缺失不能阻止通用闭环继续构建。
- [x] 补充外部产品调用方式：可从任意工作目录使用 `uv run --project /home/uisee/zaofu_v7 python /home/uisee/zaofu_v7/scripts/product-testkit.py --manifest <product-manifest> --project-root <product-root> --output-dir <evidence-root> --run-id <run-id> --ci-matrix`；调用只执行声明的测试并写证据，不执行 push/merge。
- [ ] 真实产品的业务 acceptance、Docker Playwright、真实数据库/第三方协议、性能/soak、ZAP active scan、发布矩阵及 ZaoFu ControlledAction 仍需按项目 manifest 和环境单独执行；不得用参考夹具的 `pass` 冒充真实产品质量。

## 阶段 9 补充：target commit 与 tracked 工作树身份门禁（2026-09-16）

- [x] 按 Git 官方对象与 diff 语义完成调研，记录于 `product_worktree_identity_research.md`；明确 `HEAD` 字符串本身不能证明实际测试字节属于目标提交。
- [x] `verify_target_commit` 现在分别用 `git rev-parse --verify --end-of-options <rev>^{commit}` 规范化 manifest target 与当前 HEAD；大写 SHA 与同一对象不再被误判，分支名仍由 manifest schema 拒绝。
- [x] 在任何 setup/产品命令之前执行 `git diff --quiet --no-ext-diff <resolved-target> --`；已暂存和未暂存的 tracked 修改都会输出 `ProductHarnessError`，不允许将运行结果绑定到错误 commit。
- [x] 明确保留未跟踪 manifest sidecar 的兼容边界；需要排除全部 untracked/ignored 污染的产品必须使用 detached worktree/status 白名单，不能把当前 tracked 门禁夸大为完整文件系统沙箱。
- [x] 正向、未暂存漂移、已暂存漂移和大写 SHA 回归与独立参考产品闭环联调通过：`16 passed`；Ruff 通过。

## 通用闭环完成审计（2026-09-16）

以下结论只证明“通用 Product TestKit 可以驱动任意已提供合法 manifest 的产品并形成终态证据”，不证明任意真实产品本身已经通过。

- [x] **骨干和细化步骤已落盘**：本文件包含阶段 0～10、每阶段输入/实现/判据/证据和历史联调结果；`ZaoFu_Product_TestKit.md` 固化 P-01～P-18、T0～T3 和 Issue 闭环合同。
- [x] **统一准入已完成**：`product-test-manifest.v1`、矩阵适配、能力裁决、`NOT_APPLICABLE` 来源、命令/域/acceptance 闭包、source root 和不可变 target commit 均 fail-closed。
- [x] **执行支撑层已完成**：普通命令、entrypoint/health、Docker 依赖、fake 外部服务、Schemathesis、Docker Playwright、k6、ZAP 报告、发布产物/安装升级回滚、恢复、并发和持久化场景均可由同一 manifest 调度。
- [x] **裁决和证据闭环已完成**：`PASS/FAIL/NOT_APPLICABLE/NOT_RUN/ENVIRONMENT_FAILURE/HARNESS_FAILURE/FLAKY` 可达；verification-result、JUnit、failure bundle、artifact index、Product Acceptance 和 delivery-readiness 保持一致并绑定 run/candidate/commit/digest。
- [x] **越权边界已完成**：测试通过只产生 `delivery_readiness.allowed=true`，固定 `merge_authorized=false`；TestKit 不执行 push/merge，实际外部写入仍要求 ZaoFu ControlledAction/人工授权适配器。
- [x] **Issue 回归闭环已完成**：`IssueMergeLoop` 固定 ingress→triage→approval→tests→delivery→merge callback→post-merge regression 顺序，同 revision 跨实例持久化去重，任何前置失败均阻断 merge callback。
- [x] **独立自证已完成**：`test_reference_product_closure.py` 动态创建独立 Git 产品，覆盖成功、产品失败、验收、交付、恢复、并发、持久化、发布、安全和 Issue merge 阻断；不依赖 真实生成产品样本、Provider 或远端服务。
- [x] **最终联调复核**：`uv run pytest -q --no-cov tests/product_testkit` 为 **346 passed**；`uv run ruff check tests/product_testkit` 为 **All checks passed**；`git diff --check`（TestKit 与本方案文档）无输出。
- [x] **最终联调复核（更新）**：补充未跟踪文件与 manifest 白名单门禁后，`uv run pytest -q --no-cov tests/product_testkit` 为 **354 passed**；`uv run ruff check tests/product_testkit` 为 **All checks passed**；限定 `git diff --check` 无输出。
- [x] **缺陷处理纪律已遵守**：本轮 Product TestKit 自证未发现新的 ZaoFu `src/` 生产缺陷；未修改 ZaoFu 生产代码。真实生成产品样本 的 SBOM、脚本和浏览器失败保持产品结果，不写入 ZaoFu Issue。

## 阶段 9 增量：重复运行的产品身份与未跟踪文件门禁（2026-09-16）

- [x] 复核发现仅比较 `HEAD` 与 target commit 不能阻止未跟踪产品源码被命令执行；该缺口会使测试证据脱离声明 commit。
- [x] `verify_target_commit` 现在在 setup/产品命令之前读取 Git porcelain 状态：只允许 manifest sidecar 和 manifest 显式声明的 `runtime_paths`，其余未跟踪文件 fail-closed。
- [x] `runtime_paths` 采用相对、唯一、无 `..` 的路径合同；默认空清单，不能使用通配符；状态和证据仍推荐写到隔离 output root。
- [x] 新增未跟踪产品源码拒绝、manifest sidecar 允许、非法 runtime path 拒绝和重复运行 flaky 状态保留测试；`uv run pytest -q --no-cov tests/product_testkit`：**347 passed**；`uv run ruff check tests/product_testkit`：**All checks passed**。
- [x] 本阶段只修改 TestKit 和其测试/文档，未修改 ZaoFu `src/` 生产代码，未发现需要登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

## 阶段 1 增量：Manifest 顶层字段封闭（2026-09-16）

- [x] 对版本化 `product-test-manifest.v1` 增加顶层字段白名单；拼写错误或未支持字段在任何产品命令执行前以 `ManifestError` 拒绝，避免声明被静默忽略。
- [x] 增加未知顶层字段负向回归，并与未跟踪文件、重复运行状态和参考产品闭环联调。
- [x] 当前 TestKit 回归：`uv run pytest -q --no-cov tests/product_testkit` 为 **354 passed**；`uv run ruff check tests/product_testkit`：**All checks passed**；限定 `git diff --check`：通过。
- [x] 本阶段未修改 ZaoFu `src/` 生产代码；未发现需要写入 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

真实产品后续只需提供自身 manifest、业务 oracle、项目命令、环境和阈值，然后运行同一 CLI/API。其产品失败、环境失败或准入失败是闭环的合法终态，不会把通用 TestKit 重新变成“未完成”；真实 candidate/run/ControlledAction、CI artifact 上传和远端 merge 是宿主适配/运营执行项，不由通用测试框架伪造。

## 阶段 9 增量：产品命令结束后的目标提交复核（2026-09-16）

- [x] 复核发现原有 P-01 只在执行前比较 `HEAD` 与 `target_commit`；产品命令若在测试期间改写已跟踪文件，即使退出码为 0，也可能被错误归类为产品通过。
- [x] `verify_target_commit` 增加 `check_untracked` 开关；执行前仍按 manifest/runtime path 白名单检查未跟踪文件，执行结束只复核解析后的提交身份和 tracked diff，避免把测试产物误判为源码漂移。
- [x] `run_product_plan` 在 setup 失败和所有产品测试/服务清理完成后执行 post-run identity check；检测到 tracked mutation 或 HEAD 漂移时写入 `harness_errors`，最终 verdict 由统一报告归为 `harness_failure`。
- [x] 新增真实临时 Git 产品回归 `test_target_commit_post_run_check_rejects_tracked_mutation`：产品命令返回 0 但改写 tracked 文件时，TestKit 保留命令 receipt 并拒绝通过。
- [x] focused 联调：`uv run pytest -q --no-cov tests/product_testkit/test_executor.py`：**16 passed**；随后全套 TestKit 回归与 Ruff 检查作为阶段收尾执行。
- [x] 未修改 ZaoFu `src/` 生产代码；本次发现的是 TestKit 身份校验缺口，不属于 ZaoFu 运行内核缺陷，未新增 `ZaoFu_issue.md` 条目。

## 阶段 9/10 增量：统一 CLI 验收输入与清理后身份复核（2026-09-16）

- [x] `python -m tests.product_testkit` 新增 `--acceptance-spec` 与 `--journey-results`；支持 JSON 对象/列表输入，计算 spec SHA-256，并将 Product Acceptance 结果绑定到 run、candidate 和 target commit。
- [x] `--ci-matrix` 将同一份验收 spec/旅程结果传递到各隔离 tier，避免命令行单次运行与分层运行出现不同的验收语义。
- [x] runner 在 cleanup 命令全部结束后再次检查目标提交；cleanup 即使返回 0，只要修改 tracked 产品文件，最终也会产生 `harness_failure`，并保留 cleanup receipt 与错误证据。
- [x] 新增 CLI 验收绑定回归和 cleanup 后 tracked mutation 回归；完整 TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit` **357 passed**（38.37s）；`uv run ruff check tests/product_testkit` **All checks passed**；限定 `git diff --check` 无输出。
- [x] 本阶段仍未修改 ZaoFu `src/` 生产代码；未发现需登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。产品项目自身失败仍按产品 verdict 保留，不转写为 ZaoFu Issue。

## 阶段 8/9 增量：CLI 验收输入错误的统一终态（2026-09-16）

- [x] 复核发现 Product Acceptance spec/旅程 JSON 损坏时，CLI 若只抛出参数错误会缺少统一 verification evidence；该路径不满足 TestKit 的可追溯终态要求。
- [x] CLI 现在将文件不存在、编码错误、JSON 解析错误、顶层类型错误和 spec/旅程参数不完整统一归类为 `harness_failure`，并写出 `verification-result.json`、`junit.xml` 和 `failure-bundle.json`。
- [x] 新增损坏验收输入回归；完整 TestKit 联调：`uv run pytest -q --no-cov tests/product_testkit` **358 passed**（45.15s）；`uv run ruff check tests/product_testkit` **All checks passed**。
- [x] 未修改 ZaoFu `src/` 生产代码；该问题属于 TestKit CLI 证据完整性，不新增 ZaoFu 生产 Issue。

## 阶段 8/9 增量：CI 层级域闭包边界修正（2026-09-16）

- [x] 复核发现：当产品将高阶能力（例如 P-15 发布）映射到 T3 时，旧实现仍以完整能力计划检查 T0/T1，早期层会因刻意未执行高阶单元而错误产生 `NOT_RUN`。
- [x] `run_product_plan` 现在在 `selected_command_ids`/`selected_domains` 存在时，只对本次选择实际覆盖的域执行覆盖门禁；完整 capability 计划仍保留在报告中，便于审计未执行的高层域。
- [x] `build_verification_result` 现在按本次计划计算 `domain_coverage.selected/executed/missing`；T0 只裁决 T0 单元，T3 仍裁决全部累计单元。
- [x] 新增 `test_ci_matrix_does_not_mark_earlier_tier_not_run_for_deferred_domains`，使用真实临时 Git 产品验证 T0/T3 累计矩阵最终通过，且 T0 不伪装覆盖 P-15。
- [x] focused 联调：`uv run pytest -q --no-cov tests/product_testkit/test_ci.py tests/product_testkit/test_report.py`：**21 passed**。
- [ ] 尚未在远端 GitHub Actions runner 上执行该工作流；本地 CI 语义和证据已验证，远端资源/缓存/上传属于宿主 CI 环境专项。

**阶段联调更新（2026-09-16）**：修正后执行 `uv run pytest -q --no-cov tests/product_testkit`，结果为 **359 passed**；`uv run ruff check tests/product_testkit`：**All checks passed**。该修正未修改 ZaoFu `src/` 生产代码，也未新增需要登记的 ZaoFu Issue。

## 阶段 8/9 增量：证据索引失败的统一终态（2026-09-16）

- [x] 复核发现：证据索引构建阶段的 `ArtifactError` 若未被统一入口捕获，会绕过 `verification-result`/JUnit/failure bundle 的结构化终态。
- [x] `run_product_closure` 将证据索引错误归类为 `harness_failure` 的 `evidence` 阶段；证据根可写时仍持久化三类机器报告，证据根不可写时保留返回值中的持久化失败信息。
- [x] 新增 `test_programmatic_closure_classifies_artifact_collection_failure`，验证错误类型、阶段和三类报告均可观察。
- [x] 阶段联调：`uv run pytest -q --no-cov tests/product_testkit` 为 **360 passed**；`uv run ruff check tests/product_testkit`：**All checks passed**。
- [x] 本阶段只修改 TestKit runner 与其回归测试，未修改 ZaoFu `src/` 生产代码；未发现需登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

## 阶段 1/8 增量：仓库外调用入口（2026-09-16）

- [x] 复核发现文档中的 `python -m tests.product_testkit` 仅在 ZaoFu checkout 根目录可用；从外部产品工作目录调用时，因 `tests` 未作为安装包发布而无法导入。
- [x] 新增 `scripts/product-testkit.py` 薄启动器：按自身绝对路径定位 ZaoFu checkout，将根目录加入 `sys.path`，再调用同一个 TestKit CLI；不复制测试实现、不改变产品执行语义。
- [x] 更新 `ZaoFu_Product_TestKit.md` 与本步骤记录的仓库外调用示例，明确使用启动器和 `uv run --project` 的依赖环境。
- [x] 新增 `test_checkout_launcher_imports_testkit_from_external_working_directory` 与 `test_checkout_launcher_runs_product_from_external_working_directory`，从临时外部目录分别验证导入和真实 manifest 执行；CLI 入口 7 项回归和 Ruff 检查通过。
- [x] 本阶段只增加 TestKit 启动入口与回归测试，未修改 ZaoFu `src/` 生产代码；未发现需要登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

**阶段联调更新（2026-09-16）**：新增外部真实 manifest 执行回归后，`uv run pytest -q --no-cov tests/product_testkit` 为 **362 passed**；`uv run ruff check tests/product_testkit scripts/product-testkit.py`：**All checks passed**；限定 `git diff --check` 无输出。

## 阶段 8/9 增量：TestKit 接入变更影响规划（2026-09-16）

- [x] 复核 `scripts/dev-verify.py plan --base dev`，发现 TestKit 支撑模块虽然已有 51 个直接测试文件，但此前会因不属于 `src/` 且不是 `test_*.py` 而被逐个报告为 unmapped，存在“实现已增加、自动验证路径不稳定”的门禁缺口。
- [x] 在变更候选计算中增加 `tests/product_testkit/` 共享边界：任一 TestKit 非测试支撑模块变化时，自动选择该目录下全部 TestKit 测试；不改变其他源码的 fail-closed 规则。
- [x] 新增 `test_product_testkit_support_change_selects_the_product_testkit_suite`，验证无 unmapped 错误、manifest 支撑模块会选择 `tests/product_testkit` 全套测试并生成 deterministic step。
- [x] 单路径规划验证：`python3 scripts/dev-verify.py plan --paths tests/product_testkit/manifest.py --no-parallel` 输出 `python tests: 51`、无错误并选择全套 TestKit；`uv run pytest -q --no-cov tests/test_dev_verify.py -k product_testkit_support`：**1 passed**；Ruff 通过。
- [x] 本阶段只修改 TestKit 变更规划和回归测试，未修改 ZaoFu `src/` 生产代码；未发现需登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

**阶段联调更新（2026-09-16）**：TestKit 与 `dev-verify` 联合回归 `uv run pytest -q --no-cov tests/product_testkit tests/test_dev_verify.py` 为 **380 passed, 1 skipped**；Ruff 与限定 `git diff --check` 均通过。

## 阶段 1/8 增量：外部启动器专用回归映射（2026-09-16）

- [x] 复核发现 `scripts/product-testkit.py` 属于 tooling 域，原有 tooling 哨兵只会选择 `tests/test_dev_verify.py`，启动器本身变化时不保证执行其 CLI 回归。
- [x] 在 `scripts/dev-verify.py` 增加显式 `DIRECT_TESTS` 映射，将启动器变化绑定到 `tests/product_testkit/test_cli_entrypoint.py`；通用域哨兵仍保留。
- [x] 新增 `test_product_testkit_launcher_change_selects_its_cli_regression`；单路径规划输出无错误，同时选择启动器 CLI 测试和 tooling 哨兵测试。
- [x] 验证：`python3 scripts/dev-verify.py plan --paths scripts/product-testkit.py --no-parallel --json` 输出 `errors: []`、两个测试目标和 deterministic step；专用规划回归 **2 passed**，Ruff 通过。
- [x] 本阶段只修改变更影响规划和回归测试，未修改 ZaoFu `src/` 生产代码；未发现需登记到 `ZaoFu_issue.md` 的 ZaoFu 生产缺陷。

**阶段联调更新（2026-09-16）**：TestKit 与 `dev-verify` 最终联调 `uv run pytest -q --no-cov tests/product_testkit tests/test_dev_verify.py` 为 **381 passed, 1 skipped**；Ruff 与限定 `git diff --check` 均通过。
