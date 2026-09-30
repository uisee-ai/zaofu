# ZaoFu-Product-TestKit

> 当前口径（2026-09-30）：本文件是通用产品测试合同；实施进度见 [Product_steps.md](Product_steps.md)，能力及待办见 [productOfzaofu_done_part.md](productOfzaofu_done_part.md)。要求、方法已实现、历史执行结果和单个产品验收分别记账。本次文档审核不运行产品测试，也不将产品 FAIL 当作框架未完成。真实 `/issue-merge`、Feishu 和远端写入不在本轮建设范围。

## 目标与适用范围

本文定义一套用于测试所有由 ZaoFu 生成、修改或交付的产品项目的通用方案。它不针对某个具体产品、语言或业务领域，也不评价 Agent 或模型能力；它测试的是产品项目在声明的需求和可观察契约下是否正确、可复现、可恢复、可交付。

产品项目可以是 CLI、库、HTTP/API 服务、Web 应用、后台 worker、桌面/设备服务或多模块系统。不同项目不执行完全相同的命令，而是使用统一测试域、产品能力矩阵和显式的 `not_applicable` 裁决。

### 通用闭环与真实产品 canary 的边界

本方案的“端到端可信闭环”首先由通用 TestKit 自己证明：使用版本化测试合同、最小参考产品 fixture、故障 fixture 和跨层 oracle，验证准入、执行、证据、失败分类、Issue/回归和交付门禁。它不以某个真实生成产品通过为前置条件。

真实生成产品属于兼容性 canary，并单独接受自身质量验收。机器结果使用 `tests/product_testkit/verdict.py` 的 `pass / fail / not_applicable / environment_failure / harness_failure / flaky / not_run`；准入错误记录为失败阶段和诊断，不新增 `admission_failure` 或 `product_failure` 枚举。环境不可用归 `environment_failure`，产品断言违反合同归 `fail`；runner 或证据错误归 `harness_failure`。产品失败不等于框架未完成，框架自身错误也不能推给产品；通用 fixture 通过不代表真实产品质量通过。

仓库内的独立自证入口是 `tests/product_testkit/test_reference_product_closure.py`：它动态创建临时 Git 产品，并覆盖通过、产品命令失败、验收/证据、交付就绪、恢复、并发、持久化、发布产物、安全报告和 Issue→merge 阻断。该用例用于证明 TestKit 闭环自身可构建、可判定、可留证，不用于宣称任何真实产品质量达标。

总体闭环为：

```text
固定产品 commit 与隔离环境
  → 读取产品测试 manifest、能力矩阵和验收矩阵
  → 执行适用的测试域
  → 收集断言、状态、日志、报告和副作用证据
  → 生成统一 verification-result / Product Acceptance Report
  → 绑定 candidate、target commit 和 artifact digest
  → 进入合并或发布门禁
```

测试通过不是“命令执行完”或“页面看起来正常”，而是：

```text
required case 全部通过
+ mandatory acceptance 全部有证据
+ 产品状态、输出和外部副作用符合契约
+ 失败可复现且结果绑定当前 commit
+ 恢复后与无故障 baseline 一致
+ 没有未授权副作用、资源泄漏或测试基础设施错误
```

---

## 一、统一测试契约和输入准备

### 1. 产品测试 manifest

每个生成产品项目在测试前必须提供或由 ZaoFu 适配阶段生成 `product-test-manifest.json`。manifest 是测试入口清单，不是测试结果。至少包含：

```json
{
  "schema_version": "product-test-manifest.v1",
  "project_type": "cli|library|api|web|worker|fullstack|other",
  "source_root": "...",
  "target_commit": "...",
  "setup": [{"id": "install", "command": "...", "timeout_seconds": 900}],
  "commands": [
    {
      "id": "unit-tests",
      "command": "...",
      "tier": "unit|integration|e2e|security|perf|release",
      "acceptance_ids": ["AC-1"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 900
    }
  ],
  "entrypoints": [
    {
      "entrypoint_id": "product-api",
      "start_command": "...",
      "health_check": "...",
      "owner": "product-test",
      "timeout_seconds": 30
    }
  ],
  "api_contracts": [
    {
      "id": "openapi-contract",
      "entrypoint_id": "product-api",
      "schema": "openapi.json",
      "base_url": "http://127.0.0.1:8000",
      "tool_version": "4.26.1",
      "tier": "integration",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-07"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 60,
      "phases": ["examples", "coverage", "fuzzing"],
      "mode": "all",
      "checks": ["not_a_server_error", "status_code_conformance", "response_schema_conformance", "negative_data_rejection"],
      "workers": 1,
      "seed": 1729,
      "max_examples": 25,
      "max_time_seconds": 30,
      "request_timeout_seconds": 3,
      "request_retries": 0
    }
  ],
  "browser_tests": [
    {
      "id": "browser-smoke",
      "entrypoint_id": "product-web",
      "spec": "tests/e2e/smoke.spec.ts",
      "base_url": "http://127.0.0.1:8000",
      "image": "mcp/playwright:latest",
      "tier": "e2e",
      "acceptance_ids": ["AC-2"],
      "domain_ids": ["P-08"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 300,
      "install_timeout_seconds": 180,
      "install_browser": false
    }
  ],
  "dependency_containers": [
    {
      "id": "db",
      "image": "postgres:16",
      "command": "postgres",
      "health_command": "pg_isready",
      "ports": ["15432:5432"],
      "timeout_seconds": 60,
      "mandatory": true
    }
  ],
  "mock_services": [
    {
      "id": "payment-provider",
      "routes": [
        {"method": "GET", "path": "/health", "status": 200, "body": {"ok": true}},
        {"method": "POST", "path": "/publish", "status": 200, "body": {"accepted": true}, "failures_before_success": 1, "failure_status": 503}
      ]
    }
  ],
  "performance_tests": [
    {
      "id": "load-smoke",
      "script": "tests/load.js",
      "image": "grafana/k6:1.5.0",
      "tier": "perf",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-14"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 120,
      "thresholds": [
        {"metric": "http_req_duration", "statistic": "p(95)", "operator": "<", "limit": 500}
      ]
    }
  ],
  "release_artifacts": [
    {
      "id": "python-wheel",
      "path": "dist/product-1.0-py3-none-any.whl",
      "kind": "wheel",
      "tier": "release",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-15"],
      "mandatory": true,
      "deterministic": true
    }
  ],
  "security_scans": [
    {
      "id": "zap-baseline",
      "tool": "zap",
      "command": "zap-baseline.py -t http://127.0.0.1:8000 -J evidence/zap.json",
      "report": "evidence/zap.json",
      "max_risk": 0,
      "tier": "security",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-11"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 180
    }
  ],
  "release_scenarios": [
    {
      "id": "install-upgrade-rollback",
      "install_command": "uv pip install dist/product.whl --target .release-env",
      "upgrade_command": "uv pip install --upgrade dist/product.whl --target .release-env",
      "rollback_command": "uv pip install dist/product-previous.whl --target .release-env",
      "tier": "release",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-15"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 300
    }
  ],
  "recovery_scenarios": [
    {
      "id": "restart-worker",
      "command": "python tests/recovery_worker.py state.json ready",
      "state_path": "state.json",
      "ready_path": "ready",
      "tier": "integration",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-12"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 60
    }
  ],
  "concurrency_scenarios": [
    {
      "id": "parallel-workers",
      "commands": [
        "python tests/worker.py shard-a",
        "python tests/worker.py shard-b"
      ],
      "tier": "integration",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-13"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 60
    }
  ],
  "persistence_scenarios": [
    {
      "id": "migration-and-restore",
      "commands": ["python tests/db_migrate.py", "python tests/db_verify.py", "python tests/db_restore.py"],
      "tier": "integration",
      "acceptance_ids": ["AC-1"],
      "domain_ids": ["P-09"],
      "mandatory": true,
      "deterministic": true,
      "timeout_seconds": 180
    }
  ],
  "capabilities": {},
  "budgets": {},
  "not_applicable": {},
  "runtime_paths": [],
  "cleanup": []
}
```

`runtime_paths` 是产品测试运行期间允许在产品 worktree 中产生、并在重复执行
时保留的相对文件路径（例如幂等测试的状态标记）。它不是通配符；未列入
该清单的未跟踪文件会在目标 commit 校验阶段被拒绝，避免未提交源码混入
测试结果。更推荐让状态和证据写到隔离的 output root。

manifest 只接受本方案列出的顶层字段；拼写错误或尚未实现的字段会在准入时
直接拒绝，不能依靠“忽略未知字段”继续执行。

清单校验必须检查：

- `source_root`、`target_commit` 与 ZaoFu run contract 一致；
- 每条命令有稳定唯一 `id`、准确命令文本、超时、tier 和 acceptance 关联；
- 命令不存在、不可执行、重复 ID 或悬空 acceptance 引用时拒绝进入测试；
- `not_applicable` 必须写明能力判断、理由和确认人/来源，不能以缺少命令代替；
- 服务入口、health check、数据依赖、凭据来源和 cleanup 方法必须明确；
- 性能预算、安全阈值和长期运行时长必须在对应 tier 中声明。

`entrypoints[].health_check` 是独立、shell-free 执行的探针命令，而不是仅支持 HTTP URL；因此 HTTP 服务、CLI、worker、文件就绪标记和设备代理可以使用同一生命周期契约。`entrypoint_id` 必须稳定且唯一，`start_command`、`health_check`、`owner` 均不得为空，`timeout_seconds` 必须是正的有限数。该结构与 ZaoFu 的 `product_acceptance_spec.v1` 使用相同的四个核心字段，TestKit 仅增加机械执行所需的 timeout。

`release_artifacts[]` 声明 P-15 必须存在的发布输出。runner 只接受产品 worktree 内的 regular file，按 `kind` 校验扩展名和非空内容，随后将文件复制到本次 evidence root 并重新计算 SHA-256；源文件缺失、为空、类型不符或复制前后 digest 不一致均为产品发布失败，不能由普通 build 命令的绿色结果覆盖。安装、升级、回滚仍应由独立的 `tier: release` 命令声明并保留其 receipt；artifact 检查本身不授权 merge/push。

`security_scans[]` 声明 P-11 的 ZAP JSON 报告。runner 先执行产品隔离环境中声明的扫描命令，再复制并解析报告；退出码、报告完整性、扫描工具错误和 `max_risk` 共同裁决：超出风险阈值是产品 `fail`，扫描器不可用/超时是 `environment_failure`，缺报告、格式错误或报告与命令结果矛盾是 `harness_failure`。扫描命令必须只指向项目拥有的隔离目标，不能把 ZAP 扫描未授权的生产 URL。

`release_scenarios[]` 将安装、升级、回滚声明为一个有序 P-15 测试单元。三个阶段分别生成 receipt；任一阶段失败会停止后续阶段并保留失败阶段，不能把安装成功当成完整发布通过。命令必须在隔离环境中操作临时安装目录/数据库，产品项目应在命令中声明版本检查、迁移验证和 health smoke；TestKit 只负责顺序、退出码和证据聚合，不替产品猜测升级语义。

`recovery_scenarios[]` 和 `concurrency_scenarios[]` 分别声明 P-12/P-13 的产品级进程场景。恢复场景必须给出产品根目录内的状态文件和 ready 文件；TestKit 首次启动后读取 baseline，向进程组发送硬停止信号，再启动同一命令并比较恢复状态，最终强制清理。并发场景必须给出至少两个 shell-free 命令；TestKit 并行启动、限制墙钟时间、逐进程收集退出码并清理，产品测试命令负责写入业务结果，随后使用唯一键、状态单调性或项目自有 oracle 判定。命令不可执行/超时归 `environment_failure`，进程正常退出但业务结果错误归产品 `fail`；每个场景及其日志、快照和 receipt 都进入 artifact index。两类场景均为可选能力，缺少长期进程或并发要求的项目必须在 `not_applicable` 中说明原因。

`persistence_scenarios[]` 声明 P-09 的迁移、约束、备份/恢复或一致性命令序列。TestKit 按列表顺序逐条执行、在首个失败处停止并保留每步 receipt；命令本身必须连接隔离数据库/文件存储并执行具体状态断言（例如 schema 版本、事务回滚、重复写幂等和恢复后快照等价）。启动依赖容器由 `dependency_containers[]` 管理；不可执行/超时归 `environment_failure`，命令退出非零且环境正常归产品 `fail`。TestKit 不臆测数据库语义，也不会把“迁移命令执行成功”当成数据正确性的充分证据。

所有测试单元的 `mandatory` 字段决定产品门禁：`mandatory: true` 的产品失败、缺失或未运行会阻断 verdict；`mandatory: false` 的产品断言失败会保留在报告和 JUnit 中但不阻断必需闭环。无论是否 mandatory，TestKit 自身错误、工具不可用、超时、证据损坏和未授权副作用都不能被忽略，分别输出 `harness_failure` 或 `environment_failure`。

统一 runner 的固定顺序是：

```text
target/source 准入
  → setup
  → 启动所有声明 entrypoint
  → health 命令成功
  → 执行所选产品测试命令
  → 执行所选 OpenAPI 契约并解析 Schemathesis 原始 JSON
  → 在 Docker 中执行所选 Playwright browser_tests 并解析 JSON reporter
  → 检查所选 release_artifacts 并复制/绑定 digest
  → 执行所选 security_scans 并解析 ZAP 报告
  → 执行 release_scenarios 的 install → upgrade → rollback
  → 执行所选 recovery_scenarios 和 concurrency_scenarios
  → 执行所选 persistence_scenarios
  → 停止 TestKit 所有服务进程组
  → 停止并移除所有声明的 dependency_containers
  → 执行顶层 cleanup
  → 收集 service/command/cleanup receipt 和 digest
```

服务未启动、health 超时或健康后自行退出均不能通过；缺少可执行程序归为 `ENVIRONMENT_FAILURE`，证据文件冲突等 TestKit 自身错误归为 `HARNESS_FAILURE`。无论产品测试成功或失败，已启动服务都必须进入 `finally` 清理，顶层 cleanup 必须在服务停止之后运行。

`api_contracts[]` 是一等测试单元，而不是预先拼好的任意 shell 字符串。它必须引用已声明 entrypoint 和 acceptance，覆盖 `P-07`，固定 Schemathesis 精确版本、phase、mode、checks、单 worker、seed、用例/时间预算、请求超时和外层墙钟预算。当前确定性门禁只准入 `examples`、`coverage`、`fuzzing`；会发出链式写操作的 `stateful` 必须在后续隔离数据/cleanup profile 中显式接入，不能继承默认值悄悄执行。

`browser_tests[]` 同样是一等测试单元，必须引用 entrypoint、产品内相对 spec、owner-facing `base_url`、Docker image、`e2e` tier、acceptance/domain 和超时。TestKit 负责生成 host-network、宿主 UID/GID、项目与 evidence 挂载、浏览器路径和 bounded install（若 `install_browser=true`）的命令；产品 spec 负责 DOM、交互、network、console、截图/trace 与刷新后的业务断言。没有测试、全部跳过、runner error、Docker/外层超时或 JSON 缺失不能变成绿色；只有真实 journey 通过且 evidence 已索引才可 `PASS`。

`dependency_containers[]` 是 P-09/P-10/P-12/P-13 的可选依赖声明，不是业务测试本身。每项固定 `id`、镜像、单行启动命令、单行容器内 health 命令、超时和可选 `host:container` 端口映射。runner 使用一次性 `docker run --rm`，在产品命令前启动并健康检查，在服务停止后强制移除；同时把容器名和 ID 以 `ZF_CONTAINER_<ID>_NAME/ID` 注入产品测试环境。启动/健康失败为 `ENVIRONMENT_FAILURE`，证据或清理失败为 `HARNESS_FAILURE`，所有容器日志、health 输出、receipt 和移除结果进入 artifact index。没有声明真实依赖的项目不会启动容器。

`mock_services[]` 是 P-10 的可选 localhost fake 服务声明。每项固定唯一服务 ID 和不可重复的 HTTP method/path 路由，响应状态/body 以及可选的有限 transient failure 次数；runner 启动线程隔离的 fake、注入 `ZF_MOCK_<ID>_BASE_URL`，在产品命令完成后停止并保存请求（仅保存脱敏 header 和 body digest）及 receipt。未声明 mock 服务的项目不增加 fake 依赖；路由错误、重试超限和产品对错误响应的处理仍由产品测试命令断言，TestKit 不把“收到响应”当作业务成功。

`performance_tests[]` 是 P-14 的可选 k6 测试单元。每项必须声明产品内相对脚本、镜像、`perf` tier、acceptance/domain、有限超时和至少一个阈值；runner 在 Docker host network 中执行并保存原始 summary。k6 旧版 `--summary-export` 的 threshold 布尔值表示“是否越过阈值”，TestKit 会规范化该语义并与统计值、退出码对账；缺少样本/阈值、summary 损坏或退出码与阈值矛盾时为 `harness_failure`，阈值真实不满足且退出码为 99 时才是性能 `fail`。

### 2. 测试输入类别

每个适用测试域至少从以下输入类别中选择并在 case 中记录：

- 正常输入：典型有效请求、文件、命令或用户动作；
- 边界输入：空值、最小/最大值、超长字符串、Unicode、时区和大批量；
- 非法输入：缺字段、错误类型、错误格式、过期身份和不存在资源；
- 重复输入：相同请求、相同消息、相同 webhook、重复提交；
- 乱序/并发输入：并行请求、消息乱序、多个 worker 或多客户端；
- 故障输入：超时、进程退出、依赖不可用、网络延迟、磁盘/权限错误；
- 不可信输入：用户文本、Issue、附件名、Provider 输出和第三方响应。

### 3. 单个 case 的标准结构

所有产品测试 case 必须具备以下字段或等价记录：

```text
case_id       稳定、唯一、可用于回归
capability    所属产品能力
acceptance    所属验收标准
Given         初始 commit、环境、数据、身份和依赖
When          准确命令、请求、用户动作或故障注入
Then          必须出现的输出、状态和业务结果
Forbidden     绝对不能出现的状态、错误、文件或副作用
Evidence      报告、日志、快照、trace、digest 和复现命令
```

---

## 二、通用结果裁决规则

### 1. 证据优先级

按以下顺序裁决，低优先级证据不能推翻高优先级事实：

1. 产品真实状态（数据库、文件、队列、持久化快照）；
2. API/CLI 的结构化输出、退出码和响应状态；
3. 产品验收断言和测试报告；
4. 外部交互记录、调用计数和副作用审计；
5. 日志、trace、network/HAR、浏览器 DOM；
6. 截图、自然语言摘要和人工观察。

### 2. PASS、FAIL、NOT_APPLICABLE 和 HARNESS_FAILURE

一个 case 只有同时满足以下条件才是 `PASS`：

- 测试动作和环境初始化成功；
- `Then` 中全部必需断言成立；
- `Forbidden` 中所有禁止行为均未发生；
- 结果绑定本次 `target_commit`、run/candidate 和测试输入；
- 必需 evidence 存在、可读取且 digest 正确；
- 无未分类错误、超时、残留进程、容器、端口、锁或临时目录。

`FAIL` 只表示产品行为违反契约。以下情况必须标记为 `HARNESS_FAILURE` 或 `ENVIRONMENT_FAILURE`，不能直接算产品失败：Docker/浏览器不可用、依赖安装失败、workdir 不可写、命令缺失、HEAD 与 target commit 不一致、测试服务未能启动、runner 工具版本错误或清理失败。

`NOT_APPLICABLE` 必须经过 manifest 能力裁决，并保存理由；没有声明的能力不能默认为通过。

`FLAKY` 必须由至少两次可比较的独立执行结果得出，而不是由一次测试主观声明。可比较条件包括相同 `run_id`、candidate、`target_commit` 和完整 `command_plan`（命令 ID + digest）。每次执行必须使用独立 evidence output；第一次 `FAIL`、后一次 `PASS`，或反向变化时，相关命令进入 `flaky_commands`，总 verdict 固定为 `FLAKY`，JUnit 保持红色。环境/服务/harness 结果漂移继续按环境或 harness 失败处理，不能误标成产品 flaky。

CLI 使用方式：

```text
python -m tests.product_testkit ... --output-dir attempt-1
python -m tests.product_testkit ... --output-dir attempt-2 \
  --prior-result attempt-1/verification-result.json
```

`--prior-result` 可以重复提供。TestKit 会把前次结果复制到当前 evidence root、生成 `product-stability-report.v1` 并加入 artifact digest 索引；它不会在同一个污染目录中偷偷重试产品命令。

需要一次性验证所有 CI 层级时使用 `--ci-matrix`。它按 T0、T1、T2、T3 运行累计选择，每层写入独立目录并生成 `ci-matrix.json`；聚合 verdict 按 `harness_failure > environment_failure > flaky > not_run > fail > pass` 取最高风险，任一层失败都不会被后续层覆盖。该命令只执行产品测试和证据收集，不执行 merge/push。

通用 TestKit 可以从 ZaoFu 仓库外调用，产品项目只需提供自己的路径和 manifest，不需要把测试代码复制进产品仓库。例如：

```bash
uv run --project /home/uisee/zaofu_v7 python /home/uisee/zaofu_v7/scripts/product-testkit.py \
  --manifest /path/to/product/product-test-manifest.json \
  --project-root /path/to/product \
  --output-dir /path/to/product/.testkit-output \
  --run-id product-run-<id> \
  --ci-matrix
```

该调用只在产品项目 worktree 中读取和执行 manifest 声明的测试，并在 output 目录写入证据；TestKit 不执行 `git push`、`git merge` 或其他远端写操作。真实产品缺少 manifest、命令或验收输入时，调用仍会返回结构化准入/环境/harness 失败，这正是闭环应保留的终态证据。

### 3. 统一结果文件

每条命令至少生成一份 command receipt，包含命令 digest、工作目录、环境摘要、开始/结束时间、退出码、超时、stdout/stderr 路径和 artifact refs。最终生成：

- `verification-result.v1`：单个 task/命令的机器判定；
- `schemathesis/<contract-id>/result.json`：P-07 必需的原始机器报告；
- `schemathesis/<contract-id>/junit.xml`：P-07 的 namespaced 辅助报告，不覆盖 TestKit 聚合 JUnit；
- `product-stability-report.v1`：同一身份与命令计划的跨尝试稳定性判定；
- `product_acceptance_report.v1`：所有 mandatory journey/assertion 的汇总；
- JUnit XML 或 JSON：供 CI 聚合；
- evidence sidecar：大日志、截图、trace、快照和性能/安全报告。

---

## 三、产品项目通用测试域清单

以下 P-01 至 P-18 是统一测试域。按 `planner.py::CORE_DOMAINS`，P-01～P-05、P-17、P-18 对所有项目适用；其余按能力/测试单元选择。显式 false 的执行能力须给出 `not_applicable` 理由；未声明能力在当前 planner 中不会自动选中，但这不证明产品没有该能力，接入方仍须补齐能力调查。

### P-01 项目身份、代码版本和工作树

**测试目标**：证明测试对象就是 ZaoFu 声明的产品项目和候选版本。

**测试方法与工具**：

- 读取 ZaoFu run contract、candidate manifest 和产品 manifest；
- 使用 `git rev-parse --verify <rev>^{commit}` 将 `HEAD` 和 `target_commit` 解析为规范对象 ID，再用 `git diff --quiet --no-ext-diff <resolved-target> --` 拒绝已暂存或未暂存的 tracked 漂移；
- 执行前通过 Git porcelain 检查未跟踪文件，只允许 manifest sidecar 和显式 `runtime_paths`；普通未跟踪源码不会被默认放行。ignored 文件不在该检查的完整保证内；严格隔离仍需独立 worktree/环境，证据建议放在产品树外；
- 检查 source root、candidate worktree、target commit、project/state dir 和 artifact ref；
- 在测试开始和结束各记录一次 commit，防止测试期间代码漂移。

**输入**：run ID、project ID、source root、candidate ref、target commit、state dir。

**通过判据**：执行目录与声明目录一致；执行前 HEAD、tracked diff 及未跟踪白名单满足合同；产品命令、服务清理和顶层 cleanup 后再次核对 HEAD 与 tracked diff。执行后允许测试生成物，不把它们全部当成源码漂移；ignored 输入和跨目录副作用须由隔离环境及专门断言补证。身份检查不是文件系统沙箱。

**清单与证据**：

- [ ] run contract 和 manifest 可解析；
- [ ] source/candidate root 在允许边界内；
- [ ] HEAD 与 target commit 一致；
- [ ] 测试期间没有代码漂移；
- [ ] 保存 `run-contract.json`、git 输出和环境摘要。

### P-02 隔离环境、依赖和初始化

**测试目标**：在可复现、无外部污染的环境中运行产品。

**测试方法与工具**：

- 在一次性 worktree 和 state/data/temp 目录执行 setup/install；
- 使用项目声明的 `uv`、npm/pnpm、Go、Cargo、Docker 或安装脚本；
- 固定锁文件、运行时版本、环境变量和时区；
- 使用 hermetic fake service 或 Testcontainers 提供依赖；
- 用 cleanup 脚本检查跨目录写入、遗留进程、容器、端口和临时文件。

**输入**：干净 worktree、锁文件、依赖版本、测试凭据、空数据库/文件目录。

**通过判据**：setup 退出码为 0；所有依赖版本满足声明；禁止访问生产或用户目录；初始化结果可重复；清理后无资源泄漏。

**清单与证据**：

- [ ] 安装/初始化命令存在且未被改写；
- [ ] 依赖、Python/Node/Go/Rust/Docker 版本已记录；
- [ ] 测试目录外没有写入；
- [ ] setup 日志、退出码和环境快照已保存；
- [ ] 失败能区分环境失败与产品失败。

### P-03 构建、编译和静态检查

**测试目标**：确认候选代码能够按项目声明构建，并通过静态质量约束。

**测试方法与工具**：

- 逐条运行 manifest 中的 build、compile、lint、format-check、typecheck 命令；
- Python 常用 pytest/ruff/mypy，Node 使用 npm test/lint/typecheck，Go 使用 `go test`/`go vet`，Rust 使用 `cargo test`/`cargo clippy`；实际命令以 manifest 为准；
- 禁止把多个命令拼接成一个无法追踪的 shell 字符串；每条命令独立记录 receipt。

**输入**：当前 target commit、锁文件、编译配置、支持的运行时矩阵。

**通过判据**：所有 mandatory 命令退出码为 0；生成物存在且可读取；没有编译错误、lint/type 错误或未声明修改；非 mandatory 命令失败要按策略记录而不能冒充通过。

**清单与证据**：

- [ ] 每条命令有稳定 ID 和 acceptance 关联；
- [ ] 退出码、stdout/stderr 和命令 digest 已保存；
- [ ] 构建 artifact 的 checksum 与 commit 绑定；
- [ ] 编译/静态检查失败已分类。

### P-04 单元、组件和本地逻辑

**测试目标**：验证不依赖完整部署的业务逻辑、边界和错误处理。

**测试方法与工具**：

- 使用产品自己的测试框架：pytest、Jest/Vitest、JUnit、Go test、Cargo test 等；由 ZaoFu runner 统一调用；
- 使用 pytest 参数化或等价机制覆盖正常、边界、非法、空和重复输入；
- 使用 Hypothesis（或目标语言等价 property-based 工具）生成 Unicode、极值、随机结构和状态序列；
- 对每个已知 bug 固化最小回归 case。

**输入**：函数参数、配置、文件、消息、用户数据、随机生成的合法/非法样例。

**通过判据**：mandatory case 全部通过；预期异常确实发生；生成输入始终满足声明不变量；失败可被缩减并复现；报告映射到 acceptance ID。

**清单与证据**：

- [ ] 正常/边界/非法/重复输入均有覆盖；
- [ ] 核心不变量在每次生成序列后检查；
- [ ] JUnit/JSON、失败 case、覆盖率（若该 tier 要求）已保存；
- [ ] 不把模型输出或主观评分当作通过依据。

### P-05 需求—能力—验收—测试闭包

**测试目标**：防止需求、能力或验收标准没有任何可执行测试。

**测试方法与工具**：

- 读取 `source-inventory`、`capability-matrix`、`acceptance-matrix`、`test-matrix` 和 `task-map`；
- 用 ZaoFu matrix admission 或等价校验建立 capability → task → acceptance → command → evidence 图；
- 检查 blocking 能力、mandatory journey、assertion、command ID 和 artifact ref 的双向引用。

**输入**：三类矩阵、task contract、manifest、产品验收 spec。

**通过判据**：每个 blocking capability 至少有 task、acceptance 和测试命令；每个 mandatory acceptance 有可执行命令和结果；没有悬空、重复、越界或只声明不执行的 ID；`not_applicable` 有理由。

**清单与证据**：

- [ ] 矩阵均为当前 target commit 版本；
- [ ] 闭包校验为 0 个 error；
- [ ] 每个 acceptance 有至少一个 required evidence；
- [ ] 保存矩阵快照、闭包图和 admission 报告。

### P-06 CLI 表面

**适用条件**：项目提供命令行入口、脚本入口或 stdin/stdout 协议。

**测试方法与工具**：

- 使用 pytest subprocess、目标语言 CLI harness 或 shell-free `exec` 调用；
- 覆盖 `--help`、正常参数、缺失参数、错误类型、stdin、stdout/stderr、退出码、配置覆盖和重复执行；
- 对生成文件、目录权限、路径边界和环境变量做前后快照。

**输入**：正常命令、空 stdin、非法参数、超长值、重复命令、过期 token、越界路径。

**通过判据**：有效输入输出与 CLI 契约一致；非法输入返回预期非零码和可定位错误；重复操作符合幂等定义；未授权命令不产生文件、网络或 git 副作用；stdout 不混入 secret。

**工具与证据**：pytest/subprocess、项目 CLI 测试框架；保存命令 transcript、退出码、stdout/stderr、文件快照和副作用 spy。

### P-07 HTTP/API 表面

**适用条件**：项目暴露 HTTP、GraphQL 或其他可请求 API。

**测试方法与工具**：

- 用 pytest/httpx 或项目 API harness 执行固定契约 case；
- 已有 `api_contracts[]` 适配器按固定 Schemathesis 版本执行 `examples/coverage/fuzzing`；`stateful` 不在当前准入 phase 集合。GraphQL 或写操作序列须由产品专用 `commands[]`/后续隔离 profile 显式接入，不能声称现有 OpenAPI 适配器已自动覆盖；
- 测试认证、授权、错误码、schema、content type、分页、过滤、幂等和并发；
- 对关键写操作回查数据库/文件状态，不只验证 HTTP 200。

**输入**：schema 生成请求、缺字段/错误类型、极值、错误 token、错误 scope、重复 create/update/delete 和多步资源操作。

**通过判据**：响应状态码、headers、body schema 符合契约；非法输入被拒绝；不出现未分类 5xx；未授权请求成功次数为 0；多步操作后的真实状态符合 acceptance；失败序列可复现。

**工具与证据**：pytest/httpx、Schemathesis、OpenAPI/GraphQL schema；保存 request/response、schema、服务日志、状态快照和复现命令。

**Schemathesis 机械判据**：不能使用 `exit_code == 0` 单独给绿。只有外层进程完整、实际版本等于 manifest、JSON `complete=true`、`operations.selected>0`、`operations.tested>0`、所请求 phase 成功、内部/外部退出码一致、`failures=[]`、`errors=[]` 且严格 warning policy 满足时才是 `PASS`。完整报告中的明确 check finding 是产品 `FAIL`；网络/请求超时是 `ENVIRONMENT_FAILURE`；版本漂移、报告缺失/损坏、内部外部结果矛盾和配置 warning 是 `HARNESS_FAILURE`；零 operation 是 `NOT_RUN`。原始 JSON、Schemathesis JUnit、工具版本探针、stdout/stderr 和两份 command receipt 都必须进入 artifact digest 索引。

### P-08 Web 和浏览器用户旅程

**适用条件**：项目有浏览器 UI、SSR 页面或需要真实浏览器交互。

**测试方法与工具**：

- 使用 Docker `mcp/playwright:latest` 中的 Playwright；API/UI 监听 `0.0.0.0`，容器使用 host networking 和隔离 BrowserContext；不默认安装宿主浏览器；
- 对每个 mandatory journey 写出登录/身份、动作顺序、等待条件、业务断言和刷新后的断言；
- 同时检查 API/network、产品真实状态、DOM、console 和错误/空/loading 状态；
- 使用 trace、screenshot 和 HAR 辅助复现，截图不能单独作为通过证据。

**输入**：测试账号、页面入口、表单值、正常/边界/非法数据、刷新、断线、重复点击和多项目切换动作。

**通过判据**：页面动作触发预期 API；业务状态确实改变；DOM 与 API/数据库状态一致；刷新或重连后状态保持；关键断言通过；console/network 无未分类错误；浏览器或 Docker 不可用时标记 harness failure。

**工具与证据**：Playwright Test、Docker；保存 DOM 断言结果、trace、截图、HAR、network/console 日志和退出码。

### P-09 数据库、文件和持久化

**适用条件**：项目使用数据库、文件存储、缓存、队列持久化或 schema migration。

**测试方法与工具**：

- 使用项目 migration/backup/restore 命令；
- 有真实依赖时用 Testcontainers 启动一次性 PostgreSQL/MySQL/Redis/消息队列等；
- 测试初始化、约束、事务、读写一致性、重复写、并发写、旧 schema 迁移、备份恢复和损坏处理；
- 对关键操作保存操作前后快照并比较不变量。

**输入**：空库、已有旧版本 schema、合法/非法记录、重复事务、并发写、备份文件和损坏数据。

**通过判据**：容器健康且版本正确；migration 可前进并按声明回滚；约束拒绝非法数据；事务失败不留下半写入；重复操作符合幂等要求；恢复后状态与 baseline 一致；容器、卷、网络和临时文件清理完毕。

**工具与证据**：Testcontainers、项目 DB 工具、SQL/ORM 测试；保存 migration 日志、schema diff、数据快照、容器日志和清理结果。

### P-10 外部集成和服务契约

**适用条件**：项目调用第三方 API、独立服务、消息系统或需要服务间协议。

**测试方法与工具**：

- 默认使用 fake/mock server 固定成功、错误、超时、限流、空响应和畸形响应；
- 存在稳定 Consumer/Provider 边界时使用 Pact：Consumer 记录真实 interaction，Provider 在指定 state 重放验证；
- 需要真实协议时结合 Testcontainers；网络延迟、断连和重置可使用 Toxiproxy（若项目环境允许）；
- 检查重试上限、退避、熔断、幂等和 secret 脱敏。

**输入**：合法请求、错误状态码、超时、重复响应、乱序消息、过期凭据、协议版本差异和第三方数据污染。

**通过判据**：请求和响应符合契约；Provider state 可建立；依赖失败不会伪造业务成功；重试不超过声明上限；重复外部写入次数不超过幂等要求；凭据不出现在日志或 artifact。

**工具与证据**：mock server、Pact、Testcontainers、Toxiproxy；保存 interaction/pact、mock 调用计数、重试记录、脱敏日志和最终状态。

### P-11 安全、认证、授权和不可信输入

**测试目标**：证明产品不会因不可信输入产生越权、注入、secret 泄漏或未授权副作用。

**测试方法与工具**：

- 使用 pytest 固定角色、资源 scope、路径和命令注入 case；
- 对 HTTP/API 使用 Schemathesis 的负向输入和项目授权测试；
- 对 Web/API 隔离环境使用 OWASP ZAP Baseline/Automation Framework；
- 扫描 secret、日志、artifact、文件名、Issue/Provider 文本到 shell、文件、数据库、git 和远端 API 的所有 sink；
- 用 side-effect spy 统计 git commit/merge/push、shell、远程写操作和任意路径写入。

**输入**：未认证请求、低权限 token、越界 project/path、命令片段、SQL/模板/XSS payload、恶意 Issue、附件名、Provider 输出和伪造 webhook。

**通过判据**：未授权成功次数为 0；越界路径写入次数为 0；高风险 ZAP 告警为 0，中风险不超过项目阈值；secret 扫描无命中；恶意文本只作为数据，不触发命令或远端写入；拒绝结果可定位且不污染状态。

**工具与证据**：pytest、Schemathesis、OWASP ZAP、secret scanner、side-effect spy；保存扫描报告、拒绝日志、请求样例、调用计数和脱敏证据。

### P-12 故障、崩溃和恢复

**适用条件**：项目有长期进程、后台任务、持久化状态、队列或可恢复工作流。

**测试方法与工具**：

- 用 pytest + subprocess 启动、停止、杀死和重启服务；
- 使用命名 failpoint 或 mock 在提交边界注入故障：写入前、写入后投影前、artifact 后、外部副作用后、迁移中间和切换后；
- 按 baseline、注入故障、重启、恢复、清理顺序执行；
- 测试超时、进程退出、依赖不可用、断电式中断、stale worker 和重复唤醒。

**输入**：固定任务/数据、故障点名称、进程信号、依赖断开、延迟、重复消息和旧版本状态。

**通过判据**：重启后状态不丢失、不倒退、不重复；已成功的外部副作用不重复；未完成步骤可继续；stale 结果不能覆盖新状态；最终状态与无故障 baseline 一致；崩溃点和恢复步骤可复现。

**工具与证据**：pytest、subprocess、failpoint、Testcontainers/Toxiproxy（按需）；保存故障点、进程日志、重启命令、前后快照、副作用计数和差异报告。

### P-13 并发、顺序和幂等

**测试目标**：验证多请求、多 worker、重复 webhook 和乱序输入下，业务状态仍满足不变量。

**测试方法与工具**：

- 用 pytest 多进程/线程 fixture、asyncio 和项目并发 harness；
- `pytest-xdist` 只作为加速器，不作为并发正确性的证明；共享端口、state dir 或 tmux 的测试必须隔离或串行；
- 使用 Hypothesis stateful testing 生成 create/update/retry/recover/replay 序列；
- 对锁、事务、去重键、版本号和状态单调性做断言。

**输入**：相同请求并发提交、乱序事件、多个 worker、重复 callback、过期 attempt/generation 和网络重试。

**通过判据**：不会产生重复业务对象；状态转换单调且符合规则；相同 idempotency key 至多一次外部副作用；旧版本不能覆盖当前版本；所有 worker 最终收敛；失败序列可缩减为可复现 case。

**工具与证据**：pytest asyncio/process fixtures、Hypothesis、项目锁/事务工具；保存调度顺序、事件序列、调用计数、最终快照和最小失败序列。

### P-14 性能、容量和长期稳定性

**适用条件**：项目有性能预算、并发用户、批量处理或长期运行要求。

**测试方法与工具**：

- 使用 k6 或项目性能工具定义 smoke、load、stress、spike 和 soak 场景；
- 测试 API 吞吐、Web 并发、SSE/WebSocket 连接、批量数据和后台队列；
- 同时采集 CPU、内存、文件句柄、端口、容器、队列积压和错误日志；
- 在 manifest 中预先声明阈值，禁止只采集曲线而不给 pass/fail。

**输入**：并发用户数、请求速率、数据规模、持续时间、连接断开/重连和业务流量模型。

**通过判据**：例如 `http_req_failed rate < 1%`、p95/p99 延迟低于项目预算、业务错误率和积压低于阈值、资源不持续增长；阈值失败返回非零退出码；结束后无进程、端口、句柄或容器泄漏。

**工具与证据**：k6、系统监控和项目 metrics；保存脚本、阈值配置、原始结果、资源曲线、soak 日志和清理报告。k6 仅在发布/定期 tier 运行，不替代功能测试。

### P-15 打包、安装、部署、升级和回滚

**适用条件**：项目生成 wheel/sdist/npm 包、容器、安装包或部署物。

**测试方法与工具**：

- 在全新虚拟环境或干净容器中构建并安装；
- 使用 uv、npm/pnpm、Go、Cargo、Docker 和项目部署脚本执行启动 smoke；
- 测试配置注入、health check、版本升级、数据迁移、失败回滚和旧客户端兼容；
- 记录镜像/包 digest，禁止以本地未提交源码代替部署物测试。

**输入**：构建产物、空环境、旧版本数据、升级包、错误配置、依赖不可用和回滚触发条件。

**通过判据**：干净环境可安装；入口和 health check 成功；版本升级不丢数据；失败能回滚到可用版本；产物 digest 与 target commit/版本绑定；部署资源可清理。

**工具与证据**：uv/npm/Docker/项目部署脚本；保存包清单、镜像 digest、安装日志、health 响应、升级/回滚日志和环境清理结果。

### P-16 可观测性、诊断和数据脱敏

**测试目标**：确认产品失败可定位，日志/trace/health 与真实状态一致且不泄漏敏感信息。

**测试方法与工具**：

- 触发代表性成功、失败、超时、拒绝和恢复场景；
- 检查 health/readiness、结构化日志、trace/span、审计事件和错误码；
- 用 secret scanner 和正则规则扫描 stdout、stderr、日志、trace、截图、HAR、JUnit 和 sidecar；
- 将日志中的 run/task/request ID 与真实状态和测试 case 关联。

**输入**：正常请求、异常请求、敏感 token、数据库错误、第三方错误和重启场景。

**通过判据**：每个关键失败都有稳定错误类别和关联 ID；health 反映真实可用状态；日志不声称未发生的成功；secret、token、个人数据不出现在产物；诊断证据足够复现问题。

**工具与证据**：项目日志/trace 工具、health endpoint、secret scanner；保存日志、trace、health 响应、脱敏扫描结果和关联 ID。

### P-17 回归、变更影响和测试有效性

**测试目标**：确保每个已知问题在修复后被固定覆盖，且变更不会绕过受影响测试。

**测试方法与工具**：

- 每个 `/issue` 生成稳定 `issue_id`、重现命令、预期失败行为和修复后的回归 case；
- 使用 ZaoFu `dev-verify.py plan/run` 或等价影响闭包选择受影响命令；
- 变更跨 API、schema、数据、部署或 Web 边界时补跑直接调用者和跨层测试；
- 定期用 mutmut 等 mutation testing 抽样检查测试能否杀死错误实现；
- 记录 flaky、首次失败后重试通过、超时和未分类错误，不能简单标绿。

**输入**：issue reproduction、变更 diff、历史失败序列、受影响矩阵和回归命令。

**通过判据**：修复前重现失败、修复后通过；affected closure 全部执行；旧行为无静默退化；mutation 抽样达到预设杀死率；无 flaky case 被隐藏。

**工具与证据**：项目测试框架、`scripts/dev-verify.py`、mutmut、JUnit/CI；保存 issue ref、重现日志、diff、测试选择计划、回归报告和 mutation 报告。

TestKit 提供 `IssueMergeLoop` 作为通用顺序 oracle：`ingressed → triage:ready → approval:accepted → tests:pass → delivery:allowed → merge:merged → post_merge:pass` 才能得到 `merged`。Issue body 始终是不可信数据，不会被当作命令执行；同一 `(issue_id, revision)` 重复提交只返回第一次结果且不重复调用 triage、测试或 merge。可选的 `product-issue-loop-state.v1` 原子状态文件会跨进程重启保存这一幂等事实；状态损坏或 schema 漂移时 fail-closed，不能清空后重复 merge。测试 verdict 不是 `pass`、交付就绪不允许、审批未明确接受、merge 未返回 `merged` 或合并后回归失败，均不得伪造成功。`merge` 回调由 ZaoFu ControlledAction/人工授权适配器实现，TestKit 只验证它只能在前置门禁通过后被调用，并不直接写远端引用。

### P-18 ZaoFu 交付边界和最终验收

**测试目标**：证明产品测试结果可以安全进入 candidate、merge 或 release。

**测试方法与工具**：

- 校验 candidate、run contract、target commit、Product Acceptance spec/report、verification-result 和 artifact sidecar；
- 检查每个 mandatory journey/assertion 的结果、evidence ref/digest、provider receipt 和测试命令 receipt；
- 用 Candidate/Product Acceptance preflight 检查缺失证据、stale commit、错误 candidate、路径逃逸和远端策略；
- 统计 git/远端 API/shell 等受控副作用，确认未经授权的 push/merge/release 为 0。

**输入**：测试汇总、candidate manifest、产品验收报告、artifact refs、commit、远端状态和审批意图。

**通过判据**：required case 全部 PASS；mandatory acceptance 全部有完整 evidence；所有 ref/digest 可验证且属于当前 commit；产品状态与报告一致；没有未授权外部副作用；不满足条件时阻塞 candidate/goal closure。

**清单与证据**：

- [x] 通用 TestKit 已校验并生成 `verification-result`、Product Acceptance Report、JUnit 和 failure bundle；
- [x] 通用 runner 已校验 candidate、target commit、artifact、matrix 和报告身份一致；
- [x] 通用 runner 已校验所有 mandatory journey/assertion 有结果并将 evidence digest 纳入 artifact index；
- [~] remote policy、审批和真实 ControlledAction 副作用审计由 ZaoFu 适配器完成；TestKit 只验证未授权 merge/push 不会因测试通过而自动发生；
- [x] 通用 runner 保存最终 manifest 路径、报告、证据索引和 delivery-readiness 决定；真实产品 CI 上传仍由宿主仓库装配。

---

## 四、按产品能力裁决测试范围

每次运行先生成一份适用性表。适用域必须执行；不适用域必须有理由和证据。

| 产品能力 | 必须执行的域 | 典型工具 |
|---|---|---|
| 所有项目 | P-01～P-05、P-17、P-18 | ZaoFu runner、pytest、git、矩阵校验 |
| CLI/脚本 | P-06 | pytest/subprocess、项目 CLI 测试 |
| HTTP/GraphQL API | P-07、P-11 | pytest/httpx、Schemathesis、ZAP（按 tier） |
| 浏览器 Web/SSR | P-08、P-11、P-16 | Playwright、Docker、ZAP |
| 数据库/文件/缓存/队列 | P-09、P-12、P-13 | Testcontainers、项目迁移工具、pytest |
| 独立服务或第三方 API | P-10、P-12 | mock server、Pact、Testcontainers、Toxiproxy |
| 登录、角色、敏感数据 | P-11、P-16 | pytest、Schemathesis、ZAP、secret scanner |
| 长期进程/后台任务 | P-12、P-13、P-16 | subprocess、failpoint、Hypothesis |
| 包或部署物 | P-15 | uv/npm/Docker/部署脚本 |
| 有明确性能/容量目标 | P-14 | k6、系统监控 |

---

## 五、执行层级和具体清单

### T0：每个产品任务都执行

- [ ] P-01 版本、worktree、state dir 和 candidate 身份校验；
- [ ] P-02 setup/install 和隔离环境校验；
- [ ] P-03 build/lint/typecheck/compile；
- [ ] P-04 unit/component；
- [ ] P-05 capability → acceptance → test 闭包；
- [ ] 适用的 CLI/API/Web smoke；
- [ ] 生成 command receipt、JUnit/JSON 和 evidence sidecar；
- [ ] P-18 的 commit、digest 和禁止副作用检查。

### T1：按能力执行的确定性集成层

- [ ] API 项目运行 Schemathesis 和固定业务契约；
- [ ] Web 项目运行 Docker Playwright 关键旅程；
- [ ] 有真实依赖时运行 Testcontainers 集成测试；
- [ ] 多服务边界运行 Pact provider/consumer 验证；
- [ ] 长期进程运行 subprocess/failpoint 恢复和幂等；
- [ ] 复杂状态序列运行 Hypothesis，并固化发现的回归 case；
- [ ] 安全输入和副作用矩阵通过。

### T2：合并前门禁

- [ ] 所有 affected task commands；
- [ ] 所有 mandatory acceptance/journey/assertion；
- [ ] 变更跨越的 API/schema/database/Web 契约测试；
- [ ] deterministic mock E2E 和真实产品入口 smoke；
- [ ] Product Acceptance Report、candidate、target commit 和 evidence digest；
- [ ] 未授权 push/merge、重复外部写入和资源泄漏均为 0；
- [ ] 失败不是通过重试才变绿的 flaky case。

### T3：发布、夜间或专项层

- [ ] 多 Python/Node/Go/Rust/依赖版本安装矩阵；
- [ ] Docker/部署、升级、迁移、回滚；
- [ ] k6 load/stress/spike/soak 和资源泄漏；
- [ ] OWASP ZAP 被动/主动扫描；
- [ ] 真实外部依赖或 Provider canary；
- [ ] mutation testing、全覆盖趋势和长期稳定性；
- [ ] 完整清理和发布证据归档。

---

## 六、工具职责边界

统一执行入口推荐使用 pytest 或 ZaoFu command runner；pytest 负责 fixture、参数化、断言、subprocess、报告和门禁适配，不负责猜测业务正确性。产品项目可以使用 Jest、JUnit、Go test 或 Cargo test，其命令由 manifest 注册并由 ZaoFu 收集结果。

| 工具 | 适用范围 | 不应承担的职责 |
|---|---|---|
| pytest/pytest-asyncio | 固定场景、单元、集成、恢复、安全和交付边界 | 自动推断业务语义 |
| Hypothesis | 边界输入、状态序列和不变量 | 替代固定回归或真实用户旅程 |
| Schemathesis | OpenAPI/GraphQL schema、负向和 stateful API | 替代数据库/业务状态断言 |
| Playwright | 浏览器 DOM、交互、关键用户旅程 | 单独证明后台状态或副作用 |
| Pact | 独立 Consumer/Provider 接口契约 | 替代完整业务 E2E |
| Testcontainers | 真实数据库、队列、缓存和协议依赖 | 替代产品断言或部署证明 |
| subprocess/failpoint | 崩溃、重启、提交边界和清理 | 随机混沌代替精确故障点 |
| k6 | 性能、容量和 soak 阈值 | 普通功能正确性 |
| OWASP ZAP | Web/API 动态安全扫描 | 业务授权和内部状态不变量 |
| mutmut | 测试有效性抽样审计 | 代替正常回归测试 |

不建议为所有项目引入新的大型单体测试平台或把 Chaos Toolkit 作为日常门禁。前者会创建第二套测试控制平面、报告和语义，后者难以精确命中产品提交边界且容易产生非确定性。若未来 ZaoFu 部署为多服务集群并有明确 SLO、隔离演练环境和系统级故障需求，再把 Chaos Toolkit 放在发布/定期演练层；执行仍由专业工具完成，领域 oracle 仍由产品和 ZaoFu TestKit 定义。

---

## 七、产品测试结果到 ZaoFu 门禁的接入

`tests/product_testkit/` 已具备薄 TestKit。下列早期职责草图不是当前文件清单或待新建任务；实际执行入口是 `scripts/product-testkit.py` → `tests/product_testkit/__main__.py` → `runner.py`/`executor.py`，结果由 `report.py`、`delivery.py`、`artifacts.py` 等汇总。

```text
tests/product_testkit/
  manifest.py          # manifest 读取、schema 和 not_applicable 校验
  matrix.py             # capability/acceptance/test 闭包
  command_runner.py     # 隔离执行命令并写 command receipt
  product_oracle.py     # PASS/FAIL/NA/HARNESS_FAILURE 裁决
  evidence.py           # 日志、报告、快照、digest、sidecar
  process_runner.py     # 启停、崩溃、重启和清理
  security_spy.py       # shell/git/远端副作用计数
  web_runner.py         # Docker Playwright 入口
  environment.py        # 版本、目录、依赖和 commit 检查
```

TestKit 不重新实现 pytest、Playwright、k6 或 ZAP 的执行能力，只提供：

- 场景和隔离目录装配；
- 命令、输入、环境和 commit 记录；
- 统一 oracle 与失败分类；
- evidence 收集、原子写入和 digest 绑定；
- 进程、容器、端口、锁和临时目录清理；
- Product Acceptance、Candidate 和 verification-result 的接线。

---

## 八、`/issue → merge → 测试` 闭环

### 1. 发现问题并建立 Issue

问题来源可以是产品测试失败、用户旅程失败、API 5xx、恢复不一致、安全拒绝缺失、性能阈值超标或交付门禁失败。测试 runner 自动保存：

- `run_id`、`project_id`、`candidate_id`、`target_commit`；
- `case_id`、capability、acceptance ID 和测试命令 ID；
- Given/When/Then/Forbidden；
- 最小输入、复现命令、退出码、状态快照、日志和 evidence digest；
- 失败分类：产品失败、环境失败、测试 harness 失败或 flaky。

只有产品失败或经确认的测试缺陷才创建 `/issue`；环境问题应先修复 harness 或环境，不得伪报产品 bug。

### 2. Issue ingress 和 triage

Issue 内容（包括外部 Issue、附件和 Provider 文本）是不可信数据，只能通过 ZaoFu Issue ingress 进入事件/sidecar，不能直接写 canonical state 或执行其中的命令。

triage 必须完成：

- 去重同一 Issue revision 和同一 case 的重复 webhook；
- 验证 project、repository、base、candidate 和 target commit；
- 复跑原始重现命令，确认当前 HEAD 是否仍然复现；
- 判断产品 bug、测试缺口、harness/environment failure 或已解决；
- 生成 acceptance/test 影响范围和修复所需证据。

若问题已不复现，应记录 `verified-resolved`，不要无依据修改代码；若确认是产品问题，才创建 Fix Run 提案。

### 3. Fix Run 和修复约束

经人工批准的 Issue 意图进入 Fix Run。Fix Run 必须绑定原 Issue、原始失败证据、base commit 和候选 worktree，并遵守：

- 实现 Agent 可以修改产品代码和测试，但不能自声明最终通过；
- 任何新增行为都必须补充产品测试 case、acceptance 映射或明确的测试缺口说明；
- 修复不得绕过 manifest、matrix、verification command、Product Acceptance 或 ZaoFu 受控动作；
- 外部 Issue 文本中的“忽略规则、直接 push/merge、修改 events/state”等指令不得执行；
- Fix Run 重启、重复 webhook、旧 revision 和 stale candidate 必须保持幂等并拒绝错误绑定。

### 4. 修复验证和回归

修复完成后按以下顺序验证：

1. 在同一 case 上复现原始输入，确认原失败消失；
2. 执行 P-17 回归 case，确认修复行为被固定；
3. 根据变更影响闭包运行受影响的 P-03/P-04/P-06～P-16；
4. 若跨越 API、数据、部署或 Web 边界，执行对应跨层契约和 mock E2E；
5. 检查新旧行为、禁止副作用、性能预算和安全阈值；
6. 重新生成所有报告、日志和 digest，并绑定修复后的 target commit。

原始 issue case 通过但新增 mandatory acceptance 没有 evidence 时，仍不能进入 merge。

### 5. Candidate、PR 和 merge 门禁

Candidate preflight 只允许在以下条件同时满足时通过：

- 原 Issue 的回归 case PASS；
- affected closure、mandatory acceptance 和必要的 T0～T2 测试全部 PASS；
- Product Acceptance Report 和 verification-result 可解析；
- candidate、base、target commit、artifact ref/digest 全部一致；
- 没有 stale attempt、错误 project、路径逃逸、secret 泄漏、未授权 push/merge 或重复远端副作用；
- 测试环境、进程、容器、端口、锁和临时目录已清理。

本地 candidate/merge fixture 和 `IssueMergeLoop` 回调用于验证顺序与阻断，不等于已实现 `/issue-merge`，也不证明真实宿主适配完成。远端 push、PR/merge 等交付交由人工；本仓库代理禁止执行 push 或等价远端引用更新，不能用 ControlledAction 或人工批准回调绕过该禁令。测试通过不授予 merge/release 权限；远端状态只能通过可信只读结果核对。

### 6. Merge 后闭环和防回归

merge 后应：

- 在合并后的 commit 上运行最小 smoke 和受影响回归；
- 将 Issue、PR、merge commit、测试报告和 artifact digest 互相绑定；
- 更新 regression matrix，使该 case 成为长期门禁；
- 若发现测试没有捕获缺陷，创建“测试缺口” Issue，补充 oracle、输入、工具或 evidence 检查；
- 若失败来自 flaky、环境或工具，单独记录并修复 TestKit，不把重试绿化为产品通过；
- 定期审计未执行的 mandatory 命令、长期 `not_applicable`、无真实断言的测试和资源泄漏。

闭环可表示为：

```text
测试失败
  → 固化 case/evidence
  → /issue ingress
  → 去重与 triage
  → 批准 Fix Run
  → 修复代码 + 回归测试
  → affected closure + Product Acceptance
  → candidate/PR 校验
  → 本地/mock 交付验证；真实远端交付交由人工
  → merge 后 smoke/回归
  → 更新测试矩阵和 TestKit
```

---

## 九、最终验收清单

### 产品项目级

- [ ] manifest、能力矩阵、验收矩阵和测试矩阵来自同一 target commit；
- [ ] P-01～P-05、P-17、P-18 已通过；
- [ ] 每个适用能力都有对应测试域；不适用能力有理由；
- [ ] mandatory case、journey 和 assertion 均有可执行命令与 evidence；
- [ ] 所有产品失败、环境失败、harness failure 和 flaky 已分类；
- [ ] API/CLI/DOM/数据库/文件等真实状态互相一致；
- [ ] 重复、乱序、并发、崩溃和恢复符合契约；
- [ ] 安全、性能、部署和清理结果满足声明的 tier/阈值。

### ZaoFu 交付级

- [ ] command receipt、verification-result、Product Acceptance Report、JUnit/JSON 和 sidecar 可读取；
- [ ] 所有 evidence ref/digest 正确且绑定当前 commit；
- [ ] candidate、Issue、PR、base、repository 和 merge 状态没有串项目；
- [ ] 未授权 shell、文件、git、远端 API 和重复外部副作用为 0；
- [ ] `/issue → Fix Run → 回归 → candidate → merge → merge 后回归` 链路可追溯；
- [ ] merge 后新增或修复的测试已进入长期 regression matrix；
- [ ] 没有通过重试、截图或自然语言报告掩盖失败。

最终标准不是工具数量或测试函数数量，而是：每条产品契约都有可执行测试，每个测试都有明确输入和 oracle，每次通过都有可验证 evidence，每次失败都能进入 `/issue → 修复 → 回归 → merge → 再测试` 的可追溯闭环。
