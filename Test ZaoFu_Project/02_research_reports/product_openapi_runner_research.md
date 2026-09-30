# Schemathesis 接入通用 ZaoFu Product TestKit 真实 HTTP/OpenAPI runner 调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/product_testkit/schemathesis.py`。已有 OpenAPI 执行适配器；当前 phase 仅 examples/coverage/fuzzing，stateful 仍未由该适配器准入。

> 调研日期：2026-09-15。范围仅限 Schemathesis 官方文档、官方 GitHub tag/source/tests、PyPI 官方发布元数据，以及本仓库锁定环境中已安装的 Schemathesis 4.26.1。本文只给接入契约与失败分类，不实现代码。

## 一、结论摘要

1. **ZaoFu 当前可复现基线是 Schemathesis 4.26.1，不是上游最新版本。** `pyproject.toml` 声明 `schemathesis>=4.26,<5`，`uv.lock` 锁定 4.26.1，本地 `uv run schemathesis --version` 也返回 4.26.1；截至调研日，上游最新稳定版已是 4.27.1。Product TestKit 应通过锁文件环境执行并把实际版本写入证据，不能使用会漂移的 `latest` / `stable` 作为判定依据。[PyPI 4.26.1 发布元数据](https://pypi.org/project/schemathesis/4.26.1/)；[官方 4.27.1 release](https://github.com/schemathesis/schemathesis/releases/tag/v4.27.1)；[4.27.1 `pyproject.toml`](https://github.com/schemathesis/schemathesis/blob/v4.27.1/pyproject.toml#L7-L12)
2. **当前 `build_schemathesis_command` 只是命令字符串 builder，还不是结果适配器。** 它仅支持本地非 symlink schema、完整 `--url` 和 3 个 check；没有固定 raw report、结构化结果读取、版本/seed、phase/mode、auth、网络 timeout 或 Schemathesis 特有退出码分类。官方明确说明仅凭退出码不能判断“实际测了什么”，并建议读取 JSON 且检查 `operations.tested`。[官方 CI/CD：Machine-Readable Results](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)
3. **真实 HTTP runner 应把 Schemathesis JSON 作为必需原始结果，把 JUnit/HAR 作为辅助证据。** 外层仍由现有 `run_command` 保存进程 exit、timeout、stdout/stderr；适配器再核对 JSON 的 `complete`、`operations.tested`、phase、failures、errors、warnings、seed 与版本。缺失、损坏、未完成或相互矛盾都应 fail-closed。[官方 CLI 报告参数](https://schemathesis.readthedocs.io/en/latest/reference/cli/#output)；[4.26.1 JSON report 官方源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py)
4. **退出码 `1` 不能直接等同于产品缺陷。** 名义契约是 `0=checks 通过`、`1=check/bug`、`2=config/schema error`；但官方 4.27.1 测试明确把远端 schema 的 HTTP 404/500 加载失败和目标请求 timeout 都断言为 exit 1。必须结合结构化内容和运行阶段分类。[官方退出码](https://schemathesis.readthedocs.io/en/latest/reference/cli/#exit-codes)；[官方 CLI tests：schema load、network timeout](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L230-L286)
5. **`negative` 是生成 mode，不是 phase。** `--mode positive|negative|all` 默认 `all`；phase 是 `examples,coverage,fuzzing,stateful`。`negative_data_rejection` 只在 negative/all case 上有意义。当前 helper 一旦传任一 `--checks`，实际就把默认“所有 checks”缩窄到 3 项白名单之一，可能生成负向输入却不检查“无效输入是否被拒绝”。[官方 CLI：mode](https://schemathesis.readthedocs.io/en/latest/reference/cli/#m-mode-mode)；[官方 CLI：phases/checks](https://schemathesis.readthedocs.io/en/latest/reference/cli/#run)；[官方 checks：negative_data_rejection](https://schemathesis.readthedocs.io/en/stable/reference/checks/#negative-data-rejection)
6. **stateful 必须成为显式 opt-in profile。** 官方定义它会链式发出真实 API 调用并在操作间传递资源数据；这可能执行 POST/PUT/PATCH/DELETE。通用 runner 的默认门禁宜先运行 `examples,coverage,fuzzing`，只有在隔离、可清理、非生产 SUT 上才运行 `stateful`。这是基于官方 phase 语义得到的安全接入推论。[官方 v4 迁移说明：Test Phases](https://schemathesis.readthedocs.io/en/stable/migration/#test-phases)

## 二、版本与安装边界

### 2.1 当前仓库事实

- `pyproject.toml` 的 dev extra 是 `schemathesis>=4.26,<5`；`uv.lock` 固定 wheel/sdist 为 4.26.1；本地实际命令为 `schemathesis, version 4.26.1`。4.26.1 要求 Python `>=3.10`，仓库 Python 3.11 满足要求。[PyPI 4.26.1](https://pypi.org/project/schemathesis/4.26.1/)
- 上游 4.27.1 于 2026-09-13 发布且标记 Latest。它仍处在仓库允许的 `<5` 范围内，但当前 lock 没有升级，所以本调研的可执行契约以 **4.26.1 本地 CLI help** 为准，4.27.1 官方文档/源码只用于验证上游当前行为。[官方 4.27.1 release](https://github.com/schemathesis/schemathesis/releases/tag/v4.27.1)
- 官方快速开始支持 `uvx schemathesis ...`（隔离临时安装）以及已安装后的 `schemathesis` / `st`；PyPI README 也给出 `uv pip install schemathesis`。对 ZaoFu，应优先 `uv run schemathesis` 使用本仓库 lock，而不是 `uvx` 临时解析最新版。[官方 Quick Start](https://schemathesis.readthedocs.io/en/stable/quick-start/#try-the-demo)；[PyPI Installation & Usage](https://pypi.org/project/schemathesis/4.26.1/#installation-usage)

### 2.2 runner 必须记录的版本证据

每次 run 至少保存：

- `uv.lock` 中的 Schemathesis version 与 package hash；
- 运行前 `uv run schemathesis --version` 的 stdout、exit code；
- JSON raw report 内的 `schemathesis_version`；
- 最终 argv digest、schema digest、配置 digest、target commit。

上述三处版本不一致时应判 `harness_failure/version_mismatch`，不能继续产生产品 verdict。官方 JSON writer 明确写出 `schemathesis_version`、`command`、`seed` 等字段。[4.26.1 JSON report 官方源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py)

## 三、schema location 与被测 base URL

### 3.1 当前 CLI 契约

| 输入形态 | 正确命令形态 | 语义 | 来源 |
|---|---|---|---|
| 本地 OpenAPI JSON/YAML | `st run ./openapi.yaml --url http://127.0.0.1:8000` | file-based schema 必须给 `--url` | [CLI `SCHEMA` / `--url`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#run) |
| 远端 OpenAPI URL | `st run http://127.0.0.1:8000/openapi.json` | 从 URL 加载 schema；base URL 通常由 schema/位置解析 | [官方 CI/CD：Schema Access](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#schema-access) |
| 本地 schema，只替换 origin | `st run ./openapi.yaml --origin http://127.0.0.1:8000` | 保留 schema 自己的 `basePath` / `servers[0].url` path，只替换 scheme/host/port | [CLI `--origin`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#origin-url) |

关键约束：

- v4 使用 `-u/--url`；v3 的 `-b/--base-url` 已迁移为 `--url`。当前 helper 使用 `--url` 是对的。[官方 v3→v4 迁移表](https://schemathesis.readthedocs.io/en/stable/migration/#command-line-interface)
- `--url` 表示测试请求的**完整 base URL**；它不会保留 schema 自带 path。若需求只是运行时替换 host/port，应使用 `--origin`。二者互斥。[CLI `--url` / `--origin`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#u-url-url)
- `--wait-for-schema` 只对通过 URL 获取 schema 的 readiness 有意义；当前 `service.py` 已在 command 前完成健康检查，若使用静态本地 schema，一般不需要再等待 schema。[CLI `--wait-for-schema`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#wait-for-schema-seconds)
- 通用输入契约应显式区分 `schema_location=file|url`，并区分 `base_url` 与 `origin`，不要通过字符串形状猜测用户希望保留还是覆盖 schema path。Schemathesis 自己也为错误 base URL 提供 `base_url_mismatch` warning。[官方 warnings](https://schemathesis.readthedocs.io/en/stable/reference/warnings/#base-url-mismatch)

### 3.2 与当前实现的关系

`tests/product_testkit/schemathesis.py` 目前只接受 `Path` 且要求普通文件、拒绝 symlink，然后生成：

```text
schemathesis run <schema> --url <base_url> --output-truncate 0
```

这条命令在本地 4.26.1 中仍可解析；但官方文档把 boolean 写作 `true|false`，建议未来命令证据使用规范化的 `--output-truncate false`，避免依赖 Click 的宽松 bool alias。[CLI `--output-truncate`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#output-truncate-boolean)

本地普通文件 + 完整 `base_url` 是安全且确定的默认组合；若扩展远端 schema，必须把 schema 下载/HTTP 错误与 SUT assertion 分开分类。

## 四、checks、negative mode 与 phases

### 4.1 checks

Schemathesis 当前默认启用所有 checks；`--checks a,b` 表示**只运行列出的 checks**，`--exclude-checks a,b` 表示从默认集合排除。4.26.1 也支持多次传 `--checks` 并合并，但官方展示的规范形式是一个逗号列表，便于命令 canonicalization。[官方 Checks：Controlling checks](https://schemathesis.readthedocs.io/en/stable/reference/checks/#controlling-checks)；[4.26.1 CLI options 源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/options.py)

4.26.1 CLI help 列出的 checks 为：

- 核心响应：`not_a_server_error`、`status_code_conformance`、`content_type_conformance`、`response_headers_conformance`、`response_schema_conformance`；[官方 Checks：Core response validation](https://schemathesis.readthedocs.io/en/stable/reference/checks/#core-response-validation)
- 输入处理：`negative_data_rejection`、`positive_data_acceptance`、`missing_required_header`、`unsupported_method`、`allow_header_conformance`；[官方 Checks：Input handling](https://schemathesis.readthedocs.io/en/stable/reference/checks/#input-handling)
- stateful：`use_after_free`、`ensure_resource_availability`；它们只在存在多步场景关系时触发；[官方 Checks：Stateful behavior](https://schemathesis.readthedocs.io/en/stable/reference/checks/#stateful-behavior)
- 安全：`ignored_auth`，会额外发送缺失/无效凭据请求；[官方 Checks：ignored_auth](https://schemathesis.readthedocs.io/en/stable/reference/checks/#ignored-auth)
- 响应时间：`max_response_time` 默认关闭，需 `--max-response-time <seconds>` 单独启用，且独立于 `--checks` / `--exclude-checks`。[官方 Checks：max_response_time](https://schemathesis.readthedocs.io/en/stable/reference/checks/#max-response-time)

当前 helper 的 3 项白名单只允许：

```text
not_a_server_error
status_code_conformance
response_schema_conformance
```

因此：

- `checks=()` 时不传 `--checks`，Schemathesis 默认所有 checks 开启；
- 只要传入任意现有 whitelist check，就会把其他默认 checks 全部关闭；
- 这会意外丢失 content type、response headers、正/负接受语义、coverage 特有检查、auth 与 stateful 检查。

通用 runner 不应再把官方 check 枚举硬编码为 3 项；应由版本探针/明确适配版本维护支持集，保存 effective check list，并在 JSON 中确认至少执行了 case。官方文档提醒：一个选择到零 operation 的 run 也会 exit 1，不能只按非零码归产品失败。[官方 CI/CD：Machine-Readable Results](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)

### 4.2 negative 是 mode

- `--mode positive` 只生成 schema-valid 数据；`--mode negative` 只生成 schema-invalid 数据；`--mode all` 两者都生成且是默认值。[CLI `--mode`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#m-mode-mode)
- `negative_data_rejection` 判断无效输入是否被 API 拒绝；它要求 negative 或 all mode。不能把“返回任意 4xx/5xx”笼统解释为产品通过，具体 allowed/expected statuses 由 check 配置控制。[官方 `negative_data_rejection`](https://schemathesis.readthedocs.io/en/stable/reference/checks/#negative-data-rejection)
- `positive_data_acceptance` 只针对 positive/all case；默认允许的一些 401/403/404/409/429/5xx 是为真实业务约束保留的宽容范围，所以仅有此 check 通过不代表已命中业务 happy path。[官方 `positive_data_acceptance`](https://schemathesis.readthedocs.io/en/stable/reference/checks/#positive-data-acceptance)

推荐把 effective `mode` 写入 manifest/receipt/JSON 对账，而不是依赖隐式默认。

### 4.3 phases

`--phases` 是逗号列表，默认启用全部：

| phase | 官方语义 | 通用 runner 建议 | 来源 |
|---|---|---|---|
| `examples` | 运行 schema 内显式 examples | 默认开启；数量不受 `--max-examples` 控制 | [CLI phases](https://schemathesis.readthedocs.io/en/latest/reference/cli/#phases-phases) |
| `coverage` | 确定性覆盖约束、边界及若干负向场景 | 默认开启；注意 unsupported method 等额外请求 | [v4 Test Phases](https://schemathesis.readthedocs.io/en/stable/migration/#test-phases) |
| `fuzzing` | Hypothesis 随机生成 case | 默认开启，设置明确 max examples/seed/time budget | [CLI Data Generation](https://schemathesis.readthedocs.io/en/latest/reference/cli/#data-generation) |
| `stateful` | 链式真实调用，将 response 数据传入后续 request | 仅隔离环境 opt-in；必须声明清理策略 | [v4 Test Phases](https://schemathesis.readthedocs.io/en/stable/migration/#test-phases) |

`--max-examples` 在 fuzzing 中控制每 operation case 上限，在 stateful 中控制 distinct sequence 数量，对 examples/coverage 无效。[CLI `--max-examples`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#n-max-examples-count)

## 五、报告与证据

### 5.1 官方能力

`--report` 可用逗号一次启用 `junit,vcr,har,ndjson,json,allure`；默认目录是 `schemathesis-report`。也可直接给固定路径：

| 格式 | 参数 | 用途与 TestKit 定位 | 来源 |
|---|---|---|---|
| JSON | `--report-json-path <file>` | **适配器必需输入**；单文档 verdict，含 operation 数、phase 状态、failures/errors/warnings 等 | [CLI JSON report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-json-path-filename) |
| JUnit XML | `--report-junit-path <file>` | CI/人类辅助报告；不能替代 JSON 完整性判定 | [CLI JUnit report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-junit-path-filename) |
| HAR | `--report-har-path <file>` | 请求/响应流量与复现证据；体积和敏感性最高 | [CLI HAR report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-har-path-filename) |
| NDJSON | `--report-ndjson-path <file>` | 全量 engine event 流，适合深诊断；CLI-only | [CLI NDJSON report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-ndjson-path-filename) |
| VCR | `--report-vcr-path <file>` | cassette 形式的交互记录 | [CLI VCR report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-vcr-path-filename) |
| Allure | `--report-allure-path <dir>` | 可视化辅助，需 `schemathesis[allure]` extra | [官方 Allure guide](https://schemathesis.readthedocs.io/en/stable/guides/allure/) |

固定 path 优于默认 timestamp 文件名，便于 TestKit 原子索引和完整性检查。建议放在输出目录的 namespaced 子目录，避免与 `runner.py` 最后生成的统一 `junit.xml` 冲突：

```text
<output>/schemathesis/result.json
<output>/schemathesis/junit.xml
<output>/schemathesis/traffic.har
<output>/junit.xml                     # Product TestKit 聚合报告
<output>/verification-result.json
```

### 5.2 JSON 必须检查的字段

官方 CI 指南要求至少读取 `operations.tested`，因为退出码并不说明 run 是否真正完成有效测试；4.26.1 JSON writer还写出 `schemathesis_version`、`command`、`seed`、`started_at`、`running_time`、`stop_reason`、`complete`、`exit_code`、operations、phases、test_cases、failures、errors、warnings 和 auth。[官方 CI/CD：Machine-Readable Results](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)；[4.26.1 JSON writer](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py)

适配器应 fail-closed 校验：

1. 文件存在、可读、JSON root/字段类型正确；
2. `complete is true` 且 `stop_reason` 与正常结束一致；
3. `operations` 非 null、`selected > 0`、`tested > 0`；
4. 请求的每个 phase 都有终态，不能把全 skip 当 pass；
5. `schemathesis_version` 与 version probe 一致；
6. JSON 内 exit、外层 `CommandReceipt.exit_code`、failures/errors/warnings 三者一致；
7. seed/effective command 能与 manifest 输入对账；
8. raw report digest 被加入 `artifact-index.json`。

### 5.3 当前 4.26.1 的实测矛盾案例

本地无副作用探针：

```text
uv run schemathesis run http://127.0.0.1:1/openapi.json \
  --wait-for-schema 1 --no-color \
  --report-json-path /tmp/.../result.json
```

结果是外层进程 exit `1`，但 JSON 同时为：

```json
{
  "schemathesis_version": "4.26.1",
  "complete": false,
  "stop_reason": "interrupted",
  "exit_code": 0,
  "operations": null,
  "test_cases": {"generated": 0},
  "failures": [],
  "errors": []
}
```

这与官方源码路径一致：fatal loader error 触发 CLI abort，外层退出 1；JSON shutdown 则使用 execution context 内部 exit code 写报告。因此 `JSON.exit_code == 0` 绝不能单独给 pass，`complete:false` / `operations:null` 必须优先判失败。[4.26.1 executor 源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/executor.py)；[4.26.1 JSON writer 源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py)

### 5.4 敏感信息

Schemathesis 默认启用 key-based sanitization，但官方明确说明**响应 body 不会被脱敏**，terminal 与所有报告都会原样保存响应 body。JUnit、JSON、HAR、stdout/stderr 均应视为敏感制品；不得公开发布，保留期和读取权限应由 TestKit 控制。[官方配置：Output sanitization](https://schemathesis.readthedocs.io/en/stable/reference/configuration/#output)

`--output-truncate false` 只是关闭显示截断，不等于关闭或加强脱敏；当前 helper 的 `--output-truncate 0` 会扩大日志内容，必须与敏感制品策略同时评估。[CLI output options](https://schemathesis.readthedocs.io/en/latest/reference/cli/#output)

## 六、退出码与 verdict 映射

### 6.1 官方名义契约

| exit | 官方说明 | 不能直接推导什么 | 来源 |
|---:|---|---|---|
| 0 | 所有配置的 checks 通过 | 不保证测到 operation，不保证 phase 完整，不保证命中了业务成功路径 | [官方 Exit codes](https://schemathesis.readthedocs.io/en/latest/reference/cli/#exit-codes) |
| 1 | 至少一个 check 失败或报告 bug | 不能一律当产品 failure；也可能是 schema HTTP load、network error、warning fail-on 或零 operation | [官方 Exit codes](https://schemathesis.readthedocs.io/en/latest/reference/cli/#exit-codes)；[官方 CLI tests](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L230-L286) |
| 2 | 配置或 schema error 导致 run 中止 | 不代表产品行为失败；应归 admission/config/schema/harness 类 | [官方 Exit codes](https://schemathesis.readthedocs.io/en/latest/reference/cli/#exit-codes) |

额外事实：配置 `warnings.fail-on` 后，命中的 warning 会使 exit 1，即使 checks 全部通过。[官方 Warnings：Advanced Configuration](https://schemathesis.readthedocs.io/en/stable/reference/warnings/#advanced-configuration)

### 6.2 推荐判定顺序

适配器应按优先级判断，而不是 `exit != 0 => product fail`：

1. **outer runner 不完整**：可执行文件缺失、Popen/cwd/权限失败、外层 timeout、服务启动或健康检查失败、服务提前退出 → `environment_failure`；现有 `command_runner.py` / `service.py` 已有 receipt 字段承载这些事实。
2. **raw evidence 不可信**：JSON 缺失/损坏、unknown shape、`complete:false`、version mismatch、外层 exit 与 JSON/内容矛盾、report 写入失败 → `harness_failure`。
3. **admission/config/schema 输入错误**：exit 2、无 `--url` 的本地 schema、argv/config 互斥、无效 phase/check/auth/cert、本地 schema 不存在或语法/版本错误 → `harness_failure`，并细分 `cli_config` / `schema_invalid`。[官方 CLI reference](https://schemathesis.readthedocs.io/en/latest/reference/cli/)
4. **schema 获取失败**：远端 schema 404/403/500、DNS/connect/TLS/proxy/timeout、非 schema content type → `environment_failure/schema_load` 或 `harness_failure/schema_location`，不可归产品 assertion；当前官方 tests 表明其中一些返回 1。[官方 LoaderErrorKind 源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/core/errors.py)；[官方 CLI tests](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L230-L241)
5. **没有执行有效测试**：`operations is null`、`selected == 0`、`tested == 0`、所有请求 phase 被 skip → `not_run` 或 `harness_failure/empty_coverage`，绝不 pass。[官方 CI/CD：要求 gate `operations.tested`](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)
6. **目标 transport/runtime error**：JSON `errors` / stdout 显示 `Network Error`、request timeout、connect/TLS/disconnect/proxy，且没有完整 assertion 证据 → `environment_failure/target_transport`；官方测试明确 request timeout 为 exit 1。[官方 request timeout/retries](https://schemathesis.readthedocs.io/en/latest/reference/cli/#request-timeout-seconds)；[官方 timeout test](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L243-L286)
7. **认证/测试数据/targeting 失配**：`missing_auth`、`unused_openapi_auth`、`base_url_mismatch`、`missing_test_data`、`unmatched_filter` 等 warning 应单独归 `configuration_gap`，由 policy 决定 fail-on；不能把只得到 401/403/404 的 run 当成业务验证成功。[官方 Warnings reference](https://schemathesis.readthedocs.io/en/stable/reference/warnings/)
8. **产品 finding**：run 完整、有 tested operations，且 `failures[]` 明确包含 server error、未文档状态码、content type/header/response schema 违约、无效输入被接受等 check failure → `fail/product_finding`。[官方 Checks reference](https://schemathesis.readthedocs.io/en/stable/reference/checks/)
9. **warning gate**：只有 warning fail-on 导致的 exit 1 → `fail/policy_warning` 或 `harness_failure/configuration_gap`，不要冒充 API check finding。[官方 warnings fail-on](https://schemathesis.readthedocs.io/en/stable/reference/warnings/#advanced-configuration)
10. **全绿**：外层 exit 0、JSON complete、版本/命令一致、tested > 0、请求 phases 达到预期、failures/errors 为空、必需 warning policy 满足，才可映射到 Product TestKit `pass`。

现有 `build_verification_result` 会把任意普通非零 command exit 判 `fail`；所以 Schemathesis 必须有专用 adapter 先将上述语义投影为结构化 verdict，不能只把 CLI 命令塞进通用 commands 后沿用裸 exit 规则。

## 七、seed、可复现与 replay

- `--seed <int>` 使相同 seed 产生相同的生成序列；JSON report 会保存 seed。[CLI `--seed`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#seed-integer)；[4.26.1 JSON writer](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py)
- `--generation-deterministic` 消除测试间随机变化；若目标是可比较的 CI 门禁，可显式启用。它与固定 seed 是不同入口，runner 应记录 effective 配置而不是只记录参数是否出现。[CLI deterministic](https://schemathesis.readthedocs.io/en/latest/reference/cli/#generation-deterministic)
- `--generation-database` 默认使用 `.hypothesis/examples`；缓存可影响复用。严格隔离 run 可用 `:memory:` 或专用路径，但 4.26.1 的 deterministic 模式与显式 database 组合会被 CLI 拒绝，profile 必须二选一。[CLI generation database](https://schemathesis.readthedocs.io/en/latest/reference/cli/#generation-database-text)；[4.26.1 CLI validation 源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/validation.py)
- `st run` / `st fuzz` 会把失败 case 写为 crash file；`st replay <case-id|file>` 可重放，stateful case 会按步骤重放。默认 replay 会删除已经修复的 crash，证据流程需要保留时应使用 `--keep` 或先复制到受控 artifact 目录。[官方 Replaying Failures](https://schemathesis.readthedocs.io/en/stable/guides/crash-reproduction/)
- 相同 seed 只控制测试生成，不冻结 SUT 数据库状态、服务版本、时间、并发、外部依赖或网络；完整可复现 receipt 仍须绑定 schema/配置/版本/target commit/base URL、workers 和隔离数据快照。这是基于 seed 官方定义的工程推论。[CLI `--seed`](https://schemathesis.readthedocs.io/en/latest/reference/cli/#seed-integer)

建议 deterministic CI profile 采用 `workers=1`、固定 seed 或 deterministic、固定 `max-examples`、固定 phases/mode/checks，并把 stateful 拆成另一个可清理 profile。

## 八、timeout、重试与认证

### 8.1 四类时间约束不可混用

| 参数/边界 | 含义 | 推荐关系 | 来源 |
|---|---|---|---|
| `--wait-for-schema S` | 最长等待远端 schema 可用；默认关闭 | 只给 remote schema profile | [CLI wait-for-schema](https://schemathesis.readthedocs.io/en/latest/reference/cli/#wait-for-schema-seconds) |
| `--request-timeout S` | 每次 HTTP 请求 timeout；默认 10 秒 | 小于整个 Schemathesis run 与外层 command timeout | [CLI request-timeout](https://schemathesis.readthedocs.io/en/latest/reference/cli/#request-timeout-seconds) |
| `--max-time S` | 整个 run 的时间预算；phase 分享预算，fuzzing/stateful 可重复直到耗尽 | 有界 gate 必须显式设置 | [CLI max-time](https://schemathesis.readthedocs.io/en/latest/reference/cli/#max-time-seconds) |
| Product TestKit `timeout_seconds` | 外层进程墙钟上限；超时后杀进程组 | 应大于 max-time + schema/report/cleanup 余量 | [Schemathesis max-time](https://schemathesis.readthedocs.io/en/latest/reference/cli/#max-time-seconds) |
| `--max-response-time S` | 产品性能 check，超过即 finding | 不是 transport timeout；默认关闭 | [Checks max_response_time](https://schemathesis.readthedocs.io/en/stable/reference/checks/#max-response-time) |

`--request-retries N` 默认 0，只对 network-level connection errors/timeouts 重试，使用指数退避；N 表示额外重试 N 次、最多 N+1 次请求。重试可能掩盖瞬态依赖问题，因此 raw errors/retry 配置也必须留证。[CLI request-retries](https://schemathesis.readthedocs.io/en/latest/reference/cli/#request-retries-n)

### 8.2 认证

| 认证方式 | CLI/config | 来源 |
|---|---|---|
| Basic | `--auth user:pass` | [Authentication：Static Authentication](https://schemathesis.readthedocs.io/en/stable/guides/auth/#static-authentication) |
| Bearer / API key / 任意静态 header | 可重复 `--header 'Name: value'` | [CLI headers/auth](https://schemathesis.readthedocs.io/en/latest/reference/cli/#network) |
| OpenAPI-aware apiKey/basic/bearer | `schemathesis.toml` 的 `[auth.openapi.<securityScheme>]` | [Authentication：OpenAPI-Aware](https://schemathesis.readthedocs.io/en/stable/guides/auth/#openapi-aware-authentication) |
| 动态 token | config dynamic auth、WFC `--auth-wfc`，或 Python auth 扩展 | [Authentication guide](https://schemathesis.readthedocs.io/en/stable/guides/auth/) |
| TLS / mTLS | `--tls-verify <CA>|false`、`--request-cert`、`--request-cert-key` | [CLI Network](https://schemathesis.readthedocs.io/en/latest/reference/cli/#network) |

ZaoFu 当前 `CommandReceipt` 会保存完整 argv/command digest；若把 bearer token、API key 或 Basic 密码直接插入 manifest command，secret 会进入 canonical logs/receipt。推荐使用 `schemathesis.toml` 的 `${ENV_VAR}` 替换和 `run_command(..., env=...)` 注入：

```toml
headers = { Authorization = "Bearer ${API_TOKEN}" }
```

再以全局参数位置调用 `schemathesis --config-file <run-local-config> run ...`。Schemathesis 官方说明 CLI 参数优先于 config，环境变量可在 config 中替换。[官方 Configuration](https://schemathesis.readthedocs.io/en/stable/configuration/)；[Authentication config](https://schemathesis.readthedocs.io/en/stable/guides/auth/#static-authentication)

即使这样，响应 body 仍不会被 sanitizer 清理，所以报告仍按敏感制品管理。

## 九、常见环境/运行失败分类表

| 分类 | 典型信号 | 推荐 verdict | 官方依据 |
|---|---|---|---|
| `tool_missing` | Popen `ENOENT`、version probe 不可运行 | `environment_failure` | 官方安装/命令入口：[Quick Start](https://schemathesis.readthedocs.io/en/stable/quick-start/) |
| `version_mismatch` | lock、`--version`、JSON version 不一致 | `harness_failure` | JSON 含 version：[4.26.1 JSON writer](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py) |
| `cli_config` | 无效 option/value、`--url`/`--origin` 冲突、本地 schema 缺 `--url`、auth/cert 参数错误；常见 exit 2 | `harness_failure` | [CLI reference](https://schemathesis.readthedocs.io/en/latest/reference/cli/) |
| `schema_missing_or_invalid` | 本地文件不存在、JSON/YAML syntax、OpenAPI version/schema/ref 无法处理 | `harness_failure` | [LoaderErrorKind 官方源码](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/core/errors.py) |
| `schema_load_environment` | URL DNS/connect/TLS/proxy、403/404/5xx、unexpected content type、等待超时；可能 exit 1 | `environment_failure` 或按 owner 判 `schema_location` harness failure | [官方 schema-load tests](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L230-L241) |
| `service_start_health` | start exec 失败、提前退出、health timeout | `environment_failure` | Schema readiness 的官方边界：[CI/CD Schema Access](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#schema-access) |
| `target_transport` | `Network Error`、connect/read timeout、disconnect、TLS/proxy error；通常 exit 1 | `environment_failure` | [官方 network timeout test](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L243-L286) |
| `auth_or_test_data_gap` | 反复 401/403/404；`missing_auth`、`unused_openapi_auth`、`missing_test_data` warnings | `configuration_gap`，不可自动 pass | [Warnings reference](https://schemathesis.readthedocs.io/en/stable/reference/warnings/) |
| `wrong_target_or_filter` | `base_url_mismatch`、`unmatched_filter`、selected/tested=0 | `not_run` / `harness_failure` | [Warnings](https://schemathesis.readthedocs.io/en/stable/reference/warnings/)；[CI machine result](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results) |
| `schema_runtime_mismatch` | `validation_mismatch`、`missing_deserializer`、`unresolvable_reference` | 优先 schema/config gap；有明确 response conformance failure 时才是 product finding | [Warnings reference](https://schemathesis.readthedocs.io/en/stable/reference/warnings/) |
| `rate_limit_or_flaky_dependency` | 429、timeout、connection reset；重试后结果变化 | `environment_failure` / `flaky`，保留所有尝试 | [CLI rate limit/retries](https://schemathesis.readthedocs.io/en/latest/reference/cli/#network) |
| `product_finding` | 完整 run + tested>0 + `failures[]` 中明确 check finding | `fail` | [Checks reference](https://schemathesis.readthedocs.io/en/stable/reference/checks/) |
| `report_or_evidence_failure` | JSON/JUnit/HAR 写不出、缺失/损坏、`complete:false`、外内 exit 矛盾 | `harness_failure` | [CLI reports](https://schemathesis.readthedocs.io/en/latest/reference/cli/#output) |
| `outer_timeout_or_interrupt` | Product TestKit timeout、signal、Schemathesis incomplete/interrupted | `environment_failure` / `harness_failure`，绝非 pass | JSON complete/stop reason：[4.26.1 JSON writer](https://github.com/schemathesis/schemathesis/blob/v4.26.1/src/schemathesis/cli/json_report.py) |

## 十、建议的接入轮廓

### 10.1 两层结构

1. **命令构建/执行层**：验证 schema/base URL/auth secret 边界；构建 argv；由现有 `start_entrypoint` 先启动并健康检查 SUT，再由 `run_command` 执行；保持进程组 timeout/cleanup 和 stdout/stderr/receipt。
2. **Schemathesis 结果适配层**：读取固定 JSON，核对 version/complete/operation/phase/test case/failure/error/warning/seed 与外层 receipt，输出 Product TestKit 已有 `pass|fail|environment_failure|harness_failure|not_run|flaky`，并把 raw JSON/JUnit/HAR 加入 artifact index。

这符合官方“exit code 只说明失败，不说明执行了什么”的约束。[官方 CI/CD Machine-Readable Results](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)

### 10.2 推荐的确定性 stateless gate 形态

以下只是目标 argv 轮廓，具体值来自受控 manifest/config，**不是本次实现**：

```text
uv run schemathesis --config-file <run-local-schemathesis.toml> run <local-openapi-file>
  --url <full-isolated-base-url>
  --phases examples,coverage,fuzzing
  --mode all
  --checks <explicit-comma-separated-effective-checks>
  --workers 1
  --seed <recorded-int>
  --max-examples <bounded-count>
  --max-time <bounded-seconds>
  --request-timeout <seconds>
  --request-retries 0
  --report-json-path <output>/schemathesis/result.json
  --report-junit-path <output>/schemathesis/junit.xml
  --report-har-path <output>/schemathesis/traffic.har
  --no-color
```

参数语义来自官方 CLI reference。[官方 CLI Options](https://schemathesis.readthedocs.io/en/latest/reference/cli/)

注意：

- HAR 可以按 profile 选择性启用；失败诊断价值高，但体积大且包含敏感流量。[CLI HAR](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-har-path-filename)
- stateful 应是另一条显式 profile，带数据隔离、cleanup 和更严格 timeout，不能悄悄继承 Schemathesis 的“所有 phases 默认开启”。[官方 Stateful phase](https://schemathesis.readthedocs.io/en/stable/migration/#test-phases)
- 若不需要精确 check allowlist，可省略 `--checks` 以使用当前版本默认全量 checks；但必须把版本与 effective policy 固定下来，避免升级时默认集合漂移。[官方 Checks](https://schemathesis.readthedocs.io/en/stable/reference/checks/#controlling-checks)

### 10.3 最小验收用例

| case | 方法 | 期望 |
|---|---|---|
| ST-01 | 4.26.1、本地 schema、健康真实 HTTP SUT、至少一个 operation 通过 | outer 0、JSON complete、tested>0、无 failures/errors、版本一致 → pass |
| ST-02 | 明确返回 500 或 response schema 违约 | outer 1、JSON 有具体 `failures[].type`、run 完整 → product fail |
| ST-03 | 本地 schema 不给 `--url` / 无效 phase/check | exit 2 或 admission 拒绝 → harness/config failure |
| ST-04 | 远端 schema 404/500/timeout | 即使 exit 1，也按 schema-load environment/harness failure，不是产品 finding |
| ST-05 | SUT request timeout/connect failure | exit 1 + Network Error / JSON errors → environment failure |
| ST-06 | filter 选择零 operation | JSON tested=0 → not_run/harness failure，禁止绿灯 |
| ST-07 | fatal schema load 写出 `complete:false` 且 JSON exit 0、process exit 1 | result inconsistent/incomplete → harness failure |
| ST-08 | JSON 缺失、损坏、未知 shape 或版本不一致 | harness/evidence failure |
| ST-09 | negative mode + `negative_data_rejection`，API 接受无效输入 | 完整 failure → product fail |
| ST-10 | auth 缺失导致全程 401/403 与 warning | configuration gap，不能当业务 pass |
| ST-11 | 外层 timeout 或 SUT 提前退出 | environment failure；保留 partial reports 和 service receipt |
| ST-12 | isolated stateful profile 后 cleanup 成功/失败 | 只有完整 stateful + cleanup 成功才可给该 profile pass；cleanup 失败为 harness/environment failure |

## 十一、不应采用的简化

- 不要把 `schemathesis exit 0` 直接映射为产品通过。[官方建议检查 `operations.tested`](https://schemathesis.readthedocs.io/en/stable/guides/cicd/#machine-readable-results)
- 不要把所有 exit 1 都映射为产品失败；schema HTTP load 和 network timeout 也会是 1。[官方 CLI tests](https://github.com/schemathesis/schemathesis/blob/v4.27.1/test/cli/test_commands.py#L230-L286)
- 不要只解析 stdout；使用固定路径 JSON，并保留 stdout/stderr 作为诊断证据。[CLI JSON report](https://schemathesis.readthedocs.io/en/latest/reference/cli/#report-json-path-filename)
- 不要让 raw Schemathesis `junit.xml` 覆盖 Product TestKit 聚合 `junit.xml`。[CLI report path](https://schemathesis.readthedocs.io/en/latest/reference/cli/#output)
- 不要把 `--request-timeout`、`--max-response-time`、`--max-time` 和外层 command timeout 当成同一种超时。[官方 CLI options](https://schemathesis.readthedocs.io/en/latest/reference/cli/)
- 不要把 token/password 放进 manifest command；优先 env-substituted config，并把所有输出视为敏感制品。[官方 Configuration](https://schemathesis.readthedocs.io/en/stable/configuration/)；[官方 sanitization 边界](https://schemathesis.readthedocs.io/en/stable/reference/configuration/#output)
- 不要默认对任意环境执行 stateful/all phases；stateful 会链式发出真实资源操作。[官方 phase 定义](https://schemathesis.readthedocs.io/en/stable/migration/#test-phases)

## 十二、最终建议

在不改变现有 Service/Runner 生命周期和统一报告所有权的前提下，新增一个 Schemathesis 专用结果适配器是最小正确边界：命令层继续走 `run_command`，raw JSON 是必需语义证据，JUnit/HAR 是 namespaced 辅助制品；adapter 负责将 Schemathesis 的 findings、network/schema/config/incomplete/empty-run 情况映射到 Product TestKit verdict。先以 lock 中 4.26.1 实现和测试，版本升级另设兼容性验证，不把上游 4.27.1 或 `stable` 自动带入当前门禁。

最关键的不变量是：**只有 outer receipt 完整、Schemathesis JSON `complete=true`、版本与命令一致、`operations.tested>0`、预期 phases 已运行、无 errors、无 product failures 且 warning policy 满足，才能给 pass。**
