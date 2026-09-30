# ZAP 受控启动、证据与清理调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/zap_launcher.py`。已有受控 launcher 与 fake 合同；真实扫描、认证/主动覆盖和风险判定仍须独立证据，FakeDocker 不是实扫。

> 2026-09-14；仓库读取基线 `142a62a`，工作树有主代理的在途修改。本文按 research 技能核验仓库和官方资料，只新增本文件；没有运行 Docker、扫描、镜像拉取或测试，也没有修改实现、提交或推送。以下“建议”均为待实现方案，官方 `main` 源码事实须在最终固定镜像对应版本再次核对。

## 结论与最小范围

建议第一步交付 `tests/testkit/` 内的小型启动器、独立收据校验和 fake Docker 契约测试；真实执行放在显式 `host` 层。首个真实配置采用 ZAP 镜像已有的 Automation Framework（AF），固定 `requestor → passiveScan-wait → report`，只访问声明的 ZaoFu 只读路径，称为“限定端点被动扫描”。它不等同于全站 Baseline 或 API 主动扫描。AF 已随官方稳定镜像提供，无须引入新的测试平台。[AF 官方概览](https://www.zaproxy.org/docs/automate/automation-framework/)

真实成功必须同时证明：目标确属本轮隔离服务、mandatory 请求得到预期响应、被动队列完成、报告对应本轮目标、风险满足预算、资源清理完成。镜像不可用、错目标、空执行、超时或清理未完成统一归 `harness_failure`。安全扫描不能完整证明业务授权正确；任务/角色/令牌的业务权限仍由现有确定性测试和人工安全分析覆盖。[ZAP Active Scan 的能力边界](https://www.zaproxy.org/docs/desktop/start/features/ascan/)

## 当前仓库事实与可复用接口

| 位置 | 已有能力／缺口 |
| --- | --- |
| [zap.py](/home/uisee/zaofu_v7/tests/testkit/zap.py:44) | `parse_zap_json` 验证非空 `site`、alerts、riskcode，随后丢弃站点和实例 URI，只留下告警数量；不验证目标、扫描覆盖或生成来源。 |
| [zap.py 判定](/home/uisee/zaofu_v7/tests/testkit/zap.py:83) | `scanner_exit_code` 默认 0；风险越限先返回 `fail`，然后才检查退出码。因此 `exit=3/None + 越限告警` 也会被当作产品失败；没有 `timed_out` 输入。 |
| [test_zap.py](/home/uisee/zaofu_v7/tests/testkit/test_zap.py:18) | 合成 `http://isolated.test` 报告；空 alerts 可通过，覆盖畸形报告、风险预算、非零退出、文件错误。没有 launcher、扫描完成或目标绑定测试。 |
| [performance.py](/home/uisee/zaofu_v7/tests/testkit/performance.py:169) | 可借用其顺序：先超时/执行有效性，再必需样本与阈值结果一致性，最后产品判定；不能照搬 k6 的专用退出码 99。 |
| [k6 host helper](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10_k6_host.py:20) | digest 镜像、`--pull=never`、唯一容器名、只读脚本、输出挂载、`run_command`、90 秒预算、finally 中精确 `docker rm -f`。其 `--network=none` 是无 HTTP 请求的 k6 smoke，ZAP 不能直接复用网络配置。 |
| [command_runner.py](/home/uisee/zaofu_v7/tests/testkit/command_runner.py:56) | argv 无 shell、独占 command_id、stdout/stderr 落盘、argv digest、超时进程组清理和环境失败收据。`passed` 仅表示退出 0 且未超时，不能作为扫描 verdict。`env` 会合并宿主环境，launcher 仍须约束 Docker endpoint 和显式容器环境。 |
| [evidence.py](/home/uisee/zaofu_v7/tests/testkit/evidence.py:13)／[cleanup.py](/home/uisee/zaofu_v7/tests/testkit/cleanup.py:78) | `sha256_file`、`evidence_ref`、`write_json_atomic` 与 `terminate_process_group` 可直接复用；宿主进程快照不证明 Docker daemon 中已无容器/网络。 |
| [browser_app.py](/home/uisee/zaofu_v7/tests/testkit/browser_app.py:12) | 用 `ZF_TESTKIT_BROWSER_ROOT` 加载 `zf.yaml`，尊重 `project.state_dir`，再调用真实 `server.create_app`；指定 UI dist，不添加生产 fixture 路由。可以作为容器内测试 app factory。 |
| [浏览器 E2E](/home/uisee/zaofu_v7/tests/e2e/test_testkit_browser_truth_chain.py:112) | 已示范隔离 `ZF_WORKSPACE_HOME`、fixture root、测试 token、uvicorn 和 `/api/state` readiness。浏览器使用 host 网络是它自身的测试约定，不能据此批准 ZAP 接触宿主服务。 |
| [suites.py](/home/uisee/zaofu_v7/tests/testkit/suites.py:247)／[pyproject.toml](/home/uisee/zaofu_v7/pyproject.toml:46) | S10 目前只注册报告解析测试；默认排除 host/perf/real_provider。保留这种边界，不把真实 scanner 加入默认收集列表。 |

建议只增加一个 testkit launcher 模块及直接测试，并在现有 `zap.py` 附近补目标/收据门禁。仍使用 `ZapVerdict`；扫描 recipe、范围和风险预算由测试场景声明，不能成为 runtime 的第二套业务状态机。

## Baseline、API scan、AF 的取舍

| 路线 | 官方行为 | 对本轮建议的影响 |
| --- | --- | --- |
| `zap-baseline.py` | 默认 spider 1 分钟后被动扫描，默认告警为 WARN；支持 `-m`、`-T`、`-J`、`-n`、`--autooff`。 | 可作为以后有限爬取配置；单纯 `-t .../safe-path` 不是路径沙箱。 |
| `zap-api-scan.py` | 导入 OpenAPI/SOAP/GraphQL 后默认主动扫描；`-S` 关闭 active scan，`-O` 覆盖远端 OpenAPI hostname。 | 后续才启用；只接受已审核本地 API 文件，核验全部 server/host、外部 `$ref` 和操作路径，不能把 `-O` 当完整 scope 防线。 |
| AF `zap.sh -cmd -autorun <local-plan>` | 按 YAML 顺序执行 jobs；可明确 requestor、URL tests、passive wait、report。 | 首选固定小计划，先不加入 spider、activeScan、脚本、浏览器认证、远端 plan 或任意用户 YAML。 |

来源：[Baseline](https://www.zaproxy.org/docs/docker/baseline-scan/)、[API scan](https://www.zaproxy.org/docs/docker/api-scan/)、[AF 命令与执行顺序](https://www.zaproxy.org/docs/desktop/addons/automation-framework/)。

必须保留两个源码差异：Baseline 文档仍说 AF 将成为默认，而当前脚本已称默认启用；legacy 和生成 AF 计划的路径都将路径 target 扩回站点根目录。不能依赖移动标签或文档中的默认值约束 scope。[zap-baseline.py](https://github.com/zaproxy/zaproxy/blob/main/docker/zap-baseline.py)

另一项关键事实：legacy `zap_wait_for_passive_scan` 超时只写 debug；当前 AF `PassiveScanWaitJob.runJob` 达到 `maxDuration` 或 forceStop 后也直接跳出等待。仅有退出 0 或报告存在，不能证明队列清空。[zap_common.py](https://github.com/zaproxy/zaproxy/blob/main/docker/zap_common.py)、[PassiveScanWaitJob.java](https://github.com/zaproxy/zap-extensions/blob/main/addOns/pscan/src/main/java/org/zaproxy/addon/pscan/automation/jobs/PassiveScanWaitJob.java)

最小处理建议：固定 AF 中 `passiveScan-wait.parameters.maxDuration: 0`（等待完成、不设内部截止），由外层 `run_command(timeout_seconds=...)` 统一硬截止。超时即失败并清理；report 必须是最后任务、不得 `alwaysRun: true`，AF 不接受外部 stop。这样可从受控计划和正常完成推导队列完成，收据须将其标为“计划保证”，不能虚构 API 采样的 `recordsToScan=0`。[passiveScan-wait 参数](https://www.zaproxy.org/docs/desktop/addons/passive-scanner/job-pscanwait/)

## 自有服务、网络与 URL 边界

建议 launcher 接受本轮创建的 `OwnedTarget`（run_id、容器 ID、网络 ID、config/state ref），不接受自由 `target_url`。服务和 ZAP 都仅加入本轮新建的专用 bridge 网络，目标 origin 由该目标容器的 inspect IP 和固定端口生成，例如 `http://<owned-ip>:8002`；若用 `zf-target` 别名，须核验它只绑定目标容器。fixture 使用真实 ZaoFu app、测试数据和关闭真实 integrations/provider 的 `zf.yaml`，state/workspace 都在 `/tmp/zf-zap-<UTC>-<random>/` 下。

`--internal` 阻断其它网络，但默认网桥仍有地址，可访问监听该地址或 `0.0.0.0` 的宿主服务。Docker 28 增加 `gateway_mode_ipv4/ipv6=isolated`，使 internal 网桥无地址。建议要求此能力并 inspect 验证；不支持就 `harness_failure`，不降级 host/default bridge，不自动改宿主全局防火墙。[network create](https://docs.docker.com/reference/cli/docker/network/create/)、[isolated 网关](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)、[Docker 28](https://docs.docker.com/engine/release-notes/28/)

下面是**未来 launcher 的命令形状，未执行，尖括号均是已验证占位符**；实际全部用 argv 数组、每步独立 timeout/receipt：

```text
docker --host unix:///var/run/docker.sock image inspect ghcr.io/zaproxy/zaproxy:<version>@sha256:<64hex>
docker --host unix:///var/run/docker.sock image inspect <preloaded-zf-test-image@sha256:digest>
docker --host unix:///var/run/docker.sock network create --driver bridge --internal --label zf.testkit.run=<run_id> --opt com.docker.network.bridge.gateway_mode_ipv4=isolated --opt com.docker.network.bridge.gateway_mode_ipv6=isolated <unique-network-name>
docker --host unix:///var/run/docker.sock network inspect <created-network-id>
docker --host unix:///var/run/docker.sock run --detach --pull=never --name <owned-app-name> --label zf.testkit.run=<run_id> --network <created-network-id> <bounded-app-options> <preloaded-zf-test-image@sha256:digest> <fixture-uvicorn-command>
docker --host unix:///var/run/docker.sock container inspect <created-app-id>
docker --host unix:///var/run/docker.sock run --pull=never --name <owned-zap-name> --label zf.testkit.run=<run_id> --network <created-network-id> --cap-drop=ALL --security-opt=no-new-privileges --read-only --tmpfs /tmp:rw,nosuid,size=256m --memory=2g --cpus=2 --pids-limit=256 --mount type=bind,src=<input>,dst=/zap/input,readonly --mount type=bind,src=<raw-output>,dst=/zap/wrk ghcr.io/zaproxy/zaproxy:<version>@sha256:<64hex> zap.sh -cmd -silent -dir /tmp/zap-home -autorun /zap/input/plan.yaml
docker --host unix:///var/run/docker.sock container inspect <owned-zap-id-or-verified-name>
```

容器不发布端口、不加入其它网络、不挂 Docker socket/宿主 home/生产 state，不转发宿主代理或凭据环境。默认镜像 tag 不可变性不足，须校验完整 digest 格式，并将本地 image ID、RepoDigests、平台、ZAP/add-on 版本纳入收据；使用固定镜像自带普通用户。目标 image 也必须事先准备且 digest 固定，不能在 launcher 里联网安装依赖。上述只读根目录、临时目录大小是待真实 host 验证的候选资源配置。

Docker `--pull=never` 在本地镜像不存在时直接失败、不创建容器；前置 inspect 失败后不能进入网络创建/扫描，也不能自动 pull。Docker socket 必须解析为获准本地 Unix socket（rootless 环境可预先登记另一确切 socket）；拒绝环境中的远端 `DOCKER_HOST`/context 改变实际 daemon。[pull 语义](https://docs.docker.com/reference/cli/docker/container/run/#pull)

URL 防线建议分两层：生成计划时只准固定 HTTP origin、固定 GET 路径；拒绝 userinfo、片段、控制字符、反斜杠、编码 authority、scheme-relative URL、未知端口/协议以及任何自由 extra argv。按解析后的 scheme/host/effective port 精确比较，不能用 `startswith`（如 `zf-target.evil`）。计划不含环境变量展开的目标、upstream proxy、远端认证或外部 `$ref`。AF context 的 urls/includePaths 是工具范围配置，不是系统网络隔离。[AF environment](https://www.zaproxy.org/docs/desktop/addons/automation-framework/environment/)

请求/重定向防线：首层只用 requestor，不解析页面链接或执行 JS，要求每个响应符合明确状态码及 body nonce，遇到 3xx 不作成功。当前 `RequestorJob` 使用 `sendAndReceive(msg)`，`BaseHttpSenderContext.followRedirects` 初值为 false；这是源码依据而非公开 requestor YAML 开关，必须对固定版本用本地 30x trap 验证，不推及 spider/API importer。[RequestorJob](https://github.com/zaproxy/zap-extensions/blob/main/addOns/automation/src/main/java/org/zaproxy/addon/automation/jobs/RequestorJob.java)、[BaseHttpSenderContext](https://github.com/zaproxy/zap-extensions/blob/main/addOns/network/src/main/java/org/zaproxy/addon/network/internal/client/BaseHttpSenderContext.java)

内网只保留 scanner/目标；无外部 DNS 需求时用 inspect 的目标 IP，另以容器 DNS 配置或只读 resolver 避免外部查询，实际有效性纳入 host 验证。网络层阻断外联是最终保障，报告检查是事后验真；不得把事后未见外域当作“请求从未发送”的证明。`-silent` 抑制更新等非用户请求，但不等同于 firewall；不使用 add-on update、OAST 或外部 callback。[ZAP 命令源码](https://github.com/zaproxy/zaproxy/blob/main/zap/src/main/resources/org/zaproxy/zap/resources/Messages.properties)

## 最小 AF 计划和扫描完成证明

固定计划含一个 context，`failOnError: true`、`failOnWarning: true`、`continueOnFailure: false`；requestor 只包含经允许的 URL 和 GET，`responseCode: 200`，再配 `type: url`、精确 url、`operator: and`、响应状态/content-type/body regex、`onFail: error`。利用 TaskStore 种下带本轮 nonce 的任务，在 `/api/state` 正文验证 nonce；错误服务即使也返回 200 也不能通过。来源：[requestor](https://www.zaproxy.org/docs/desktop/addons/automation-framework/job-requestor/)、[URL Presence tests](https://www.zaproxy.org/docs/desktop/addons/automation-framework/test-url/)。

最后执行被动等待和 `report`，明确 `template: traditional-json`、报告目录 `/zap/wrk`、唯一文件名，保留全部 risks/confidences/sites；不加改变 risk 的 alertFilter，不用 `sites` 字符串过滤代替 scope 验证（官方文档的该选项是子串匹配）。原始报告只读消费，不能在缺报告时由 helper 合成空报告。[report job](https://www.zaproxy.org/docs/desktop/addons/report-generation/automation/)

`traditional-json` 官方模板包含 `@programName/@version/@generated`，site 的 `@name/@host/@port/@ssl`，告警 `pluginid/riskcode/confidence/count`，实例 `uri/method/param/attack/evidence`；报告主要呈现告警，并非完整成功请求计数。`confidence` 通常是数值字符串，不能照合成测试的 `High` 推断真实格式。[JSON 模板源码](https://github.com/zaproxy/zap-extensions/blob/main/addOns/reports/src/main/zapHomeFiles/reports/traditional-json/report.json)

必须独立验证：本轮新建空输出目录且无同名文件/符号链接；文件为受控目录内的非空普通 UTF-8 JSON，大小受限，拒绝重复关键字段和非有限数；版本字段、所有 site origin 与预期一致，实例 URI 必须在允许范围，实例 method/count 结构一致。空 alerts **允许**表示未发现风险，但只在 mandatory 请求证据和扫描完成链成立时允许 pass；空 site、缺 mandatory、错 origin、错 nonce、借用旧报告、只有登录页/404 都是 `harness_failure`。

建议扫描收据复用现有 JSON sidecar，不扩展 canonical Task：`schema_version/run_id/profile/execution_kind`、目标 config/state/源码 digest、目标容器/网络/镜像身份、origin/mandatory endpoints、plan/policy digest、每步 command receipt refs、开始结束时间、report ref/digest、coverage proof、cleanup receipt、最终 status/reason。`execution_kind=fake` 的结果只证明启动器合同；真实扫描才可写 `execution_kind=docker-zap`。

首层 coverage proof 可由“绑定 digest 的固定计划＋mandatory URL tests＋AF 零退出＋仅在末尾生成的新报告＋目标 access log”组合证明；未直接读取的队列值或测试计数不得臆造。若以后必须产出细粒度机器证据，可在容器内控制 ZAP API：`automation.runPlan(filePath)` / `planProgress(planId)` / `stopPlan(planId)`，并读取 `core.urls()`、分页 `core.messages()`/`numberOfMessages` 与被动队列；全部 API 仅本容器 loopback，不能发布控制端口。控制器访问目标时可显式 `core.access_url(url, followredirects=False)`。这些 API 有官方依据，但不是首层必须加的新 daemon 管理框架。[AF API](https://www.zaproxy.org/docs/desktop/addons/automation-framework/)、[Python core API](https://github.com/zaproxy/zap-api-python/blob/master/src/zapv2/core.py)、[被动队列源码调用](https://github.com/zaproxy/zaproxy/blob/main/docker/zap_common.py)

## 退出码与风险预算分别判定

| 执行方式／退出码 | 官方含义 | 建议 TestKit 规则 |
| --- | --- | --- |
| Baseline/API `0` | 脚本成功 | 完成链、scope、报告均有效后再判 risk；不自动 pass。 |
| Baseline/API `1` | 至少一条配置为 FAIL 的规则 | 必须有对应配置和报告告警支撑；风险预算判定与规则 verdict 分别保留。 |
| Baseline/API `2` | 至少一条 WARN 且无 FAIL | WARN 并不等于 Low/Medium 等风险；完整可信扫描可能满足风险预算，不应一律当工具坏掉。 |
| Baseline/API `3` | 其它执行失败 | `harness_failure`，即使已有越限告警也保留“扫描未完成”主状态。 |
| AF 默认 `0/1/2` | 无执行问题／执行 error／执行 warning | 首层不用 `exitStatus`，`1/2` 归 harness；`0` 后独立用报告 risk 判产品 pass/fail。 |
| Docker `125/126/127`、信号退出、OOM、None、外层超时 | daemon/启动/进程问题；不是上述脚本规则分类 | `harness_failure`；记录 Docker State、OOMKilled 和底层 receipt，不用报告覆盖它。 |

Baseline/API 默认把告警归 WARN，可经规则配置改为 FAIL/IGNORE，`-I` 又会忽略 warning 退出；因此原始退出码、规则策略 digest 和风险预算缺一不可，不能设置 `-I` 后宣称风险通过。[Baseline 退出码](https://www.zaproxy.org/docs/docker/baseline-scan/)、[API scan 退出码](https://www.zaproxy.org/docs/docker/api-scan/)、[Docker run](https://docs.docker.com/engine/containers/run/#exit-status)

AF `exitStatus` 的 `warnLevel/errorLevel` 会将大于等于指定 risk 的告警转成 warning/error，且退出值可重定义；它会混合执行错误和风险超标，不建议首层启用。保留现有 `risk > max_risk` 作为独立判据，例如预算 1 允许 Informational/Low、拒绝 Medium/High；任何 scope/完成证据失败优先 `harness_failure`，风险发现仍可附带展示。[AF exitStatus](https://www.zaproxy.org/docs/desktop/addons/automation-framework/job-exitstatus/)

## 超时与清理

1. 每个 docker inspect/create/run/stop/rm/network rm 都有独立短 timeout；扫描有统一墙钟预算，例如 180 秒，此外限制 CPU、内存、PID、输入/报告大小。日志直写受控目录，避免 inherited PIPE 导致超时无法返回。
2. 外层超时先复用进程组终止，但 Docker CLI 退出不代表 daemon 容器消失。finally 中先对确实由本轮创建的 scanner 做有界 `docker stop --time 5 <id>`，必要时 `docker rm --force <id>`，再同样处理目标。
3. 再 `docker network rm <created-network-id>`；全部对象校验创建 receipt、run label、ID。名称冲突而未成功创建的容器不归本轮所有；禁止 name prefix 批量删、`docker system/network prune` 或全局 kill。
4. 退出前保留 container inspect/logs 和不可变 evidence；对精确 ID 做不存在检查，并确认 daemon 仍可正常查询。不能把“daemon 无响应/无权限”的非零 inspect 当作“已经删除”。清理失败保留残留 ID 和 cleanup_error，最终不能 pass。
5. 清理阶段异常不得覆盖原扫描原因；先原子写扫描结果，再在 finally 写独立 cleanup receipt 并汇总。测试 fixture/state 可按已验证精确路径清理，报告留在声明的证据目录；硬杀主 launcher 无法依赖 finally，后续回收只能凭本轮 ownership receipt 处理。

复用依据：[run_command](/home/uisee/zaofu_v7/tests/testkit/command_runner.py:94)、[k6 finally](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s10_k6_host.py:109)、[Docker stop](https://docs.docker.com/reference/cli/docker/container/stop/)、[Docker rm](https://docs.docker.com/reference/cli/docker/container/rm/)。不使用 `--rm` 时才能在结束后获取 `State/OOMKilled`，因此清理必须在所有异常路径保证执行；若选择 `--rm`，须承认退出后 inspect 证据受限。

## 建议验收（先 deterministic，再显式 host）

| step | verify: check |
| --- | --- |
| 用注入的 fake Docker executor 测预检和 argv | 公网/localhost/metadata/编码 authority、任意 image/URL/extra argv、host 网络、远端 daemon 均在扫描前拒绝；digest 错误或镜像缺失后 run 次数为 0，记录 harness_failure。 |
| fake Docker 模拟完整生命周期 | 只使用创建成功的 ID；网络 inspect 为 Internal+isolated、成员仅目标与 scanner；输入只读、输出隔离、pull=never、限额齐全、无发布端口/秘密挂载。 |
| 参数化退出与报告交叉矩阵 | 覆盖 0/1/2/3/125/126/127/137/None、timed_out、risk 0..3、畸形/空/跨站点/旧报告；尤其 `3 + High` 和 `0 + 错目标` 均为 harness_failure。 |
| 完成证据反例 | 空 alerts + mandatory 正常响应可过；只有空 alerts、零请求、错误 body nonce、3xx/401/404、partial wait、报告步骤提前执行均不可过。 |
| 受控 subprocess 集成 | 实际运行临时 fake-docker 可执行文件，记录 argv/模拟 stdout；用 `run_command` 真实收据证明无 shell、超时终止进程组、保留日志。假报告明确标记 synthetic，不能生成真实扫描覆盖声明。 |
| fake cleanup 故障注入 | startup/scan/report/inspect/rm 任一步异常及取消都尝试清理；daemon 断开不误判不存在；清理超时仍有最终收据且不删无关对象。 |
| suite 分层 | 普通 S10 只加 launcher/parser deterministic 节点；实际扫描独立 host 文件，未选 host 即“未执行”。选择 host 后缺工具/镜像必须 harness_failure，不能 skip/xfail 为绿色。 |
| 后续 opt-in host | 在自有本地 fixture 同时验证有效 nonce 正例、错服务反例、30x trap 未收到跟随请求、内部网络外联拒绝、风险阳/阴对照、passive wait 卡住触发外层超时、容器和网络无残留。所有 trap 均自有，不能向公网发探测。 |

预期命令（文件尚未实现）：`uv run pytest tests/testkit/test_zap.py tests/testkit/test_zap_launcher.py -q --no-cov`；真实层单独 `uv run pytest tests/conformance/test_zaofu_testkit_s10_zap_host.py -m host -q --no-cov`。fake 层即使全部通过，也只证明启动器/判定/清理合同，不证明 Docker 防火墙、ZAP rule 执行或 ZaoFu 安全结果。

## 未确定项与后续边界

- 本轮未 inspect 本机 Docker/镜像；不存在可核验的 ZAP digest/版本承诺。维护者须先提供本地预装的固定 ZAP 和测试 app image，核验 bundled AF/pscan/network/report 版本、rootless/Engine 能力和只读运行目录。
- 当前 AF requestor 禁止跟随重定向是源代码推导，须在所固定版本的两个自有 listener 间验证；未来加入 spider/API/auth 后重新核验其独立重定向/远端资源加载路径。global exclusions 是工具选项，不能单独替代网络隔离。[Global Exclusions](https://www.zaproxy.org/docs/desktop/addons/network/options/globalexclusions/)
- 需核验选定 ZaoFu fixture 的 mandatory API 响应、是否有 SSE/无限响应、test nonce 所在字段、预期风险规则与误报预算；不能凭一个 `/api/state` 请求声称已扫描 UI 或所有接口。
- 原始报告的 alert evidence 可能含请求数据，fixture 只能含合成凭据；报告 artifact 不自动上传。未来业务认证、API active scan、全站爬取和权限矩阵是分别授权/验证的扩展，不由本启动器 pass 隐式覆盖。

## 实施前补充：AF 精确计划与 JSON 边界（2026-09-14）

以下 YAML 按官方 help 和当前源码核验，尚未执行 ZAP。预条件是 `zf-target:8002` 已验证属于本轮服务，fixture 通过 TaskStore 创建任务 `TASK-ZAP-0123456789abcdef0123456789abcdef`，`GET /api/state` 返回含该任务 id 的 JSON；示例 nonce 必须由 launcher 每轮重生并在序列化前统一替换，不能照抄或让 AF 通过环境变量补 target。该计划只验证这个端点，不扩展为 UI 或权限扫描。

```yaml
env:
  contexts:
    - name: zf-owned
      urls:
        - http://zf-target:8002/api/state
      includePaths:
        - '^http://zf-target:8002/api/state$'
  parameters:
    failOnError: true
    failOnWarning: true
    continueOnFailure: false
    progressToStdout: true
jobs:
  - type: requestor
    name: mandatory-read
    enabled: true
    alwaysRun: false
    requests:
      - name: read-seeded-state
        url: http://zf-target:8002/api/state
        method: GET
        httpVersion: HTTP/1.1
        responseCode: 200
    tests:
      - name: state-200-json-owned-nonce
        type: url
        url: http://zf-target:8002/api/state
        operator: and
        requestHeaderRegex: '\AGET[ \t]+'
        requestBodyRegex: '\A\z'
        responseHeaderRegex: '(?im)\AHTTP/[0-9.]+[ \t]+200(?:[ \t]|\r?$)(?=[\s\S]*^Content-Type:[ \t]*application/json(?:[ \t]*;[^\r\n]*)?[ \t]*\r?$)'
        responseBodyRegex: '"id"\s*:\s*"TASK-ZAP-0123456789abcdef0123456789abcdef"'
        onFail: error
  - type: passiveScan-wait
    name: passive-complete
    enabled: true
    alwaysRun: false
    parameters:
      maxDuration: 0
  - type: report
    name: write-final-report
    enabled: true
    alwaysRun: false
    parameters:
      template: traditional-json
      reportDir: /zap/wrk
      reportFile: zap-0123456789abcdef0123456789abcdef.json
      reportTitle: ZaoFu TestKit 0123456789abcdef0123456789abcdef
      reportDescription: run_id=0123456789abcdef0123456789abcdef
      displayReport: false
```

精确层级：`env.parameters` 放运行策略；`env.contexts` 为列表，context 的 `urls/includePaths` 是列表。`requestor.requests` 和 `requestor.tests` 都是 job 的直接子项；HTTP 字段名为 `method/url/httpVersion/responseCode`，不是 `statusCode`；测试字段为 `requestHeaderRegex/requestBodyRegex/responseHeaderRegex/responseBodyRegex`，没有独立 `contentType` 或 `responseStatusRegex`。status mismatch 在 requestor 里产生 warning，因此本计划必须 `failOnWarning: true`。[environment](https://www.zaproxy.org/docs/desktop/addons/automation-framework/environment/)、[requestor](https://www.zaproxy.org/docs/desktop/addons/automation-framework/job-requestor/)、[URL tests](https://www.zaproxy.org/docs/desktop/addons/automation-framework/test-url/)

这里明确写四个 test regex 是必要的源码兼容处理：当前 `UrlPresenceTest` 的四个 Data 字段初始为 null，`checkPattern(null)` 返回 false，`operator: and` 联合四个检查；只提供两个 response regex 会使另外两个 null 阻断成功。上例 request body 用 Java 正则 `\A\z` 要求为空，header 用 `\AGET` 校验方法。response header 的一个正则同时约束 200 与 `application/json`（容许 charset 参数），body 另约束 nonce；全部通过才能成功。源码用 Java `Pattern.matcher(...).find()`，不能用 Python `re` 的可解析性代替 Java 兼容性检查。[UrlPresenceTest.java](https://github.com/zaproxy/zap-extensions/blob/main/addOns/automation/src/main/java/org/zaproxy/addon/automation/tests/UrlPresenceTest.java)、[其上游测试显式传空字符串的例子](https://github.com/zaproxy/zap-extensions/blob/main/addOns/automation/src/test/java/org/zaproxy/addon/automation/tests/UrlPresenceTestUnitTest.java)

`url` test 从 Sites Tree 的该 URL 取 history message，不证明所有同 URL 请求均满足条件；必须每轮新 ZAP home/session、单次固定 GET、不导入旧 session。本例 `includePaths` 也不构成传输防火墙。预期正文若不以 `id` 承载任务，应先核实 fixture 的实际 API schema，再替换为对应字段约束；不能改成仅匹配“任何正文里出现 nonce”。

容器内调用精确形状：`zap.sh -cmd -silent -dir /tmp/zap-home-0123456789abcdef0123456789abcdef -autorun /zap/input/plan.yaml`。`-cmd` 等待命令完成后退出，`-dir` 指定新的 ZAP home，`-silent` 抑制主动更新等额外请求；`-autorun` 由 AF add-on 提供。未放 `exitStatus` job、未注入 exit override 时，AF 正常/执行 error/仅 warning 的默认退出码为 `0/1/2`；单有风险告警并不自动变成 AF 执行 error，仍须由 Python 报告 risk gate 判定。[ZAP 命令定义](https://github.com/zaproxy/zaproxy/blob/main/zap/src/main/resources/org/zaproxy/zap/resources/Messages.properties)、[AF 命令和退出码](https://www.zaproxy.org/docs/desktop/addons/automation-framework/)、[ExtensionAutomation.setExitStatus](https://github.com/zaproxy/zap-extensions/blob/main/addOns/automation/src/main/java/org/zaproxy/addon/automation/ExtensionAutomation.java)

`passiveScan-wait.maxDuration: 0` 的单位是分钟、0 表示无限等当前队列完成；不用内部截止，由外层硬 timeout 截断整个进程。report 固定最后且 `alwaysRun: false`，不省掉 mandatory URL tests，也不允许 failOnWarning/continueOnFailure 被改写。[passiveScan-wait](https://www.zaproxy.org/docs/desktop/addons/passive-scanner/job-pscanwait/)

**nonce 元数据更正：`traditional-json` 不输出 `reportTitle/reportDescription`。** ReportJob 接受这两个参数并写入 ReportData，但该模板没有渲染它们；不能要求 JSON 根对象出现 `title/description/run_id`，也不能往原始报告补写伪装成 ZAP metadata。模板顶层 `@programName/@version/@generated/created` 不含自定义 nonce；无告警时也没有实例 URI/evidence 可借来绑定 nonce。真实绑定来自唯一文件名、新目录、固定 plan digest、mandatory body test 和 report/receipt digest；title/description 只留在计划侧。若要求报告正文自身必含 nonce，当前原版模板不能满足，须另行选择受控模板或独立证据 sidecar，不能假设已有。[JSON 模板](https://github.com/zaproxy/zap-extensions/blob/main/addOns/reports/src/main/zapHomeFiles/reports/traditional-json/report.json)、[ReportJob.java](https://github.com/zaproxy/zap-extensions/blob/main/addOns/reports/src/main/java/org/zaproxy/addon/reports/automation/ReportJob.java)

文件名用完整小写 `.json` 后缀可避免自动追加；ReportJob 仅在文件名不以模板扩展名结尾时追加 `.json`。原始 JSON 中 site 的 `@name/@host/@port/@ssl` 都以字符串输出，`@ssl` 是 `"true"/"false"`，不能用 `bool("false")`，也不能要求原生 JSON boolean。固定模板入口可严格收字符串；通用适配器若兼容原生 boolean，应明确支持两种形态并按值正规化，其它值拒绝，再与 URL scheme/有效端口交叉核对。[传统 JSON 官方样例](https://www.zaproxy.org/docs/desktop/addons/report-generation/report-traditional-json/)

`count` 是告警实例计数，不是 HTTP 请求数，也不是扫描覆盖数；`instances` 是报告实际列出的告警证据。当前 AF traditional-json 模板的 `count` 明确取同一 `instances.size`，但不可推广为所有 ZAP 报告的通用 `count == len(instances)` 规则：核心旧报告路径存在 `maximumInstances` 截断输出、仍累计总 count 的实现。建议通用 parser 分别保留 `reported_count` 和 `listed_instances`，不以相等作为扫描完成判据；仅在镜像/模板版本锁定且证实语义时才加专用一致性检查。上文“method/count 结构一致”不意味着跨格式强制相等，计数缺失/截断也不能由 helper 补造成完整扫描证据。[AF JSON 模板](https://github.com/zaproxy/zap-extensions/blob/main/addOns/reports/src/main/zapHomeFiles/reports/traditional-json/report.json)、[ExtensionAlert 的 maximumInstances/count 分离](https://github.com/zaproxy/zaproxy/blob/main/zap/src/main/java/org/zaproxy/zap/extension/alert/ExtensionAlert.java)

补充验收边界：直接测试生成计划的字段层级、三任务顺序、四个非 null regex、`responseCode=200`、`maxDuration=0`、`alwaysRun=false`、无 exitStatus；使用真实模板字段形状的合成报告（不含 title/description，`@ssl="false"`）验证可解析；对空报告、错 nonce、错误 content-type、301/401/404、被替换计划仍拒绝 pass。该 YAML 是经源码审查的执行候选，固定镜像所带插件版本、Java 正则实际表现和 fixture 响应形状仍待显式 host 验证，本次没有执行这些验证。
