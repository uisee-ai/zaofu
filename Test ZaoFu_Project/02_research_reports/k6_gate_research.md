# k6 结果适配与性能门禁调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/performance.py`。性能摘要与阈值判定已实现；工具 oracle 不代表生产容量或长稳已通过。

> 范围：仅研究 k6 官方文档、Grafana 官方仓库和 k6 源码。目标是为 ZaoFu TestKit 规定 k6 结果如何被测试适配器读取、如何 fail-closed，以及 Docker 短 smoke 能证明什么。本文讨论的是对产品项目的测试执行和判定，不是性能评分或模型评估。

## 结论

1. k6 的 `check()` 是断言记录，不会单独改变进程退出码；必须为 `checks`（或自定义结果指标）配置 threshold，才能把业务检查接入 CI 的 pass/fail。官方文档明确说明失败 check 会继续执行，需与 threshold 组合。[Checks](https://grafana.com/docs/k6/latest/using-k6/checks/)；[Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
2. threshold 表达式满足时测试通过；任一 threshold 不满足时 k6 测试失败。k6 源码定义 `ThresholdsHaveFailed = 99`；因此 `99` 可识别为“threshold 失败”，但任意其他非零码不能被当成 threshold 失败或产品失败，必须按运行/环境原因另行分类。[thresholds.go](https://raw.githubusercontent.com/grafana/k6/master/metrics/thresholds.go)；[exitcodes/codes.go](https://raw.githubusercontent.com/grafana/k6/master/errext/exitcodes/codes.go)
3. k6 默认的 threshold 计算可能对没有样本的 sink 返回通过：当前源码 `runNoTaint()` 在找不到聚合值时返回 `true`，Rate 在 `Total == 0` 时也没有 rate 值。[metrics/thresholds.go](https://raw.githubusercontent.com/grafana/k6/master/metrics/thresholds.go) 因此 ZaoFu 不能只相信退出码或 `ok`；对每一个 mandatory metric 必须检查实际样本数量，否则“零样本绿灯”应判定为 harness failure。
4. 优先使用 k6 v1.5.0 引入的 machine-readable summary，并用官方版本化 JSON Schema 校验；该格式将在 k6 v2 成为默认格式。k6 官方文档同时指出旧 `--summary-export` 格式仍为兼容保留但不再推荐，适配器必须区分两种格式，不可混用字段语义。[Custom summary](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/)；[k6-summary schema](https://github.com/grafana/k6-summary)
5. Docker 短 smoke 可以证明脚本可加载、k6 版本和结果解析/阈值映射链路可工作；它不能证明长期吞吐、p95/p99 稳定性、资源泄漏或 soak 行为。长期结论必须使用稳定环境、明确负载和独立性能门禁。

## 一、官方结果格式及其风险

### 1. `handleSummary(data)` 的契约

k6 要求 `handleSummary()` 返回字符串键到 `string` 或 `ArrayBuffer` 的映射；`stdout`、`stderr` 或文件路径分别表示输出目的地，文件会被覆盖。[Custom summary 的语法](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/#use-handlesummary)

官方示例允许把 `JSON.stringify(data)` 写到文件。当前 summary data 的聚合信息包含 `root_group`、`options`、`state` 和 `metrics`；每个 metric 包含 `type`、`contains`、`values`，有 threshold 时还包含 threshold 对象。官方示例中失败的 `p(95)<500` 对应 `thresholds["p(95)<500"].ok == false`。[Summary data reference](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/#summary-data-reference)

适配器应将 `ok` 解释为：官方新格式下 `true` 表示该 threshold 通过，`false` 表示该 threshold 失败；但必须同时核对进程退出码和声明的 threshold 集合。不能仅依赖人类可读 stdout 中的勾/叉。

### 2. 新旧 summary 不能混淆

k6 v1.5.0 开始可通过 `--new-machine-readable-summary` 或 `K6_NEW_MACHINE_READABLE_SUMMARY` 选择新格式，官方说明该格式将在 v2 成为默认；最新 k6 v2 文档还说明旧 `--no-summary`/legacy summary mode 已移除，应使用 `--summary-mode=disabled`。[Custom summary](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/#summary-data-reference)；[End of test](https://grafana.com/docs/k6/latest/results-output/end-of-test/)

官方 `grafana/k6-summary` 仓库提供版本化 JSON Schema 2020-12。summary schema v1 要求 `version`、`metadata`、`config`、`results`；`version` 和 `metadata.k6Version` 使用 SemVer，`config.execution` 只能是 `local` 或 `cloud`，`results.metrics` 是数组。[summary/1.0.0/schema.json](https://raw.githubusercontent.com/grafana/k6-summary/main/schemas/summary/1.0.0/schema.json)

仓库 README 将 `schemas/summary/1.0.0/schema.json` 定义为主 schema，并说明 schema 按版本并列、可用于自动验证和代码生成。[grafana/k6-summary README](https://github.com/grafana/k6-summary#readme)

注意：官方仓库 issue 曾报告 k6 1.7.1 生成 machine-readable summary 时出现 `metadata.k6_version`/`generated_at` 与 schema 期望的 camelCase 字段不一致。[Issue #6004](https://github.com/grafana/k6/issues/6004) 这说明“版本已知”不等于“输出一定符合 schema”，所以 schema 验证失败必须是 harness/protocol failure，不能降级为通过。

旧 `--summary-export` 是另一个向后兼容格式。官方 options 文档现在不鼓励继续使用它，并推荐 `handleSummary()`；其结构和新 `handleSummary` data “similar but not identical”。[Options reference](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#summary-export)；[v0.30 release notes](https://github.com/grafana/k6/blob/master/release%20notes/v0.30.0.md)

## 二、threshold、check 和退出码的可靠判定

### 1. threshold 语义

threshold 是测试整体的 pass/fail 条件；例如 `http_req_failed: ['rate<0.01']` 和 `http_req_duration: ['p(95)<200']`。表达式为真时通过，任一表达式为假时整体失败，官方示例指出通过时退出码为 `0`，失败时为非零。[Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)

当前源码中 `Threshold.run()` 将 `LastFailed = !passes`，`runAll()` 只要有一个 `passes == false` 就返回失败；threshold 解析或 metric 类型不匹配会返回错误，未知 metric 在 `Validate()` 中带 `InvalidConfig` 退出码。[metrics/thresholds.go](https://raw.githubusercontent.com/grafana/k6/master/metrics/thresholds.go)

### 2. check 不能代替 threshold

`check()` 只创建成功率 metric；失败 check 不会抛出测试失败状态，也不会自动改变退出码。要把响应状态、响应正文等业务断言变为门禁，必须声明 `checks: ['rate>...']` 或相应 tagged check threshold。[Checks](https://grafana.com/docs/k6/latest/using-k6/checks/)；[Fail a load test using checks](https://grafana.com/docs/k6/latest/using-k6/thresholds/#fail-a-load-test-using-checks)

因此，TestKit 规则是：manifest 中声明 mandatory checks 时，既要检查各 check 的 passes/fails，也要检查对应 `checks` threshold；只有 check 绿而 threshold 缺失，不能判定该测试通过。

### 3. 官方退出码及分类

k6 当前源码列出以下有意义的本地退出码：

| 退出码 | k6 源码含义 | TestKit 分类建议 |
|---:|---|---|
| 0 | 正常结束且没有导致失败的结果 | 只有 summary 完整、mandatory threshold/样本满足时才可能通过 |
| 99 | `ThresholdsHaveFailed` | 产品/性能失败；前提是输出可解析且不是 harness 不一致 |
| 100/101 | setup/teardown timeout | 运行失败；根据依赖和脚本归产品或环境，不得绿灯 |
| 102 | generic timeout | harness/环境或产品需进一步分类，不得绿灯 |
| 103 | REST API stop | 中止，不是通过 |
| 104 | invalid config | harness 配置失败，不是产品通过 |
| 105 | external abort（SIGINT/SIGTERM 等） | operator/harness 中止，不是通过 |
| 106 | REST API 无法启动 | harness/环境失败 |
| 107 | script exception | 脚本/产品运行失败，不是通过 |
| 108 | `test.abort()` | 脚本主动中止，不是通过 |
| 109 | Go panic | k6/harness 崩溃，不是通过 |
| 110 | `test.fail()` 标记失败 | 产品测试失败，不是通过 |

来源是 k6 官方 `errext/exitcodes/codes.go`；`test.abort()` 和 `test.fail()` 的行为也在官方 execution 文档中说明。[exitcodes/codes.go](https://raw.githubusercontent.com/grafana/k6/master/errext/exitcodes/codes.go)；[k6/execution](https://grafana.com/docs/k6/latest/javascript-api/k6-execution/)

## 三、零样本、空 threshold 和中止的 fail-closed 规则

### 1. 零样本

k6 当前 `runNoTaint()` 对缺少聚合 sink 返回 `(true, nil)`；Rate 在 `Total == 0` 时不设置 rate。也就是说，某些未产生样本的 metric/threshold 可能不显示或按“未失败”处理，而不是证明测试真的执行了。[metrics/thresholds.go](https://raw.githubusercontent.com/grafana/k6/master/metrics/thresholds.go)

因此 TestKit 必须在 k6 之外定义“执行过”的证据：

- 每个 mandatory metric 在 summary 中存在；
- Counter 的 `count`、Rate 的 `total` 或 check 的总次数大于 0；
- Trend 至少有可用聚合值；
- 每个 mandatory threshold 都能在对应 metric 下找到结构化结果；
- 如果脚本使用 tagged/submetric threshold，还要验证该 tag 的样本存在。

任一条件不满足：`k6_result_invalid/no_samples`（harness failure），即使进程返回 0 或 `ok:true` 也不能成为通过证据。官方历史 issue 也记录了“自定义 metric 没有数据时 metric/threshold 不出现在结果中”的风险。[Issue #1346](https://github.com/grafana/k6/issues/1346)

### 2. 空 threshold 或禁用 threshold

官方 options 中 `thresholds` 默认可以为空/未定义，`--no-thresholds` 表示不运行 threshold；`summary-mode=disabled` 还会禁用 summary 和 `handleSummary()`。[Options reference](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#thresholds)；[Summary mode](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#summary-mode)

TestKit 的性能门禁若声明 `mandatory_thresholds`：

- 脚本没有 threshold、threshold 列表为空、使用 `--no-thresholds`，均判定 `missing_threshold_gate`；
- 仅在 manifest 明确声明 `not_applicable` 且写出理由时，才允许不执行性能 threshold；
- `summary-mode=disabled` 或 summary 文件缺失，判定 `missing_evidence`；
- 不允许用“没有失败 threshold”推导“性能通过”。

### 3. 中止、短跑和无完成迭代

通过 `abortOnFail` 的 threshold 可在测试中途停止；k6 官方说明此时会停止测试，`delayAbortEval` 用来先收集一段样本。[Threshold abort](https://grafana.com/docs/k6/latest/using-k6/thresholds/#abort-a-test-when-a-threshold-is-crossed)

TestKit 处理：

- exit 99 且 summary 中至少有失败 threshold：记录 `threshold_failed`；若是 `abortOnFail`，同时记录 `aborted_after_threshold`，不能把部分样本当作完整 soak；
- exit 103/105/108 或收到外部取消：记录 `aborted`，不是 pass；
- summary 报告的 interrupted iterations 大于 0，或 mandatory 场景未达到声明的 iterations/requests：记录 `incomplete_run`，除非 manifest 明确允许短跑；
- exit 0 但 summary 缺失、无法解析、mandatory metric/threshold 缺失：`harness_failure`，不是 pass。

## 四、ZaoFu TestKit 的 k6 适配器判定算法

建议把一次 k6 命令的 receipt 固化为：`command`、镜像引用及 digest、k6 version、script digest、manifest/commit、开始/结束时间、退出码、summary ref/digest、stderr/stdout ref。

适配器按以下顺序判定：

1. **执行前**：确认脚本和 manifest 在目标 worktree，禁止远程生产地址；记录固定 image digest，运行 `k6 version` 并与 manifest 的允许版本匹配。
2. **运行后**：确认容器已退出、summary 文件存在且可读，stdout/stderr 没有 runner 自身错误；若进程被超时杀死或退出码未知，先判 harness/环境失败。
3. **格式**：读取显式声明的 summary mode/schema version；新格式用官方 JSON Schema 验证，未知 schema major、必填字段缺失、额外非法字段或版本字段不匹配均 fail-closed。旧格式只走单独 legacy parser，不得把 legacy threshold bool 当成新格式 `ok`。
4. **完整性**：把 manifest 声明的每个 mandatory metric、check、threshold 与 summary 对齐；检查样本数量 > 0、threshold 结果齐全、tag/submetric 命中；缺项或零样本为 `k6_result_invalid`。
5. **一致性**：
   - 退出码 `0` 要求所有 mandatory threshold 结构化结果为通过；
   - 退出码 `99` 要求至少一个结构化 threshold 明确失败；
   - `0` 与失败 threshold、或 `99` 与全部通过 threshold，都是 `result_inconsistent`；
   - 其他非零码按上表分类，全部不是性能通过。
6. **证据**：对 summary、原始脚本、命令 receipt 和输出计算 digest，并绑定当前 target commit；仅 stdout 文本、截图或 k6 进程返回 0 不足以完成 ZaoFu evidence gate。

## 五、Docker 固定版本的本地只读短 smoke

### 1. 镜像与权限

Grafana 官方 Docker Hub 明确提供 `X.Y.Z` 版本 tag，例如 `grafana/k6:0.46.0`，同时也提供 `-with-browser` 变体；不要使用 `latest` 作为合并门禁唯一版本。[grafana/k6 Docker Hub](https://hub.docker.com/r/grafana/k6)

官方 Dockerfile 使用非 root UID `12345`、`WORKDIR /home/k6` 和独立 browser image。[k6 Dockerfile](https://github.com/grafana/k6/blob/master/Dockerfile) 因此输出目录要显式挂载并保证该 UID 可写；脚本、manifest 和输入数据应只读挂载。官方 Docker 运行示例也采用脚本输入和挂载目录。[Running k6](https://grafana.com/docs/k6/latest/get-started/running-k6/)

### 2. 推荐的安全短 smoke 形态

镜像应锁定为 `grafana/k6:<version>@sha256:<digest>`，执行目录使用一次性临时目录，输入挂载只读、结果目录单独可写，容器退出自动清理。推荐基线（其中网络按产品是否需要本地 SUT 调整）：

```bash
docker run --rm \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --network=none \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --mount type=bind,src="$CASE_DIR/script.js",dst=/scripts/script.js,readonly \
  --mount type=bind,src="$OUT_DIR",dst=/out,rw \
  grafana/k6:<version>@sha256:<digest> \
  run --summary-mode=full --new-machine-readable-summary \
  --summary-export=/out/summary.json /scripts/script.js
```

这是 TestKit 的安全基线建议，不是 k6 自身保证。若被测服务只在宿主机 loopback 提供服务，使用 `--network=host` 会扩大网络范围，必须由 harness 显式允许并把 URL/端口写入 manifest；更安全的是使用隔离 Docker network 与专用 SUT 容器。不得把 token、生产 URL 或真实 SaaS 凭据放入 smoke。

先用 1 VU、1–N 次固定 iterations 的短脚本验证 parser、metric、check、threshold 和 evidence 绑定；把摘要写到单独输出目录后计算 digest。若脚本导出自定义 `handleSummary`，应保证它只写 `/out` 并返回结构化 JSON；不要依赖人类 summary 文本。

### 3. 短 smoke 的证据边界

短 smoke 可以证明：

- 固定版本镜像可启动，脚本语法和模块可加载；
- 被测本地入口可访问（若显式配置网络）；
- 至少一次合法请求/迭代真正产生样本；
- `check`、threshold、结构化 summary、退出码和 TestKit parser 的映射一致；
- 结果没有越过临时目录，容器和临时资源可清理。

短 smoke 不能证明：

- 生产负载下的吞吐、p95/p99 预算或容量上限；
- 数小时 soak 的内存、句柄、连接和资源泄漏；
- spike/stress/breakpoint 的恢复行为；
- 多区域、分布式 k6 或真实外部依赖的网络稳定性；
- 业务正确性、数据库一致性或 ZaoFu EventLog 状态闭环。

性能长期测试必须另列 release/nightly 层，固定负载模型、环境基线、阈值、最大运行时长和资源观测，且任何中止、无样本、summary 不完整都不能被压成“性能通过”。

## 六、建议落地的 k6 TestKit 用例清单

| case | 输入/方法 | 期望判定 |
|---|---|---|
| K6-01 | 1 VU、固定迭代、所有 threshold 满足 | exit 0、summary schema 通过、每个 mandatory metric 有样本、所有 threshold `ok=true` |
| K6-02 | 服务返回错误，使 `http_req_failed` threshold 失败 | exit 99，至少一个 threshold 明确失败，receipt 分类 `threshold_failed` |
| K6-03 | `check()` 失败但不配置 checks threshold | 不能判性能通过；判 `missing_threshold_gate` |
| K6-04 | 声明 custom metric threshold 但脚本不产生样本 | 即使 exit 0，也判 `k6_result_invalid/no_samples` |
| K6-05 | 传入错误 threshold metric 名称/表达式 | 非零（通常 invalid config），判 harness/config failure，不执行产品结论 |
| K6-06 | `abortOnFail` 或 `test.abort()` | 记录 aborted/incomplete；非 pass；保留 partial summary 和退出码 |
| K6-07 | 外部 SIGTERM/超时 | exit 105 或 runner timeout；判 aborted/harness failure，禁止绿灯 |
| K6-08 | summary 缺失、损坏 JSON、schema 版本未知 | fail-closed `missing_evidence`/`invalid_summary` |
| K6-09 | exit 0 与结构化失败 threshold 不一致 | `result_inconsistent`，禁止进入 acceptance |
| K6-10 | exit 99 但 summary 全部通过或没有 threshold | `result_inconsistent`/`missing_threshold_result`，禁止进入 acceptance |
| K6-11 | 固定 digest Docker 只读输入 smoke | 版本、脚本、样本、summary、清理均通过；仅证明短 smoke 边界 |
| K6-12 | 长时间 soak（release/nightly） | 另行检查吞吐/延迟/错误率/资源曲线；不能用 K6-01 的短 smoke 代替 |

以上规则能让 k6 作为性能/压力工具接入 ZaoFu，而不把“命令退出 0”“页面有绿色勾”或“没有出现失败 threshold”误当作完整测试证明。
