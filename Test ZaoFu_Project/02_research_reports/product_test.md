# 仓库级审查结论

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/product_testkit/runner.py`。早期“尚无统一产品 TestKit”结论已经过时；当前通用 runner 与参考产品闭环已存在，宿主接线/真实产品验收另记。

本次审查只依据当前工作树的源码、测试、配置、技能和脚本；没有执行全量测试。当前工作树存在大量未提交修改，以下数量和结论均是当前工作树快照，不代表所有测试已经通过。

历史审查的核心结论是（截至当时）：

> ZaoFu 已经具备“约束、派发、验证、证据、候选交付”的测试基础设施，但当时尚未形成对所有生成产品项目自动执行的统一产品质量测试闭环。

## 当前状态校正（2026-09-30）

后续 Product TestKit 已补齐通用 manifest、能力裁决、命令/服务/容器/mock、OpenAPI、浏览器、性能、安全报告、发布产物、恢复、并发、持久化、Issue loop、CI 矩阵和独立参考产品自证。当前实现入口为 `tests/product_testkit/runner.py`、`executor.py`、`manifest.py` 和 `tests/product_testkit/test_reference_product_closure.py`，实施增量见 [Product_steps.md](../03_plans_and_steps/Product_steps.md)。因此本报告中“尚未发现统一闭环”“建议新增 runner/manifest”等早期结论视为历史审查结论，不再作为当前待办。

当前仍需分开看：通用 TestKit 自证已完成记录；真实产品的 manifest、业务 acceptance、环境和阈值仍按项目单独接入；宿主 CI artifact 上传、真实远端交付和已明确排除的 `/issue-merge`、Feishu 不因通用自证自动完成。

ZaoFu 不会自动理解任意产品的业务正确性。它采用的是：

```text
产品项目事实
  → source/capability/acceptance/test matrix
  → Task Contract verification.commands
  → 产品测试命令执行
  → verification-result / Product Acceptance Report
  → candidate / goal closure
```

产品语义必须由产品项目的矩阵、任务契约、技能或验收报告声明；ZaoFu 内核主要负责 schema、身份、路径、命令、证据、状态和交付边界。

———

# 一、当前仓库中与“生成产品项目测试”直接相关的真实能力

## 1. 项目初始化与技术栈检测

`zf project init` 支持声明或检测：

- Python、Node、Go、Rust；
- backend、frontend、fullstack、library；
- `build_cmd`；
- `test_cmd`；
- `gate_cmds`；
- 是否实际存在测试文件。

证据：

- `src/zf/core/profile/schema.py:12-35`
- `src/zf/core/profile/project_types.py:30-79`
- `src/zf/cli/project.py:51-124`

当前内置默认门禁主要是：

| 技术栈 | 默认测试/门禁 |
|---|---|
| Python | `pytest`、`ruff check .` |
| Node | `npm test`、`npm run lint` |
| Go | `go test ./...`、`go vet ./...` |
| Rust | `cargo test`、`cargo clippy` |

这些只是栈级默认建议，不是对产品正确性的完整证明。

## 2. 产品测试矩阵

`zf flow intake` 会生成以下矩阵草稿：

- `source-inventory.json`
- `capability-matrix.json`
- `acceptance-matrix.json`
- `test-matrix.json`
- `task-map.json`
- `real-e2e-matrix.json`

证据：

- `src/zf/runtime/workflow_intake.py:497-713`
- `src/zf/runtime/workflow_intake.py:733-772`

矩阵初始状态是 `draft`，并明确要求后续项目适配阶段补充真实文件、模块、接口、命令和证据。它不是最终测试结果。

技能中还规定：

- `test_matrix.commands[]` 是唯一测试命令注册表；
- 每个命令必须有稳定 ID；
- 每个验收标准必须关联测试命令；
- 每个能力必须关联任务、验收和测试；
- `real_e2e_matrix` 中的命令必须真实可执行；
- 没有命令或证据时不能标记真实 E2E 通过。

证据：

- `skills/zf-project-adapter-matrix-enrichment/SKILL.md:23-40`
- `skills/zf-project-adapter-matrix-enrichment/SKILL.md:90-141`
- `skills/zf-project-adapter-matrix-enrichment/SKILL.md:206-226`

## 3. Task Contract 测试命令

ZaoFu 已将验证命令结构化：

```json
{
  "id": "unit-tests",
  "command": "pytest -q tests",
  "acceptance_ids": ["AC-1"],
  "owner": "task_verify",
  "tier": "runtime",
  "deterministic": true,
  "reusable": true,
  "timeout_seconds": 900
}
```

证据：

- `src/zf/runtime/verification_commands.py:18-91`
- `src/zf/runtime/verification_commands.py:139-176`

Candidate 阶段优先执行 Task Contract 中声明的命令；缺失时可退回 `quality_gates`，但在 `task_contract_required` 模式下会直接失败。

证据：

- `src/zf/runtime/candidates.py:1026-1073`
- `src/zf/runtime/candidates.py:1585-1628`

## 4. Product Acceptance

当前仓库有明确的产品验收契约：

`product_acceptance_spec.v1` 要求：

- 产品启动入口；
- health check；
- 必填用户旅程；
- 可观察断言；
- 断言类型可以是 `api`、`cli`、`dom`、`interaction`、`state`、`visual`；
- provider qualification policy。

证据：

- `src/zf/runtime/product_acceptance.py:16-29`
- `src/zf/runtime/product_acceptance.py:50-130`

`product_acceptance_report.v1` 要求：

- workflow run identity；
- task-map generation；
- candidate ref；
- target commit；
- spec ref/digest；
- 每个 mandatory journey 的结果；
- 每个 mandatory assertion 的结果；
- 每个结果的 evidence ref/digest。

证据：

- `src/zf/runtime/product_acceptance.py:170-315`
- `src/zf/runtime/product_acceptance.py:398-546`

但该契约是否强制执行，取决于 workflow 的 `product_acceptance_mode`：

- `blocking`：必须提供；
- `shadow`：可选；
- 未配置时不能视为所有项目都已强制启用。

证据：

- `src/zf/core/config/workflow_profiles.py:821-841`
- `src/zf/runtime/plan_candidate_preflight.py:258-309`

## 5. 运行身份、工作树和证据绑定

ZaoFu 已经具备：

- run contract；
- project root；
- state dir；
- workflow input refs；
- target commit；
- artifact refs/digests；
- candidate worktree；
- setup script；
- local-only push guard。

证据：

- `src/zf/runtime/run_contract.py:34-188`
- `src/zf/runtime/run_contract.py:671-809`
- `src/zf/runtime/workdirs.py:51-139`
- `src/zf/runtime/candidates.py:890-925`
- `src/zf/runtime/candidates.py:1630-1655`

———

# 二、所有 ZaoFu 生成产品项目必须具备的测试覆盖范围

不能要求所有项目执行完全相同的业务测试命令，因为生成项目可能是 CLI、Web、API、库、后台服务或多模块仓库。

因此应该采用：

```text
统一测试域
  + 产品能力矩阵
  + 明确的 not_applicable
  + 产品项目自有命令
```

“不适用”必须显式声明并附原因，不能通过缺少测试命令来默认为通过。

## 产品项目通用测试清单

| 编号 | 测试域 | 具体测试方法 | 工具 | 通过判据 | 必须保留的证据 |
|---|---|---|---|---|---|
| P-01 | 项目身份与目标树 | 检查 source root、target root、target commit、candidate ref | `git`, ZaoFu run contract | 测试执行目录与声明目录一致；`HEAD == target_commit` | `run-contract.json`、`git rev-parse HEAD` 输出 |
| P-02 | 环境初始化 | 在干净 worktree 执行项目 setup/install | 项目声明的 setup 命令、`WorkdirManager` | 依赖安装成功；setup 非零退出则测试阻塞；没有跨目录写入 | setup 日志、环境快照、退出码 |
| P-03 | 构建与静态检查 | 执行 build、lint、typecheck、compile | 项目声明命令；Python/Node/Go/Rust 默认命令 | 所有 mandatory 命令退出码为 0；命令不能被改写或拼接 | command receipt、stdout/stderr、命令 digest |
| P-04 | 单元与组件逻辑 | 覆盖正常、边界、错误、空输入、重复输入 | 项目自身测试框架，如 pytest、Jest、Go test、Cargo test | 所有 mandatory case 通过；失败必须映射到 acceptance ID | 测试报告、失败用例、覆盖率报告 |
| P-05 | 需求—能力—验收闭包 | 对照 `capability_matrix`、`acceptance_matrix`、`test_matrix` | ZaoFu matrix admission | 每个 blocking capability 至少有 task、acceptance、test；无悬空 ID | 三类矩阵及闭包检查结果 |
| P-06 | CLI 表面 | 测试 help、参数、标准输出、错误码、stdin/stdout、重复执行 | 项目 CLI 测试命令 | 正常输入结果正确；非法输入返回预期非零码；不产生越权文件 | CLI transcript、退出码、文件快照 |
| P-07 | API 表面 | 测试 schema、认证、授权、错误码、幂等、分页、并发请求 | 项目 API 测试；若项目提供 OpenAPI，可使用 Schemathesis，但当前仓库未内置 | 响应结构、状态码、权限边界与契约一致；错误不能变成成功 | 请求/响应记录、schema、服务日志 |
| P-08 | Web/浏览器表面 | 测试启动、health、关键用户旅程、DOM、交互、动态变化 | 项目声明的 Playwright 命令；仓库规范建议 Docker `mcp/playwright:latest` | 关键 DOM/业务断言通过；动态功能必须有 before/after 差异；截图非空 | DOM 断言、截图、trace、网络和 console 日志 |
| P-09 | 数据与持久化 | 测试初始化、迁移、读写一致性、约束、重复写、备份恢复 | 项目数据库测试、迁移脚本、容器化依赖 | 新旧 schema 可迁移；非法数据被拒绝；重复操作幂等 | migration 日志、schema diff、数据快照 |
| P-10 | 外部集成 | 使用 mock/contract test 验证第三方接口、超时、重试、错误响应 | mock server、项目集成测试；Testcontainers/Toxiproxy 当前不是仓库依赖 | 第三方失败不会伪造业务成功；重试有上限；凭据不写入日志 | mock 交互记录、重试计数、脱敏日志 |
| P-11 | 安全与权限 | 测试越权、路径逃逸、命令注入、secret 泄漏、恶意输入 | 项目安全测试；必要时 ZAP，但当前仓库未内置 | 未授权请求为 0 次成功；越权路径为 0 次；secret 不出现在产物 | 安全扫描结果、拒绝日志、secret 扫描报告 |
| P-12 | 故障与恢复 | 在超时、进程退出、依赖不可用、重复消息后恢复 | subprocess、项目恢复脚本、容器/网络故障工具 | 重启后状态不丢失、不重复副作用、不回退；最终结果与无故障运行一致 | 崩溃点、重启日志、前后状态快照 |
| P-13 | 并发与幂等 | 重复请求、乱序事件、多 worker、并发写、重复 webhook | pytest 并发 fixture、项目并发测试；xdist 只是加速器 | 不产生重复业务对象；状态单调；锁和事务有效 | 调度记录、调用计数、最终状态 |
| P-14 | 性能与长期稳定性 | 吞吐、延迟、资源泄漏、长时间运行、批量数据 | k6 或项目性能工具；当前仓库仅有少量 perf marker | 达到项目声明预算；无内存、句柄、端口、进程泄漏 | 性能报告、资源曲线、soak 日志 |
| P-15 | 打包与部署 | wheel/sdist/npm 包/容器构建、全新环境安装、启动和升级 | uv, npm/pnpm, Docker、项目部署脚本 | 干净环境可安装；服务可启动；版本升级和回滚可验证 | 包清单、镜像 digest、启动日志 |
| P-16 | 观测与诊断 | 检查日志、健康状态、错误 trace、审计事件和敏感字段脱敏 | 项目日志测试、health endpoint、trace 检查 | 关键失败可定位；日志与实际状态一致；无敏感信息泄漏 | 日志、trace、health 响应 |
| P-17 | 回归测试 | 为每个已知 issue 建立重现命令和修复后的回归用例 | 项目测试框架、`regression_test_matrix` | 修复前能重现，修复后通过；旧行为不能悄然退化 | issue ref、重现记录、回归报告 |
| P-18 | ZaoFu 交付边界 | 检查 candidate、commit、artifact、report、state dir 和 remote policy | ZaoFu Candidate、Product Acceptance、run contract | evidence 完整、digest 正确、目标 commit 一致；未授权 push/merge 为 0 次 | candidate manifest、acceptance report、artifact refs |

## 条件测试域

下列测试不是每个项目都执行相同命令，但每个项目都必须做适用性裁决：

| 项目能力 | 必须执行 |
|---|---|
| 有 CLI | P-06 |
| 有 HTTP/API | P-07 |
| 有 Web UI | P-08 |
| 有数据库或本地持久化 | P-09 |
| 有外部服务 | P-10 |
| 有登录、角色或敏感数据 | P-11 |
| 有长期运行进程 | P-12、P-13、P-16 |
| 有发布包或部署物 | P-15 |
| release/full-parity | P-14、P-15 |
| Issue 修复 | P-17 |
| 所有 ZaoFu 生成项目 | P-01 至 P-05、P-18 |

———

# 三、每个测试结果如何判断

每个产品测试 case 都必须包含：

```text
Given       初始项目、commit、依赖、数据库和环境
When        执行的准确命令或用户动作
Then        必须出现的状态、输出和结果
Forbidden   绝对不能出现的副作用、错误状态或越权行为
Evidence    用于裁决的报告、日志、快照、trace、digest
```

## 通过条件

一个产品测试 case 必须同时满足：

1. 命令或测试动作正常完成；
2. 预期业务断言全部成立；
3. 所有 mandatory acceptance 均有结果；
4. 结果绑定当前 `target_commit`；
5. evidence ref 存在且 digest 正确；
6. 没有禁止事件或未授权副作用；
7. 没有环境/测试基础设施错误。

## 失败分类

应区分：

### 产品失败

满足以下条件后才判定为产品失败：

- worktree 正确；
- setup 已成功；
- 测试工具可用；
- 服务已正常启动；
- 产品 API、CLI、DOM、状态或输出违反产品契约。

### 环境/测试基础设施失败

例如：

- Docker 不可用；
- 依赖安装失败；
- workdir 不可写；
- 浏览器未准备好；
- `HEAD != target_commit`；
- 缺少 provider 或外部服务；
- runner 镜像/工具版本错误。

浏览器测试技能明确要求这些情况先归为环境或 harness 问题，不能直接判为产品失败。

证据：

- `skills/zf-browser-e2e-contract/SKILL.md:145-155`
- `skills/zf-browser-e2e-contract/SKILL.md:167-192`

———

# 四、当前已有测试覆盖了哪些产品项目相关能力

## 1. 已有较强覆盖

| 能力 | 现有证据 | 覆盖结论 |
|---|---|---|
| 产品验收 spec/report schema | `tests/test_product_acceptance.py` | 已覆盖 identity、journey、assertion、evidence、provider receipt、stale commit |
| Plan Candidate 预检 | `tests/test_plan_candidate_preflight.py` | 已覆盖 Product Acceptance 进入 critic 前的结构检查、矩阵闭包和错误拒绝 |
| Product Delivery task-map 入库 | `tests/test_product_delivery.py` | 已覆盖任务物化、verification command、source index、coverage report、replan |
| Candidate 质量门禁 | `tests/test_candidates.py` | 已覆盖 setup、Task Contract 命令、quality gates、失败阻止 candidate 更新 |
| 工作树与 setup | `tests/test_worktree_env.py`、`tests/test_workdirs.py` | 已覆盖 setup marker、脚本变化重跑、失败不写 marker、worktree 边界 |
| 运行合同与恢复身份 | `tests/test_run_contract.py` 等 | 已覆盖 run contract、ref/digest、resume drift 的部分行为 |
| Artifact 与 evidence | `tests/test_sidecar_refs.py`、`tests/test_artifact_*` | 已覆盖 ref、digest、路径逃逸、缺失 evidence、sidecar |
| Issue candidate 交付 | `tests/test_issue_candidate_delivery.py` | 已覆盖候选仓库、base、candidate、PR 读取和远端写入限制 |
| 产品流程 fixture | `tests/e2e/fixtures/product-pulse`、`tests/test_product_pulse_golden_baselines.py` | 已覆盖固定示例产品 fixture 的文件和 Git blob 稳定性 |

特别是 `tests/test_product_acceptance.py:206-351`，已经证明了：

- mandatory journey 缺失 assertion 时拒绝；
- passed journey 不能隐藏 failed assertion；
- provider receipt 不能复用；
- provider qualification 有 TTL；
- stale target commit 会被拒绝；
- 缺失 Product Acceptance report 会被拒绝。

## 2. 已有但不是通用产品测试

以下测试存在，但不能直接证明“任意生成产品项目都测试完整”：

- `tests/e2e/test_generic_workflow_complex_mock_e2e.py`
- `tests/e2e/test_agent_owned_verification_mock_e2e.py`
- `tests/e2e/test_terminal_kanban_convergence_mock_e2e.py`
- `tests/e2e/test_recovery_control_loop_serial.py`
- `tests/e2e/test_full_stack_validation.py`
- `tests/e2e/fixtures/product-pulse/...`

它们主要验证：

- ZaoFu 工作流；
- mock provider；
- candidate/verification；
- Kanban/状态收敛；
- ZaoFu 自身的恢复；
- 固定 fixture 的产品交付样例。

它们不等于：

- 任意生成产品的业务功能测试；
- 任意产品的数据库测试；
- 任意产品的 API schema 测试；
- 任意产品的部署/升级/回滚测试；
- 任意产品的性能和安全测试。

## 3. ZaoFu Web 测试不是产品 Web 测试

当前仓库有 85 个前端测试相关文件，其中大量是 ZaoFu 自身 Dashboard、Kanban、SSE、Trace 和 Issue 页面测试。

证据：

- `web/package.json:6-24`
- `tests/test_web_server.py`
- `tests/test_web_sse.py`
- `tests/test_workspace_projects.py`
- `web/tests/*.spec.ts`

这些测试证明 ZaoFu Web 投影层的行为，不证明生成产品项目的 Web 页面行为。

## 4. 当前测试规模

静态盘点结果：

- Python `test_*.py`：967 个；
- Python E2E/Integration 文件：52 个；
- Web 测试相关文件：当前按文件名粗略统计 171 个；
- 这些统计包含当前未提交修改；
- 本轮没有执行测试，不能据此说明通过率。

———

# 五、当前欠缺的通用产品项目测试

## P0 缺口

### 1. 缺少“所有生成项目通用”的产品测试入口

当前有矩阵、命令和报告契约，但没有发现一个统一命令能够对任意生成项目自动完成：

```text
识别项目能力
→ 读取产品测试矩阵
→ 执行所有适用测试
→ 收集结果
→ 生成统一 Product Acceptance Report
→ 绑定 candidate/commit
```

目前仍依赖项目适配技能和项目自身命令。

### 2. 缺少产品项目级测试 manifest 的统一强制规范

当前存在：

- `project-profile.v1`；
- `task-map.v1`；
- `test-matrix.v1`；
- `product_acceptance_spec.v1`；
- `verification-result.v1`。

但没有看到一个明确规定所有生成项目必须提供以下内容的统一产品测试 manifest：

- 项目类型；
- 可测试 surface；
- build/test/package 命令；
- 服务启动命令；
- health check；
- 数据依赖；
- 安全测试入口；
- 性能预算；
- cleanup 方法；
- 不适用能力及理由。

### 3. 缺少完整的产品项目端到端闭环测试

已有组件测试较多，但尚未发现一个通用测试完整证明：

```text
zf project init
→ product project
→ flow intake
→ matrix enrichment
→ implementation
→ product tests
→ Product Acceptance Report
→ candidate verification
→ goal closure
```

并同时覆盖：

- 重启；
- stale commit；
- 重复 webhook；
- 缺少 evidence；
- candidate ref 错误；
- 未授权外部副作用。

### 4. 缺少通用 API、数据库和部署测试适配层

ZaoFu 可以执行项目声明的命令，但没有看到统一的：

- OpenAPI/HTTP contract runner；
- 数据库迁移/恢复 runner；
- 包安装/升级/回滚 runner；
- 服务健康与依赖拓扑 runner。

这些需要由每个产品项目自行声明或另外建设 TestKit。

### 5. 缺少统一安全输入—副作用矩阵

当前已有 ZaoFu 自身的：

- token；
- path guard；
- signing；
- Issue ingress；
- ControlledAction；

测试，但还没有一套通用于生成产品项目的矩阵，系统验证：

```text
Issue / 用户输入 / API 参数 / 文件名 / Provider 输出
→ shell / 文件系统 / 数据库 / git / 远端 API
```

是否存在未授权路径。

### 6. 缺少系统化故障点矩阵

ZaoFu 有恢复和崩溃测试，但仍未形成通用产品项目级 failpoint 矩阵，例如：

- artifact 写入后崩溃；
- 测试报告写入后崩溃；
- 数据库迁移中断；
- 外部 API 成功后结果落盘前崩溃；
- 服务启动后 health 成功前崩溃；
- 部署切换后回滚。

### 7. 缺少通用性能和长期稳定性门禁

`pyproject.toml` 只有 `perf` marker，且默认排除：

```toml
addopts = "--cov=zf --cov-report=term-missing -m 'not perf and not host and not real_provider'"
```

证据：

- `pyproject.toml:41-52`

这说明性能、宿主机和真实 Provider 本来就是显式 tier，而不是默认回归。对于产品项目，目前没有统一性能预算、资源泄漏和 soak 结果协议。

## P1 缺口

- 缺少 Hypothesis 状态序列测试的通用产品模板；
- 缺少多项目、多语言安装矩阵；
- 缺少升级旧版 product artifact/state 的兼容性测试；
- 缺少测试工具自身崩溃、超时、输出截断的统一分类；
- 缺少 mutation testing 对产品测试有效性的定期审计；
- 缺少跨项目报告聚合和趋势展示；
- 缺少“测试通过但没有真实断言”的检测。

———

# 六、建议增加的通用测试代码

建议增加的不是某个具体产品的业务测试，而是两层代码。

## 1. ZaoFu 侧通用 TestKit

建议建设：

```text
tests/product_testkit/
  manifest.py          # 产品测试 manifest 读取与校验
  matrix.py             # capability/acceptance/test 闭包
  command_runner.py     # 逐条执行声明命令并记录 receipt
  product_oracle.py     # 统一 pass/fail/not_applicable 判定
  evidence.py           # 日志、报告、digest、sidecar
  process_runner.py     # 启停、崩溃、重启、清理
  security_spy.py       # shell/git/remote side-effect 计数
  web_runner.py         # Docker Playwright 入口
  environment.py        # 依赖、版本、目录、commit 检查
```

## 2. 生成产品项目侧测试模板

`zf project init` 或产品 workflow 应至少生成：

```text
product-test-manifest.json
tests/
  test_smoke.py
  test_contract.py
  test_regression.py
  test_security.py
  test_recovery.py
  e2e/
```

其中业务测试仍由产品项目或适配技能生成；ZaoFu 只校验：

- 命令是否声明；
- 命令是否可执行；
- 测试结果是否绑定 acceptance；
- evidence 是否完整；
- commit 是否正确；
- 是否产生越权副作用。

———

# 七、推荐执行顺序

## 每次产品任务

1. 检查 project root / state dir / target commit
2. 执行 setup
3. 执行 build、lint、typecheck
4. 执行 unit/component
5. 执行 API/CLI/数据库测试（按 capability）
6. 执行关键用户旅程 E2E
7. 执行安全和回归测试
8. 收集 evidence 和 digest
9. 生成 verification-result
10. 进入 Candidate/Product Acceptance gate

## 合并前

必须至少执行：

- 所有 affected task commands；
- 所有 mandatory acceptance；
- 受影响 API/Store/Event/Schema 的契约测试；
- 必要的 deterministic mock E2E；
- 产品项目真实入口 smoke；
- candidate target commit 校验。

## 发布前

额外执行：

- Docker/部署测试；
- 包安装测试；
- 升级和回滚；
- 性能/压力/soak；
- 安全扫描；
- 真实外部依赖或 Provider canary；
- 清理检查。

———

# 最终判断

当前 ZaoFu 已有较强的“测试执行和交付约束能力”，尤其是：

- Task Contract；
- verification commands；
- acceptance/test matrix；
- Product Acceptance spec/report；
- candidate quality gates；
- worktree、commit、artifact、digest；
- ZaoFu 自身的事件、状态和恢复测试。

但它还没有完成“所有生成产品项目的一套完整通用测试套件”。目前最需要补的不是更多零散 `test_*` 函数，而是：

1. 通用产品测试 manifest；
2. 统一命令执行和结果收集器；
3. capability → acceptance → test → evidence 闭包；
4. 产品项目级 API/Web/数据/安全/恢复/部署测试适配；
5. 产品测试结果到 Product Acceptance、Candidate 和 Goal Closure 的单一端到端闭环。

另外，AGENTS.md 指定的权威架构文档 `docs/design/142-layered-runtime-authority-and-orchestration-modes.md` 当前不存在，因此本次审查以当前源码、schema、技能和测试为主要事实依据。

---

# 第三部分 对product_testing_research.md测试框架与方法讨论分析

## 结论

不建议为所有产品项目选择一个“大而全”的测试框架。

最合适的方案是：

> 以 pytest 作为统一执行主框架，以产品项目自己的测试框架作为业务测试实现；再按产品能力组合 Playwright、Hypothesis、Schemathesis、Pact、Testcontainers、k6 和 OWASP ZAP。

推荐结构：

```text
pytest
  ├─ 固定场景、单元、集成、恢复、安全和交付边界测试
  ├─ Hypothesis：复杂输入和状态序列
  ├─ Schemathesis：OpenAPI/API
  ├─ Testcontainers：数据库、队列、缓存
  ├─ Pact：服务间契约
  ├─ Playwright：Web 用户旅程
  ├─ k6：性能和长期运行
  └─ OWASP ZAP：动态 Web/API 安全
```

完整调研内容见：

`awesome-zaofu/New_function/Test ZaoFu_Project/product_testing_research.md`

---

## 一、先区分“测试方法”和“测试框架”

调研文档中的内容并不都是框架。

| 类型 | 内容 | 作用 |
|---|---|---|
| 测试方法 | 测试金字塔 | 决定单元、集成、契约、E2E 的比例 |
| 环境方法 | Hermetic Testing | 固定依赖、目录、版本和外部服务 |
| 主执行框架 | pytest | 发现、执行、断言、fixture、报告 |
| 浏览器框架 | Playwright | 浏览器用户旅程和 DOM 验证 |
| 输入生成工具 | Hypothesis | 边界输入、随机状态序列、不变量 |
| API 测试工具 | Schemathesis | 从 OpenAPI/GraphQL 生成 API 测试 |
| 服务契约工具 | Pact | Consumer/Provider 接口契约 |
| 依赖环境工具 | Testcontainers | 启动隔离数据库、队列和缓存 |
| 性能工具 | k6 | 并发、延迟、错误率和 soak |
| 安全工具 | OWASP ZAP | Web/API 动态安全扫描 |
| 结果格式 | JUnit XML、JSON、HAR、trace | CI 聚合和失败复现 |

因此，不能简单问“选 pytest 还是 Playwright”。它们解决的是不同问题。

---

## 二、主框架选择：pytest

### 建议

所有生成产品项目都以 pytest 作为 ZaoFu TestKit 的统一执行入口。

即使产品本身使用 Jest、JUnit、Go test 或 Cargo test，也可以由 pytest 或 ZaoFu runner 调用产品声明的命令。

### pytest 负责测试什么

- 产品测试 manifest 和矩阵；
- 项目目录、commit、依赖和环境检查；
- 固定输入和固定期望；
- 单元测试；
- 组件测试；
- 集成测试；
- CLI 测试；
- 文件和数据库状态测试；
- 恢复、重试、幂等和故障注入；
- 安全输入与副作用测试；
- ZaoFu 的 evidence、candidate、commit 绑定。

### 输入方式

```text
固定测试数据
+ 正常输入
+ 边界输入
+ 非法输入
+ 空输入
+ 重复输入
+ 并发输入
+ 故障输入
```

### 结果判定

- 进程退出码；
- 断言是否成立；
- 预期异常是否发生；
- 文件、数据库和状态是否正确变化；
- 禁止的外部副作用调用次数是否为 0；
- JUnit XML 中是否存在 failed/error；
- evidence 是否完整并绑定当前 commit。

### 为什么选择 pytest

1. 当前 ZaoFu 已经使用 pytest。
2. 当前仓库的 pyproject.toml 已声明 pytest、pytest-asyncio、pytest-cov 和 pytest-xdist。
3. ZaoFu 已有大量 Python 测试，不需要迁移。
4. fixture 适合创建隔离的 project、state dir、worktree 和数据库。
5. 参数化适合把同一个产品不变量应用于多种输入。
6. marker 可以区分 deterministic、serial、mock E2E、perf、host 和 real provider。
7. JUnit/JSON 结果容易接入 ZaoFu evidence 和 CI。
8. pytest 可以调用任意语言的产品测试命令，不要求生成产品必须使用 Python。

### pytest 不能单独解决的问题

pytest 不会自动知道：

- 什么是产品正确行为；
- 哪些用户旅程必须通过；
- 哪些 API 字段是业务必需的；
- 哪些副作用是不允许的；
- 页面显示成功但数据库没有改变是否错误。

这些必须由 acceptance matrix、test matrix、产品测试函数和 ZaoFu oracle 定义。

---

## 三、按产品能力选择专用框架

### 1. Playwright：只用于 Web 产品

#### 测试范围

- 页面启动；
- 登录和权限；
- 关键用户旅程；
- 表单提交；
- 页面跳转；
- 动态状态变化；
- 错误和空状态；
- 多项目切换；
- 浏览器刷新后的状态保持；
- SSE/WebSocket 的页面表现。

#### 输入

```text
打开页面
→ 登录
→ 填写数据
→ 点击操作
→ 等待状态变化
→ 刷新页面
→ 再次查询结果
```

#### 判定

不能只检查“页面加载成功”，而应检查：

```text
点击前：Pending
点击后：API 请求成功
数据库：状态变为 Submitted
页面：显示 Submitted
刷新后：仍显示 Submitted
```

同时收集：

- DOM 断言；
- screenshot；
- trace；
- network；
- console；
- 测试退出码。

#### 为什么使用

Playwright 适合验证真实浏览器用户行为，具有 BrowserContext 隔离、自动等待和 trace。

ZaoFu 当前 Web 测试已经使用 Playwright，因此可以直接复用现有运行方式。

#### 为什么不作为主框架

- 只能覆盖 Web 表面；
- 无法替代单元、数据库、API、恢复和安全测试；
- 浏览器测试执行慢；
- 页面显示正确不代表 canonical state 正确；
- 不适用于 CLI、库和纯后台服务。

---

### 2. Hypothesis：用于复杂输入和状态不变量

#### 测试范围

- 边界数值；
- Unicode；
- 空值；
- 重复值；
- 非法组合；
- 序列化和反序列化；
- 多步状态变化；
- 重试、删除、恢复和幂等。

#### 输入示例

```text
创建资源
→ 更新资源
→ 重复更新
→ 注入非法输入
→ 删除
→ 恢复
→ 再次读取
```

#### 判定

每个生成输入都检查不变量，例如：

- 重复请求不会生成重复资源；
- 删除后资源不可见；
- 无效输入不会污染数据库；
- API 不出现未预期的 5xx；
- 状态转换不违反产品规则；
- 恢复后状态与 baseline 一致。

失败时保存 Hypothesis 缩减后的最小输入，并固化为普通 pytest 回归用例。

#### 为什么使用

产品项目输入空间通常无法靠手写 case 穷举。Hypothesis 能扩大边界空间并自动缩短失败案例。

#### 为什么不作为主框架

- 它不定义产品正确性；
- 随机生成结果不能替代固定回归用例；
- 不适合直接测试真实 Provider 或真实外部 SaaS；
- 生成的序列必须配合领域 oracle 才有意义。

---

### 3. Schemathesis：只用于有 OpenAPI/GraphQL 的 API

#### 测试范围

- API schema；
- 字段类型；
- required 字段；
- 错误参数；
- 边界参数；
- 未认证请求；
- 越权请求；
- 多步资源操作；
- 响应状态码和响应体。

#### 输入

输入由 OpenAPI/GraphQL schema 生成：

- 合法请求；
- 缺少字段；
- 类型错误；
- 极值；
- 错误 content type；
- 错误 token；
- 多步 create/update/delete 序列。

#### 判定

- 不出现未分类 5xx；
- status code 符合 API 契约；
- response schema 正确；
- 非法输入被拒绝；
- 未授权请求不能成功；
- 多步操作后的资源状态正确；
- 失败请求可以复现。

#### 为什么使用

API 产品通常有大量边界组合，Schemathesis 可以从已有 schema 自动生成测试，不需要手写所有请求。

#### 为什么不作为通用主框架

- 没有 API 的产品不适用；
- schema 正确不等于业务行为正确；
- 它不知道 EventLog、数据库和业务状态之间的权威关系；
- 不能替代用户旅程测试；
- 不能完整覆盖 SSE/WebSocket 时序。

因此关键 API 测试必须同时回查产品状态和数据库。

---

### 4. Pact：只用于独立服务之间的接口契约

#### 测试范围

- Consumer 与 Provider 的 HTTP 契约；
- 请求字段；
- 响应字段；
- Provider state；
- 版本兼容；
- 第三方服务边界。

#### 输入

```text
Consumer 测试真实请求
→ 记录 request/response interaction
→ 生成 Pact contract
→ Provider 在指定状态下重放
```

#### 判定

- 每个 interaction 都能被 Provider 响应；
- 状态码、字段和类型匹配；
- Provider state 建立成功；
- 不需要启动完整生产集群。

#### 为什么使用

适合多个独立服务或产品依赖第三方 API 的情况，可以避免“消费者以为接口是 A，提供者实现成 B”。

#### 为什么不作为所有产品的默认工具

- 单体应用或纯 CLI 没有 Consumer/Provider 边界；
- 需要维护契约版本和 provider state；
- 它只测试接口共识，不测试完整业务；
- 不能替代产品验收和 E2E。

---

### 5. Testcontainers：有真实依赖时使用

#### 测试范围

- PostgreSQL、MySQL、Redis；
- 消息队列；
- 搜索服务；
- 外部协议兼容；
- migration；
- 事务；
- 数据恢复；
- 依赖服务不可用。

#### 输入

- 真实数据库连接；
- SQL/ORM 操作；
- 消息；
- HTTP/WebSocket 协议；
- migration 和 backup/restore 操作。

#### 判定

- 容器健康检查成功；
- schema 和 migration 正确；
- 事务和约束正确；
- 重复写入符合幂等要求；
- 依赖失败时产品正确失败；
- 测试结束容器被清理。

#### 为什么使用

mock 可能和真实数据库、队列行为不一致。Testcontainers 可以在隔离环境中运行真实依赖。

#### 为什么不作为所有项目默认工具

- 没有外部依赖的项目不需要；
- 依赖 Docker，运行成本高；
- 容器启动会降低普通提交测试速度；
- 容器通过不代表生产部署一定正确；
- 它不能替代业务断言。

建议放在 integration 或 release tier，而不是所有单元测试中。

---

### 6. k6：只用于性能、压力和 soak

#### 测试范围

- API 吞吐；
- Web 并发；
- SSE 连接；
- spike；
- stress；
- soak；
- 大数据量；
- 长时间运行资源泄漏。

#### 输入

- 并发用户数；
- 请求速率；
- 请求数据；
- 持续时间；
- 自定义业务指标。

#### 判定

```javascript
thresholds: {
  http_req_failed: ["rate<0.01"],
  http_req_duration: ["p(95)<500"],
}
```

必须提前声明：

- 最大错误率；
- p95/p99 延迟；
- 最大连接恢复时间；
- 最大资源占用；
- 最大事件遗漏数；
- 运行结束后资源清理要求。

#### 为什么使用

k6 将性能预算转换成明确的阈值和非零退出码，适合 CI/release gate。

#### 为什么不作为日常主框架

- 性能需要稳定环境和基线；
- 运行成本高；
- 普通功能测试不需要压测；
- 没有阈值时只有观测数据，没有 pass/fail；
- k6 不能判断产品业务状态是否正确。

---

### 7. OWASP ZAP：只用于动态 Web/API 安全层

#### 测试范围

- 常见 Web/API 安全问题；
- OpenAPI 入口；
- 认证后页面；
- 被动扫描；
- 隔离环境主动扫描；
- 风险规则门禁。

#### 输入

- 授权 URL；
- OpenAPI/GraphQL schema；
- 测试账号；
- 扫描规则；
- 风险阈值。

#### 判定

```text
高风险问题 > 0 → fail
中风险问题超过阈值 → fail
扫描了未授权生产目标 → harness failure
```

#### 为什么使用

它能补充代码测试难以发现的 HTTP 层安全问题，并可以输出报告和退出码。

#### 为什么不作为所有提交的默认测试

- 扫描时间较长；
- 需要可运行 Web 环境；
- 主动扫描可能影响测试数据；
- 不能理解业务角色和业务授权；
- 不能测试 ZaoFu EventLog、ControlledAction 或内部状态权威。

---

## 四、推荐的最终工具组合

| 测试层 | 必选/条件 | 推荐工具 | 主要职责 |
|---|---|---|---|
| 统一执行和基础测试 | 必选 | pytest | fixture、断言、单元、集成、恢复、安全 |
| 异步产品 | 条件 | pytest-asyncio | async API、worker、后台任务 |
| 结果和覆盖率 | 必选 | pytest-cov、JUnit XML | 结果聚合、覆盖盲区 |
| 复杂输入 | P1 | Hypothesis | 边界输入、状态序列、不变量 |
| Web 产品 | 有 Web 时 | Playwright | 浏览器用户旅程、DOM、trace |
| API 产品 | 有 OpenAPI 时 | Schemathesis | schema、负向输入、API 状态序列 |
| 多服务产品 | 有服务边界时 | Pact | Consumer/Provider 契约 |
| 数据库/队列 | 有真实依赖时 | Testcontainers | 隔离真实集成环境 |
| 故障恢复 | 长期运行产品 | pytest + subprocess + 自建 failpoint | 崩溃、重启、恢复、幂等 |
| 性能 | 发布阶段 | k6 | 延迟、吞吐、错误率、soak |
| Web/API 安全 | 发布/定期 | OWASP ZAP | 动态安全扫描 |
| 多版本安装 | 发布阶段 | CI matrix + uv | Python、extras、安装兼容性 |

---

## 五、不建议使用的方案

### 1. 不使用单一大型测试平台

原因：

- 无法同时覆盖 Python、API、浏览器、数据库、性能和安全；
- 会与 pytest、Playwright、CI、ZaoFu dev-verify 重复；
- 仍然需要自己编写产品 oracle；
- 增加第二套测试配置和报告体系；
- 可能隐藏真实的 Given/When/Then/Forbidden 判据。

更好的做法是建设一层薄的 ZaoFu TestKit：

```text
scenario
oracle
manifest
command runner
evidence collector
failpoint
process cleanup
side-effect spy
```

但测试执行仍交给 pytest、Playwright、Schemathesis 等专业工具。

### 2. 不把 Hypothesis 当成全部测试

它适合扩大输入空间，不适合替代：

- 固定回归 case；
- 产品验收 case；
- 浏览器用户旅程；
- 真实部署测试；
- 安全扫描。

### 3. 不把 Playwright 当成全部 E2E

浏览器只能看到用户表面，不能独立证明：

- 数据库状态；
- 后台任务；
- 外部副作用；
- EventLog；
- 恢复后状态；
- 权限内部实现。

### 4. 不对所有项目强制 Pact

Pact 只适合存在稳定 Consumer/Provider 边界的项目。单体、CLI、纯库项目强行使用只会增加契约维护成本。

### 5. 不对所有项目强制 Testcontainers

没有数据库、队列或外部服务的项目没有必要启动容器。容器应由产品能力矩阵决定。

### 6. 不在每次普通提交中运行 k6 和 ZAP

它们应放入：

- release；
- nightly；
- 定时稳定性；
- 安全专项；
- full-parity。

日常提交应优先运行快速、确定性、可复现的测试。

### 7. 不让生成代码的 Agent 自己决定“测试通过”

产品项目的实现 Agent 可以提供测试和证据，但最终通过条件应由：

- 独立测试进程；
- 结构化断言；
- 产品验收契约；
- 当前 commit；
- evidence ref/digest；
- 禁止副作用检查；

共同决定。

---

## 六、推荐的 ZaoFu 测试执行分层

### T0：每个产品任务都执行

```text
pytest
+ 项目 build/lint/typecheck
+ 项目 unit/component
+ acceptance/test matrix 闭包检查
+ 当前 commit 和 worktree 检查
```

### T1：按产品能力执行

```text
有 API       → Schemathesis
有 Web       → Playwright
有数据库     → Testcontainers
有多服务     → Pact
有长期进程   → subprocess/failpoint/recovery
```

### T2：合并前执行

```text
所有 mandatory acceptance
+ affected task commands
+ 关键 API/CLI/Web smoke
+ deterministic mock E2E
+ evidence/digest 校验
+ 未授权副作用检查
```

### T3：发布或定期执行

```text
k6 性能和 soak
+ ZAP 安全扫描
+ 安装/升级/回滚
+ 多版本 CI matrix
+ 真实 Provider canary
+ 资源泄漏和清理检查
```

---

## 最终选型

对通过 ZaoFu 跑出来的产品项目，推荐：

> pytest 作为主测试框架，Playwright、Hypothesis、Schemathesis、Pact、Testcontainers 按能力条件组合，k6 和 OWASP ZAP 放在发布/定期测试层；由 ZaoFu TestKit 负责统一场景、oracle、证据和门禁。

这套方案的关键不是工具数量，而是职责边界：

```text
产品项目测试契约：定义什么是正确
专业框架：生成输入并执行测试
ZaoFu TestKit：隔离环境、收集证据、绑定 commit
ZaoFu Kernel：执行状态、证据和交付门禁
```

因此，最终判定不能是“某个框架给了多少分”，而必须是：

```text
required case 全部通过
+ mandatory acceptance 全部有证据
+ 产品状态和外部副作用符合预期
+ 失败可复现
+ 恢复后与 baseline 一致
+ 没有未授权副作用和资源泄漏
```
