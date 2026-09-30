# ZaoFu 测试框架已完成部分梳理

> 2026-09-30 文档核对：方案基准为 [ZaoFu_TestKit.md](ZaoFu_TestKit.md)，实施状态以 [steps.md](steps.md) 顶部 K613 暂停快照为准；产品业务能力另见 [产品框架梳理](productOfzaofu_done_part.md)。本文历史结果不代表重启后重新运行；K597 34 项审计、K599 单项审计、K612 S1 与 K613 聚焦验证各有独立范围，不能混算比例。测试能可靠检出问题是能力成果，但 harness failure 仍需独立定位。备份路径见 [重启交接](../05_restart_handoff/README.md)。

更新时间：2026-09-30（保留原有结构，增补 K598～K613；本次仅整理已有证据，未重新运行测试）

## 1. 统计口径

本文件统计“测试框架已经具备并实际使用过的执行、取证、问题发现和结果归类能力”，不是被测的 ZaoFu 是否全部通过。

- 框架通过：测试入口、断言、证据绑定、清理和报告分类本身成立，当前场景未检出新的 ZaoFu 缺陷。
- 检出 ZaoFu 问题：测试成功运行并保留了可复现红灯；问题编号引用 ZaoFu_issue.md。
- 测试框架问题：测试代码、夹具、报告绑定或诊断层有问题；不归因于 ZaoFu 生产代码。
- 环境/证据阻断：框架明确拒绝把缺少 Provider、原始报告或同源快照的场景判绿。

同一技术面的多个 K 阶段合并为一行，避免把重复回归次数当成新增功能；完整阶段细节仍以 steps.md 对应 done 章节和附件报告为准。

## 2. 已完成测试能力与结果

| 阶段/模块 | 使用技术和入口 | 具体测试内容 | 结果归类 | Issue/证据 |
|---|---|---|---|---|
| K4/S5 Codex canary | CodexHeadlessBackend、app-server initialize/thread/start/resume/turn、真实 transcript、host+real_provider marker | 首轮启动、同 session resume、usage、marker、短 timeout、子进程关闭、工作区不变 | 框架通过；Provider 偶发断流正确归为外部环境 | K4；test_zaofu_testkit_s5_real_provider.py |
| K5/K12/K16/S10 k6 | digest-pinned grafana/k6、真实 zf web、pressure/spike/stress/soak、阈值解析器 | /health、/api/state、VU 峰值、checks、HTTP error rate、p95/p99、request count、容器/Web 清理 | 框架适配问题已修复；负载测试可执行 | K5/K12/K16；k6 parser 问题不是生产 Issue |
| K6/K8/K10/K14 发布安装 | uv build、wheel/sdist、Python 3.11/3.12/3.13 clean venv、隔离 HOME/XDG/PYTHONPATH | base/dev/web/stream-json/feishu extras、zf help、init/validate/doctor/start/stop、state_dir、不借用源码路径 | 框架通过并成功检出发布缺陷 | ZF-TEST-046：base/web/stream-json 缺 httpx，open |
| K7/K26/K40/K46 Web 交互 | Docker mcp/playwright、Vite/API、隔离 state、fake provider、DOM+EventLog | Channel Group、Kanban Agent、提案确认/Create task、canonical task 回查、DOM/tool-use hydration、进程组清理 | 交互审计通过；selector、DOM 证据和遗留子进程问题已修正 | ZF-TEST-086 测试框架问题已修复 |
| K11/K15/K28/K31 真值链 | TaskStore、EventLog、projection rebuild、FastAPI、SSE、第二浏览器、DOM | Store→EventLog→read-model→API→SSE→DOM；archive、freshness、cursor、reload、token action、断线重连 | 框架通过，并成功保留间歇红灯 | ZF-TEST-009/010/011/012/060/061/062 |
| K13 兼容性 | YAML/JSONL/StatePacket、旧 archive 合并读取、unknown-field 策略 | v1 zf.yaml、state_dir 不漂移、旧 EventLog、同主版本未知字段容忍、跨主版本 fail-closed | 框架通过；没有虚构迁移/回滚能力 | K13；test_zaofu_testkit_s10_compatibility.py |
| K9/K47/K49–K188 mutation | mutmut 3.7.0、隔离副本、source SHA、严格 mutation parser | EventSigner 21/21 killed；PathGuard 131 中 76 killed、55 survivor；EventLog/Store/Run Manager/ControlledAction/Recovery mutation | 框架能执行并严格暴露 survivor；survivor 保留为测试断言缺口 | K9、K47、K49–K188 |
| K17/K22/K23/K24/K27/K36/K39 CLI/Provider 能力 | dev-premerge-gate、stream-json、tmux、CLI help、无 Provider host 矩阵 | 参数、无 CLI 冷启动、profile/config/rules/presets/skills、status/events/kanban/projection/metrics 只读命令 | 框架通过；受限启动问题准确检出 | ZF-TEST-048 已由 K595-B 修复 |
| K20/K33–K38/K48/K198–K202 Provider/tmux | tmux pane、真实/伪 Provider、session/transcript、timeout、SIGKILL/restart、进程组 | 四流程启动、Codex/Claude tmux 边界、pane kill/restart、timeout fail-closed、cwd/身份/transcript、清理 | 具备真实 Provider/宿主观测；真实长任务仍受认证/CLI条件约束 | ZF-TEST-068/069/070/071/072/073/074/075/076/077/078/079/080/089/090/091/102/103/104/105/106/107/108 |
| K25/K367/K368 浏览器/宿主探针 | Docker、端口/代理隔离、multi-browser、进程树 kill | 浏览器/Docker 能力、Vite/API/Playwright 清理、出站/重定向隔离、多浏览器边界 | 框架通过；清理探针可以检出宿主残留 | K40/K46 测试框架清理修复 |
| K29/K41–K45/K369/K399/K426–K429 ZAP | OWASP ZAP digest pin、Automation Framework、baseline、OpenAPI safe/active、spider | 未认证/已认证 OpenAPI、baseline、active、spider、URL 发现、高风险阈值、真实退出码 | 框架能执行并拒绝把超时/非零退出码判绿；观察范围 High=0 | active scan 124/1 退出被正确归为 harness_failure |
| K30/K404 性能 soak | k6、资源采样、固定预算和清理 | 10 分钟/2 小时等有界 soak、HTTP/p95/p99、资源曲线、FD/PID/临时目录 | 短中窗口框架能力完成；PERF-05 长窗口未伪造 | K552-F2/PERF-05 按研究条件后置 |
| K42/K43 API 安全 | ZAP replacer、X-ZF-Web-Token、OpenAPI | 未认证/已认证路由、权限注入、安全头、高风险门禁 | 框架通过；Medium/Low 与 High 分层保留 | K42/K43 |
| K46/K49–K55 状态与事件 | EventLog/EventWriter、TaskStore/FeatureStore/SessionStore/RoleSessionRegistry、atomic write、crash injection | append/fold/index、重复 ID、CAS、归档/轮转、session identity、evidence gate、ControlledAction attempt | 框架通过并稳定复现故障边界 | ZF-TEST-001/007/008/013/014/015/016/018/020/032/039/041/042 |
| K56–K80 recovery/action | Run Manager、TaskAttempt、watchdog、resident/restart、repair、workflow resume、mutation | lease、预算、终止、repair、candidate rework、resume、operator action、terminal settlement、重放幂等 | 框架通过；机械 gate 和 mutation survivor 可观测 | ZF-TEST-029/033/038/051/052/053/054/055/056/057/063/067 |
| K81–K145 Store/Action | Store/Session/Role registry、ControlledAction router、plan/rework/resume | CAS、身份绑定、角色生命周期、workflow request、operator surgery、candidate rework、Plan apply、timeout/receipt | 框架通过；结构化拒绝和缺身份 fail-closed 生效 | ZF-TEST-049/050/051/052/053/054/055/056/057 |
| K146–K188 Orchestrator mutation | Orchestrator/Run Manager/ControlledAction 全模块 mutation、direct caller、mock E2E | dispatch/fanout/lifecycle、recovery、rework、action settlement、terminal evidence、agent reactor、因果/预算 | 框架能把 survived mutation 保留为测试缺口 | ZF-TEST-058 仍保持开放 |
| K191–K298 module-parity | module-parity、flow-discovery、gap-plan、judge evidence gate、canonical projection、OA run-plan | flow/gap identity、scope/filter、request/closure、terminal envelope、candidate verification、plan fingerprint、task-map/fanout、replay/dedup | 确定性正反例和 fail-closed 边界已可测 | ZF-TEST-059 非 mapping payload 被精确检出 |
| K299/K354–K410 当前 HEAD 复核 | S0/S6/S9、CLI/API/Web、browser、source SHA、精确 nodeid、ZAP | 当前源码重复复现开放 Issue；registry、shared delivery gate、浏览器 route、报告 source 绑定 | 框架通过；红灯没有被 retry、删节点或改阈值隐藏 | ZF-TEST-040/048/059/060/061/062 |
| K411–K418 核心桥接 | verify→flow-discovery、verify→parity、candidate-ready、S4 recovery、local Git delivery | bridge request/closure、candidate freeze、generation/replay、跨进程恢复、本地 candidate→controlled merge→post-merge | 确定性核心闭包通过；远端 merge 不伪造 | K411–K418、K430 |
| K419/K424 统一入口 | flow-smoke、fake provider、核心 API/CLI/Web、S0/S6 impact closure | 单一入口启动、fake Provider、报告、清理、共享 caller 影响闭包 | 框架通过；局部红灯按严格分类保留 | K419/K424 |
| K420/K421/K423/K585 Provider 诊断 | capability probe、真实 Claude canary、tmux shell handshake、transcript、计时诊断 | CLI 发现、登录、start/resume、timeout、transcript、nonce、握手、清理 | 框架可区分 CLI 缺失、认证、握手失败、超时 | ZF-TEST-068/072/074/079/102/103/104/107 |
| K422/K571-D4 premerge | dev-premerge-gate、structure-discipline、JUnit/source 绑定、归档回读 | 超大文件、未登记 source、premerge 结果、归档后路径和 SHA | 历史运行中框架 fail-closed 并保留结构红灯；当前台账显示 040 已在 K593-G3 修复，但这不等于运行时全量验收 | ZF-TEST-040：结构门禁问题；premerge 回读误判已修 |
| K425/K438/K443 Web 分层 | legacy /api/events、moving watermark、fresh route、WAL | malformed/cursor、freshness、fallback metadata、跨进程 WAL、空页/500、cold rebuild | 框架可稳定产出确定性/间歇性红灯 | ZF-TEST-009/010/011/012/060/061/062 |
| K430/K433 交付边界 | candidate preflight、controlled merge、post-merge regression、本地 Git fixture | candidate 内容、target commit、merge gate、post-merge impact、重放幂等、source SHA | 本地/模拟交付边界通过；K430/K433 报告中的 `ZF-TEST-020` strict xfail 是历史证据，当前台账已标 fixed；远端 PR/merge 不伪造 | `/issue-merge` 按要求不设计 |
| K432–K435 S8/S9/S4 | workflow/candidate evidence、S9 delivery、S4 crash/recovery/exactly-once | request→reader/writer/synth→verify→candidate→delivery、hard crash、replay、唯一终态、跨层 gate | 确定性闭包通过，并保留生产红灯 | ZF-TEST-002/003/006/007/022/033/034/035/036/037/038/039/042/047/058 |
| K450–K468 blocking/retry | retry exhaustion、Hypothesis、operation generation、TaskAttempt、semantic submit/settlement | retry 耗尽、generation fence、lease、迟到结果、旧轮次接管、新 task-map、批准恢复、candidate freeze | 模型和 mock caller 可测；不以模型结果替代真实 Provider | ZF-TEST-063/065/066/067/073/075/076/078/080/089/090/091 |
| K500–K513 原生恢复与来源绑定 | Claude 双阶段、CLI stop/start、resume、candidate、五份报告 source binding | impl/verify 中断、cwd/session/transcript、candidate/Task done、归档、幂等、清理、跨报告 source | 框架完成来源绑定和汇总；旧报告不回填当前证据 | ZF-TEST-072/074/075/076/078/079/080/083/084；框架问题 081–084 |
| K530/K531 watchdog | 正式 zf start、Watcher、tmux/scripted worker、PID、stdin、watchdog、retry | Issue live worker、首次异常收据、retry briefing 身份、respawn、Task/Judge/candidate/receipt/restart | 框架能保留首次退出边界；不拿正常 retry 推断根因 | ZF-TEST-089 fixed；K530 首次根因仍开放 |
| K535–K539 shared runtime | signed worker、Product Acceptance、catalog、双 lane、formal scope、run admission | PRD/Refactor 两 lane、scope、task collision、config/contract drift、FIFO、容量释放、General+Issue 交错、receipt/dossier | 确定性并发和交付合同完成；全场景/owner 实收未冒充完成 | ZF-TEST-092–099 已检出并 fixed |
| K541/K564–K571 整机恢复 | owned worker/watchdog、SIGKILL、同盘 QEMU、full CLI start、EventLog/receipt durability | worker 接管、preserve/full-start、EventLog 前缀、workflow input、六 receipt、唯一终态、再次 startup 幂等、QEMU 清理 | 框架成功检出并驱动最小修复；修复后对应回归通过 | ZF-TEST-111/112 fixed |
| K576–K591 VM/guest panic | QEMU/OVMF/guest serial/QMP、panic nonce/UUID、两 boot、overlay、provenance | owned-QEMU SIGKILL、guest panic、同盘恢复、firstboot/boot_id/checkpoint、QMP/serial/cleanup/幂等 | 能力探针和一条 guest-panic acceptance 完成；不外推物理掉电/全 OS | K590 夹具 QMP/UUID 问题已修，未新增生产 Issue |
| K592/K594/K547 原生 Codex queue | opt-in native node、私有 CODEX_HOME、tmux、自然 180 秒占槽、CLI gap recovery | prompt/stop、Provider slot、自然 timeout、只恢复缺口、transcript/进程/candidate、权限目录/argv、离线正反例 | 离线合同完成；最新按操作者要求跳过原生执行，只保留方法，不计通过 | ZF-TEST-048 fixed；K600/K610：SKIPPED_BY_OPERATOR_ENVIRONMENT |
| K595-B | Codex 0.155.1 私有配置、on-request/never、sandbox/allowed_paths、user hooks、spawn 前 app-server probe | restricted worker 启动、配置来源、五 hook trust、argv、失败拒绝、进程组回收 | 框架/适配器阻断已修复；旧静态红灯保留 | ZF-TEST-048 fixed |
| K596 | 独立 literal command、私有 Codex hooks/list、currentHash、fixture SHA、反例变更 | 十个 hook hash 来源、精确命令字节、Stop 单项 hash 变化、版本/发现范围 | fixture 来源问题 fixed；未外推实际 hook 执行/审批/恢复 | K596-F1 fixed（测试 fixture） |
| K597 | requirement-audit CLI、JSON/JUnit/phase/source SHA、source profile、审计器正反例 | 旧 32 条身份/ref/SHA 保留；并列 K595-B 红报告/K596 闭包；拒绝普通 JUnit 伪装 premerge；校验 scope、same revision、发布门禁 | 审计/证据接线完成；pass/fail/missing/harness_failure 均被保留并分类 | K597；attachments/k597-evidence-reconciliation-20260929 |

## 3. 测试成功检出的主要 ZaoFu 问题

| 问题族 | 测试具体检出的行为 | Issue 引用 | 台账状态 |
|---|---|---|---|
| 发布依赖 | clean venv 的 base/web/stream-json 执行 zf help 缺 httpx | ZF-TEST-046 | open |
| Web/read-model | malformed/cursor、freshness、legacy events、WAL、空页/500 | ZF-TEST-009/010/011/012/060/061/062 | 部分 fixed，部分 open |
| EventLog/恢复 | append 前缀、归档碰撞、重复事件、dispatch context、TaskStore 提前推进 | ZF-TEST-001/002/007/013/014/015/016/032/034/035/039/041/042 | 部分 fixed，部分 open |
| 并发准入 | scope、target_root、Task collision、重复拒绝、active contract | ZF-TEST-092–099 | fixed |
| worker/transport | 迟到结果、子进程树、respawn、cwd/session/transcript、ready 误判 | ZF-TEST-063/066/067/068/069/070/071/072/073/074/075/076/077/078/079/080/089/090/091/102–108 | 多数 fixed，按台账为准 |
| workflow/fanout | retry 身份、synth barrier、rework/replay、一次性事件、operation hash | ZF-TEST-031/033/036/037/038/045/047/058/065/088/109/110 | 部分 fixed，部分 open |
| VM/整机恢复 | EventLog 前缀、workflow input、receipt 持久性、guest panic 边界 | ZF-TEST-111/112；K590/K591 夹具记录 | 111/112 fixed；其他 OS 模型未覆盖 |
| 结构/发布门禁 | 超大文件与未登记结构债务导致 premerge fail | ZF-TEST-040 | 被测门禁已检出，不能因框架会报错而视为生产已修复 |
| Codex 兼容 | -a untrusted 不被 Codex 0.155.1 接受；静态 hash fixture 来源错配 | ZF-TEST-048；K596-F1 | fixed |

## 4. 已修复的测试框架自身问题

| 框架问题 | 修复内容 | 证据 |
|---|---|---|
| ZF-TEST-081 | 原生报告无法保留/分类 premerge 失败 | K511/K512 |
| ZF-TEST-082 | S0 审计不支持精确 nodeid，负向夹具依赖 Issue 状态 | K512/K554 |
| ZF-TEST-083 | 来源绑定误分类失败、漏检跨报告矛盾 | K512/K595/K597 |
| ZF-TEST-084 | premerge wrapper 零退出码误报通过 | K513/K571-D4 |
| ZF-TEST-086 | 浏览器后台任务与当前用户任务混淆 | K7/K20 |
| ZF-TEST-077 | 一次性 lease 注入被测试心跳续写 | K510/K531 |
| ZF-TEST-074 | 原生超时先报归档断言，掩盖真实未完成原因 | K500/K510 |
| K5 parser | k6 passes/fails 没有归一化为 rate | K5 |
| K40/K46 cleanup | 只杀父 shell，遗留 Vite 子进程 | K40/K46 |
| K590 F1/F2 | QMP Path 参数和无连字符 UUID 使探针错误判定 | K590/K591 |
| K571-F6 | 测试 receipt 只 replace 未 fsync，验收侧读到空文件 | K571-F6 |
| K585-D | PRD handshake 缺少有界诊断和计时证据 | K585 |
| K595-B/K596 | restricted argv 和 hook hash fixture 来源问题 | K595-B/K596 |

## 5. 阶段性结论

已完成的是一套能真实发现问题的分层测试框架，而不是只追求全绿的脚本集合：

1. S0 registry 能选择精确 nodeid，S1–S10、CLI/API/Web、Provider、tmux、Docker、k6、ZAP、VM/QEMU 均有对应入口。
2. 能保留 JSON/JUnit/phase/source/transcript/serial/QMP/receipt 等证据，并用 SHA、source profile、same-revision 和 scope 规则绑定。
3. 能区分框架通过、ZaoFu 红灯、测试框架自身问题、环境阻断和证据缺失。
4. 已检出的问题全部引用 ZaoFu_issue.md；已修复和仍 open 的状态不被混写。
5. /issue-merge、Feishu 联调和明确降级事项没有被设计或伪造为完成。

K598/K599 已补齐当时完整 MAINLINE 报告及其单项同源审计，不再把这部分列为未完成。原生 Provider 按操作者要求跳过；同签名新现场、历史原件缺证、性能长窗口、破坏性 OS 实际验收和最终统一验收仍开放。K613 收尾后已暂停构建；这些边界不否定已完成的框架能力，也不能由方法合同通过推断实际验收通过，详见第 9 节。

## 6. 细化执行矩阵（按测试目标拆开）

下面把前表中合并过的技术面进一步拆成“被测对象 → 入口 → 断言 → 结果”的可回读条目。数字是该阶段实际报告数字，不把不同阶段、重叠调用方或历史报告相加。

### 6.1 基础执行、报告和安装层

| 被测对象 | 具体执行与断言 | 实际结果 | 归因/当前状态 |
|---|---|---|---|
| S0 注册与报告 | `python -m tests.testkit --stage S0`；检查 registry nodeid、stage、source、JUnit/JSON、session_finished、清理和唯一键。 | K424/K425/K427/K428/K429 等多次 **11/11 passed**；K579 按新增注册面为 **19 passed**。 | 测试框架基础门禁已可执行；S0 绿不代表被测 ZaoFu 全绿。 |
| S1 普通状态取证 | setup/call/teardown、pytest fail、skip/xfail/XPASS、timeout、SIGTERM/SIGKILL、phase facts、snapshot ownership、嵌入文件摘要。 | 核心取证联调曾达 **175 passed**；普通 evidence adoption 达 **430 passed/15 strict xfailed**。 | 取证合同通过；未宣称覆盖所有 async/xdist/custom protocol 组合。 |
| S4 recovery suite | dispatch commit point、action loss、archive collision、multiprocess dispatch、workflow resume、fanout/provider-stop、TaskAttempt、stale inflight。 | K434：**158 reported，145 passed、13 strict xfailed**；13 个 xfail 映射 002/003/007/013/018/032。 | 生产 recovery 红灯被保留；测试框架没有把 strict xfail 改成 skip。 |
| S6 共享影响闭包 | EventLog/Store、CLI/API/SSE/Web/projection、workspace/terminal、Schemathesis、browser truth-chain 的跨边界调用方。 | K425：**910 collected/914 reported，906 passed、8 strict xfailed**；K444：**916 reported，908 passed、8 strict xfailed**。 | 8 个 xfail 是已登记问题；没有新的普通失败/collection/timeout。 |
| S8 工作流证据 | Issue/Research/triage admission、reader/writer/synth、Goal/Task settlement、candidate acceptance/preflight、artifact/run archive、runtime-noise。 | K432：**219 reported，208 passed、11 strict xfailed**；xfail 映射 006/036/037/044/045。 | 工作流证据注册面已执行；不是完整 Provider/远端交付证明。 |
| S9 本地交付 | Issue ledger、regression artifact、candidate preflight、Fix Run、ShipService、controlled merge、post-merge regression、triage mirror。 | K430/K433：**79 reported，78 passed、1 strict xfailed（020）**。 | 本地 candidate 链可测；远端 PR/merge/push 明确不在范围。 |
| S10 兼容/发布 | wheel/sdist、clean target import、YAML/JSONL/StatePacket、旧 archive 合并读取、unknown field、extras 元数据。 | 兼容合同与 wheel/sdist 入口可执行；完整 extras/Python 独立安装矩阵仍未声称完成。 | `ZF-TEST-046` clean-venv httpx 缺失仍是产品发布问题；`--no-deps` 不能冒充完整安装。 |

### 6.2 EventLog、Store、投影与 Web 真值链

| 被测对象 | 具体测试内容 | 结果与问题引用 |
|---|---|---|
| EventWriter/EventLog append | 重复 event id、同 ID 冲突、签名/append-fold、archive rotation、归档碰撞、重开读取和 source/ref 绑定。 | 能稳定复现并保留 001、013、039；002/007 的 crash window 由 S4 strict-xfail 保留。 |
| Task/Feature/Session/Role Store | wrong-root/非空错误根、CAS、文件锁、归档/owner reopen、Feature/Session 两进程非冲突更新。 | 613 passed/29 strict xfailed 的 K2 Store 闭包曾检出 041/042；当前台账仍以 open/fixed 状态为准。 |
| 投影重建 | 单份 spine 丢失、全量 rebuild、旧 EventLog 实例追加、Index 顺序、Kanban `created_at`、freshness/source metadata。 | 035、034、011、012 被分别保留，避免把索引漂移合并成一个问题。 |
| `/api/events` | malformed JSON 行、`limit=2&cursor=0` 第一页 seq `[1,2]`、下一页 seq `[3]`、读模型滞后时 EventLog fallback。 | K425 20 次独立进程 **16 passed/4 failed**；失败是 `items=[]`，对应 062，仍 open。 |
| Traces 页面 | canonical route、scoped list/detail/history/spans/raw、cursor、waterfall、degraded child、empty trace、deep-link、820/600 responsive。 | K395/K396 深层旅程通过（合规三浏览器 **6 passed**），但首屏旧 `/events` 请求仍对应 060。 |
| Web 输入边界 | 非法 artifact `view=http://...`、4096 字符 profile path、合法输入对照、4xx/500 分类。 | K426：**65 passed、3 strict xfailed**；裸红 061 为真实 HTTP 500，不是测试环境失败。 |
| Store→API→SSE→DOM | canonical Store/EventLog 到 read-model、FastAPI、SSE reconnect、DOM convergence、token-gated action、refresh 后一致性。 | `test_testkit_browser_truth_chain.py` 与 full-stack scorecard 可执行；synthetic fanout/empty state 被拒绝。 |

### 6.3 工作流、候选和并发隔离

| 被测对象 | 具体测试内容 | 结果与 Issue |
|---|---|---|
| Issue 默认路线 | `request/proposal/submit`、light TaskContract、writer commit/必读回执、candidate quality、verify、Thin Judge、Goal/Task done；负向包含缺必读文件、旧 commit、blocked Judge、自测失败、错误归属假绿。 | K2 Issue route 以 11 个 case 覆盖 8 个控制通过和 3 个旧 candidate replay 红灯；对应 045，不能只看 worker 自测绿灯。 |
| Research/replan | fixed/adaptive failure、generation、同目录重建、reason 改写、invoke generation gate、重复失败唤醒。 | 044 被确定性检出；S8 联调报告中的 036/037/044/045 作为既有 xfail 保留。 |
| Product Acceptance | spec/report/journey evidence、evidence ref/digest、candidate identity、target commit、stale candidate、restart replay 幂等。 | 缺 acceptance 时 candidate gate 拒绝；完整绑定才可进入 candidate，但 `merge_authorized` 仍独立。 |
| Shared runtime contract | General→Issue→PRD→Refactor FIFO、active contract、配置内容身份、scope 透传、容量/重叠、Task owner collision、writer scope、planner scope。 | 092/093/094/095/096/097/098/099 均由正式入口检出；台账现均 fixed，保留原始 red 和修复后的 impact closure。 |
| 双 lane | 两个 source/worktree、互斥 allowed_paths、writer/verifier barrier、同 candidate SHA、反序/重建、组合 probe、两 Task done。 | K536 最终 **2 passed/21.82s**；缺 lane verify 时不产生 ship/terminal。 |
| 双 lane live | CLI/Watcher/tmux/Docker browser、12 物理收据、13 次准入、candidate/ship/local delivery、restart/cleanup。 | K537 host **1 passed/241.86s**，Docker **1 passed/162.42s**；不关闭 K557-F2 长时稳定性。 |

### 6.4 Provider、tmux、浏览器和整机恢复

| 被测对象 | 具体测试内容 | 实际结果与边界 |
|---|---|---|
| Codex/Claude canary | CLI discovery、initialize/config/read/hooks/list、session identity、resume、usage、transcript、模型/认证和 exit code。 | K421/K423/K592/K594/K595-B/K596 保留历史离线或显式 host 证据；K600/K610 后两项原生链均按操作者要求跳过。本次未检查登录/安装，不沿用历史“未登录/CLI 缺失”作为当前事实。 |
| tmux readiness | nonce handshake、3 秒 deadline、shell/foreign/stale pane、owner/pane guard、capture `-120/-200`、stderr/non-zero/TmuxError fallback。 | K579 host **6 passed**；K580 直接影响闭包 **138 passed**、真实 capture **14 passed**；候选仍未证明完整语义等价。 |
| Worker lifecycle | ready 误判、stream-json timeout、子进程树、respawn、cwd、transcript 路径、graceful stop、迟到结果、retry briefing。 | 068–080、089–091、102–108 逐项有入口；多数已 fixed，但 fixed 只关闭对应窗口，不代表原生主链全通过。 |
| Browser live | Docker Playwright、真实本地 `zf start`、Watcher/tmux、browser approval、same Run、Task/terminal、restart、cleanup。 | K533–K537 形成 PRD/Refactor/strong/two-lane 证据；K584/K585 仍显示 PRD/strong 某些重启/延迟窗口需单独归因。 |
| 整机 SIGKILL | owned worker、Watcher 接管、preserve/full-start、EventLog prefix、workflow input、receipt durability、再次 startup 幂等。 | K541/K571 驱动 109–112 修复；原始缺陷已标 fixed，K571-F6 测试 receipt 持久性另行保留。 |
| VM/guest panic | QEMU/OVMF、guest serial/QMP、panic nonce/UUID、两次 boot、overlay、boot_id/checkpoint、source/provenance、cleanup。 | K590/K591 完成 guest-panic→同盘 full-start acceptance；不外推物理掉电、所有 OS 或历史缺失报告。 |

### 6.5 安全、性能和发布边界

| 被测对象 | 具体测试内容 | 结果与边界 |
|---|---|---|
| ZAP launcher | digest/no-pull、requestor/passive-wait/report、origin、risk threshold、报告完整性、资源 ownership、cleanup 注入。 | K426 **65 passed/3 strict xfailed**；061 仍作为 Web 产品红灯。 |
| ZAP owned target | 隔离 network、合成 app、固定 `/health` 与 `/api/state` GET、passive report、scanner exit、digest、容器/网络清理。 | K427 Docker host **1 passed/58.77s**，scanner exit=0，报告 SHA `2f112955…a670c`；仅 passive，不是 active scan。 |
| ZAP 统一 runner | 零告警 pass、超 max_risk product fail、exit=0 无报告 harness_failure、failure-bundle 和 reference closure。 | K428 **4 passed/25 deselected**；K429 security/runner/manifest **156 passed**，closure/report/bundle **19 passed**。 |
| OpenAPI/Schemathesis | service lifecycle、OpenAPI operation discovery、status/schema、not-server-error、JSON/JUnit/version receipt、accepted invalid input。 | generic API fixture 正向通过；配置/版本漂移单独分类，不冒充产品 finding。 |
| k6/performance | digest-pinned image、health/state workload、pressure/spike/stress/soak、checks、error rate、request count、p95/p99、threshold、summary。 | 单次/有界负载可执行；PERF-05/K552-F2 长时分配窗口没有被一次 smoke 关闭。 |
| clean install | wheel/sdist 构建、target import、base/dev/web/stream-json/feishu extras、隔离 HOME/XDG/PYTHONPATH、CLI help/validate/doctor。 | K6/K8/K10/K14 发现 046；当前不能把 `--no-deps` target import 写成完整 extras clean-venv 通过。 |

## 7. 证据阅读规则与当前状态校正

1. **报告数量不是功能数量。** 同一调用方闭包、S0 收尾和主干 MAINLINE 可能共享节点，不能把 589 MAINLINE、S8、S9、S6 数字相加。
2. **strict xfail 是可执行的失败证据。** 例如 K434 的 13 个 recovery xfail 和 K432 的 11 个 S8 xfail 都有 Issue 映射；它们不是“测试没跑”。
3. **修复状态以 `ZaoFu_issue.md` 当前台账为准。** 020、040、048、063、065–080、086、092–099、102–103、105–112 等已标 fixed；001–003、006–007、009–013、018、022、026、032–039、042–044、046、058–062、064、104 等仍有 open 或待回归项，不能沿用旧表中“全部开放”或“全部完成”的历史描述。
4. **K597 不是产品通过率。** 当前统一审计为 **11 pass / 2 fail / 6 missing / 15 harness_failure**，并且 `same_revision_verified=false`、`gate_exit=1`；它只说明证据源、阶段、SHA 和缺项被如实分类。
5. **K583 的 589 passed 是历史 MAINLINE 报告；后续 K598 已完成 44 个注册入口、678 个节点通过。** K599 的单项来源审计通过，不会覆盖 K597 的 34 项统一审计缺口；K612 的 S1 556 项和 K613 的聚焦 51 项也不能与 MAINLINE 相加推算功能完成率，不能视为当前所有源码的全量验证。

## 8. 细化后的结论

经过本次补充，`zaofu_done_part.md` 已不再只按 K 阶段罗列，而是把已完成部分拆到以下可核对粒度：

- 每个测试域的被测对象、入口文件/命令、关键断言和实际报告数字；
- EventLog/Store/Web、workflow/candidate/concurrency、Provider/tmux/VM、ZAP/k6/install 等不同边界；
- 产品问题、测试框架问题、环境阻断和证据缺失的不同归因；
- fixed 问题的原始红灯、修复回归和“只关闭局部故障窗口”的限制。

因此，本文件可以作为后续继续补测的索引：回归时应优先复用表中的精确入口和原始证据路径，不用重复已完成的绿色链，也不能只引用一个总通过数来替代具体测试范围。

## 9. 最新补充进展（K598～K613，2026-09-29～09-30）

本节沿用前文“技术/入口—测试内容—结果/边界”的口径，仅补录已完成部分，不改变历史报告。来源为 [steps.md](steps.md) 对应 done 章节和 [coverage_matrix.json](../04_audit_and_issue_ledger/coverage_matrix.json) 的 `latest_k598`～`latest_k613` 条目。以下数量包含重叠合同和不同批次，不累计；方法就绪、合同通过、实际故障验收分别记录。

| 阶段/已完成部分 | 使用技术和入口 | 具体补充的测试内容 | 已有结果与边界 |
|---|---|---|---|
| K598 完整确定性主干 | `python -m tests.testkit --profile mainline`；`tests/testkit/suites.py` | 按当时 44 个注册入口展开精确 nodeid；校验 session、phase JSON/JUnit、退出码、source-before/after 和文件摘要，未缩减到旧报告选择集 | **678 passed / 513.275464s**；无 collection error、deselection、timeout 或 evidence error。只证明该批确定性注册面，不替代真实 Provider/最终验收 |
| K599 MAINLINE 正式来源审计 | `requirement-audit-k599-20260929.json`；requirement-audit CLI | current selection 从注册表核对报告，绑定 phase、JUnit、source-before/after；保留 K597 旧清单，不覆盖历史缺项 | **1 pass、0 fail/missing/harness_failure**；`same_revision_verified=true`、`gate_exit=0`、`release_authorized=false`。这是 MAINLINE 单项审计，不是完整 34 项统一验收 |
| K600/K602/K610 原生方法与跳过标记 | Codex：`tests/conformance/test_zaofu_testkit_s5_native_codex_legacy_queue.py`；Claude：`tests/testkit/native_run.py` | 保留 timeout→resume、thread/turn/transcript、normal/impl/verify、同源报告与 cleanup 合同；启用开关分别为 `ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE=1`、`ZF_RUN_NATIVE_CLAUDE_PIPELINE=1` | 两项均 **SKIPPED_BY_OPERATOR_ENVIRONMENT**，原因是操作者要求；方法已搭建，原生未运行、不计通过。历史环境观察不作为本次登录/安装诊断 |
| K601/K603 长样本观测与重新进入条件 | `tests/testkit/allocation_segments.py`、`test_allocation_segments.py`、`test_memray_guard.py`；S4 workloop host 检查点 | 单一连续 Memray Tracker/单 capture；按任务区间核对完成水位、PID、Tracker 起始时间、窗口顺序/重叠、预算；重新进入须有完整窗口、来源 ref/SHA 和预算兼容证据 | K601 分段/capture guard **29 passed、1 deselected**；K603 与 guard 联调 **35 passed**。未运行 32Task/1200 秒长样本，不能记为 PERF-05 验收 |
| K604 统一审计负向边界 | `tests/testkit/test_requirement_audit.py` | 缺报告保持 incomplete；digest/JUnit/phase/source 篡改归 harness_failure；historical/current selection 分离；同 HEAD 不同 source scope 不冒充同源 | **42 passed / 128.77s**，后置 S0 **19 passed**；只验证审计器，不补造历史原件、不关闭既有缺证 |
| K605 同签名新现场准入 | `tests/testkit/incident_reentry.py`、`test_incident_reentry.py` | K530 首次退出的 worker/PID/启动时间/snapshot；K584-R 的 handshake failure class、command、nonce/capture；精确签名、新时间戳、诊断和 source ref/SHA 必须齐全 | **10 passed**，后置 S0 **19 passed**；拒绝正常控制链、历史报告和缺诊断证据。K530/K584-R 根因仍开放，不等于修复 |
| K606 OS 故障模型分类 | `tests/testkit/os_fault_models.py`、`test_os_fault_models.py` | exact fault_model 与证据字段校验；已有 owned-QEMU-SIGKILL、guest-kernel-panic 只归属本模型；未知/错模型/坏摘要/缺证拒绝 | **8 passed**，后置 S0 **19 passed**；不从 SIGKILL/panic 推断物理掉电或文件系统损坏覆盖 |
| K607 最终同源验收准备门禁 | `tests/testkit/unified_acceptance.py`、`test_unified_acceptance.py` | audit pass、gate_exit=0、单一 source variant、same revision、非空 requirements 且逐项 pass/source_verified；拒绝缺证、失败、混合来源 | **7 passed**，后置 S0 **19 passed**；仅可返回 ready_for_operator_review，始终不授权发布；真实统一验收仍未完成 |
| K608 S1 基础设施联调 | `tests/testkit/suites.py::S1`、TestKit JSON/JUnit | 分段观测、新现场准入、OS 模型、统一 readiness 与原有基础设施共同执行；核对 session、selection、证据与退出码 | **519 passed**，外层 **683.286s**；非原生 Provider 轮。此后 K609/K611/K612 扩展后的完整 S1 结果见下 |
| K609 待办状态守卫 | `tests/testkit/need_to_do_inventory.py`、`test_need_to_do_inventory.py` | 16 个条目区分 operator skip、等现场、方法已就绪、破坏模型开放、历史缺证、最终验收阻断、不稳定/降级和 framework-only 完成；拒绝重复 ID、未知状态、缺方法/条件/evidence | 与 OS/unified 合同合计 **26 passed**，S0 **19 passed**；`product_pass_count=0`，框架完成不推导产品完成率 |
| K611 文件系统损坏安全方法 | `tests/testkit/guest_filesystem_corruption.py`、`test_guest_filesystem_corruption.py` | disposable qcow2 overlay、只读 base、网络关闭、无宿主可写共享；精确注入身份、同盘恢复、boot identity 变化、目标内容变化、SHA/cleanup/source proof | **13 个合同节点通过**；不启动 QEMU、不执行损坏注入，真实模型仍为 destructive_model_open |
| K612 物理宿主掉电安全方法 | `tests/testkit/physical_host_power_loss.py`、`test_physical_host_power_loss.py` | 专用 host identity、人工批准引用、IPMI/PDU/Redfish 控制器；只读 base/私有 overlay；电源事件/时间戳、恢复 boot、持久前缀与摘要；拒绝 SIGKILL/panic/reset/shutdown 替代 | **13 个合同节点通过**；两类 OS 与既有合同合计 **52 passed**，隔离 S0 影响闭包 **71 passed**。不调用电源控制器，不执行断电 |
| K612 扩展后的完整 S1 | TestKit runner、pytest JUnit、显式隔离 workspace | 核对新增 guest/physical 各 13 节点实际进入 S1、source/session 完整性与注册一致性 | **556 tests，0 errors/failures/skips，381.641s**；exit=0、session_finished=true。包含合同节点，不是 556 个真实业务或故障场景 |
| K613 开放项只读就绪检查 | `tests/testkit/open_item_readiness.py`、`test_open_item_readiness.py` | 汇总长样本重新进入证据、同签名现场/破坏性宿主能力的调用方声明；缺证 not_ready，有条件 ready_for_operator_run；所有项 execution_authorized=false，最终验收仍 blocked | 直接 **11 passed / 0.27s**；S0＋当前模块＋allocation 合同 **51 passed / 63.08s**（含该 11 项）。只读汇总，不是实机探测；未重跑完整 S1，收尾后暂停构建 |

### 9.1 补充阶段的报告索引与框架问题

| 阶段 | 原始报告/记录位置 | 说明 |
|---|---|---|
| K598 | `/tmp/zf-mainline-current-20260929/testkit-run.json` | 主干 678 项；具体 SHA、source scope 见 coverage 的 latest_k598 |
| K599 | `/tmp/zf-k599-audit-current-20260929-132040/requirement-audit.json` | 单项 MAINLINE 审计通过；未改写 K597 的 11 pass / 2 fail / 6 missing / 15 harness_failure |
| K608/K612 | `/tmp/zf-s1-k608-foundation-20260929/pytest-testkit.json`；`/tmp/zf-s1-k612-power-method-20260929-current/pytest-junit.xml` | 分别是 519 与 556 项的不同源码批次，不合并计数 |
| K613 | `/tmp/zf-k613-readiness-s0-20260930-YJTU7L/pytest-junit.xml` | 聚焦 51 项结果；不是完整 S1/MAINLINE 的新报告 |
| 注册遗漏 | steps 的 K605/K609/K612 | S0 曾检出 coverage 漏登记；已补齐并复验。属于 TestKit 登记错误，不新增 ZaoFu 产品 Issue |
| workspace 隔离 | steps 的 K612 | 非隔离收尾曾报真实 registry 变化；保留 harness error，随后显式临时 ZF_WORKSPACE_HOME 下 71 项通过，不把环境风险归因产品 |
| 参数化收集 | steps 的 K613 | `function uses no argument 'kwargs'` 已通过修正测试参数修复；11 项直接测试与 51 项影响闭包通过，不新增 ZaoFu 产品 Issue |

这些补充阶段没有新增或关闭 ZaoFu 生产 Issue，既有问题继续以 [ZaoFu_issue.md](../04_audit_and_issue_ledger/ZaoFu_issue.md) 为准。`/tmp` 是当时报告位置，不保证永久留存；缺失时必须标记缺证，不得用文档数字或新报告冒充原始证据。

当前状态：**K613 当前测试已完成，测试方案构建已暂停**。K547/Claude 保留“方法已搭建、操作者跳过”；K530/K584-R 等待同签名新现场；K552-F2 长样本、两类破坏性 OS 实际验收、历史原件和最终统一同源验收仍开放。Feishu、`/issue-merge` 及明确降级项仍不实施；本次文档补充不解除暂停。

# ZaoFu 测试框架待完成部分梳理

本章只梳理“要形成 ZaoFu 端到端可信系统测试闭环，还需要具备或执行的测试”。它不是完成百分比，也不是本轮新增实现清单；本轮不启动 Provider、故障注入、长样本或发布授权。`method_ready` 表示方法/合同已经存在，`execution_open` 表示仍需在满足条件后实际执行，`blocked` 表示前置证据或其它开放项未闭合。

## 1. 框架完成与产品验收的不同判据

**测试框架闭环完成**要求：需求有对应可执行入口、独立断言、结果分类、可回读证据、清理和报告接线。实际检出 ZaoFu 产品失败并准确关联 Issue，是有效的测试结果，不要求先把产品 bug 全部修好。仅有方法描述或证据字段校验、不具备实际执行和取证接线的场景，不能称为该场景执行能力已经完成。

**产品最终验收通过**另受现有 `unified_acceptance.py` 的全项通过门禁约束。以下是来源与验收要求，不应倒推成“测试框架必须测到全绿才算搭建完成”：

1. 当前源码和测试选择集固定在同一 immutable revision；完整 MAINLINE、S0 注册审计、阶段报告、JUnit、source-before/source-after 和文件摘要相互一致。
2. 框架报告必须如实区分 `pass`、可复现产品 `fail`、有依据的不适用，以及 `missing`、`harness_failure`、`operator_skip`；后几类说明未形成相应业务结论，不等于产品失败，更不能代替通过。当前最终门禁要求每个纳入项为 pass/source_verified，并不会自动接受不适用或跳过项；范围变化需单独确认，不能静默删项。
3. 原生 Provider、故障模型、长样本和同签名事故项均使用各自精确模型和证据合同；不能用 fake provider、QEMU SIGKILL、guest panic、短样本或正常控制链跨模型推断。
4. 每个结果都能回读命令、nodeid、阶段、退出码、报告、日志、artifact/sidecar SHA-256、清理和 source revision；首次红灯、修复回归和重跑结果不可混写。
5. 开放产品 Issue 要么保留为当前可复现的产品 `fail` 并阻断发布，要么在产品修复后用同一检测节点获得回归证据；测试框架不能通过删除节点、改阈值或把 xfail 改 skip 来“完成”。
6. 最终统一审计的 `same_revision_verified=true`、`source_variant_count=1`、`gate_exit=0`，且每一项 requirement 为 `pass + source_verified`；即使 readiness 为 ready，也仍不自动授予 merge/push/release 权限。

## 2. 真正需要补齐的构建接线

以下是依据当前非执行型合同确认的接线需求，不是重新建设 S0/S1/MAINLINE；具体宿主工具与故障实现仍须在隔离资源明确后核对，未发现已有实现时才补代码。

| 构建缺口 | 已有基础 | 仍需补齐什么 | 框架完成判据 |
|---|---|---|---|
| 文件系统损坏的真实 host 执行链 | K611 计划/证据校验函数明确不启动 QEMU、不执行损坏 | 在获准的 disposable overlay 上接入创建、精确注入、同盘恢复、状态比较、失败取证与 finally 清理；将 host 节点接入阶段注册和统一报告 | 由实际执行生成证据，而非调用方填入“已注入”；正向/故障负向/清理失败均有可解释报告，业务失败也能留证 |
| 物理掉电的带外执行/取证链 | K612 仅校验专用宿主、批准引用和电源回执形状，不调用控制器 | 根据专用宿主选择受控控制器适配，接入身份核验、掉电/上电回执获取、重连、boot/持久性比较、超时和资源收尾；测试记录须跨宿主断电保留 | 精确电源事件与同次恢复可关联；控制器不可用/恢复失败不会假报通过；不把杀 QEMU 当掉电 |
| 新 host 场景的统一证据接线 | 已有 runner、S0、requirement-audit 和 source 校验 | 在上两项执行链具备后，为其绑定精确 nodeid、fault_model、报告/sidecar SHA、phase/JUnit、source scope、cleanup 和审计条目 | 直接执行结果可由统一入口回读；模型错配/缺证/摘要变化有明确拒绝；不重复编写已完成的通用审计器 |

K552-F2 已接入 workloop 检查点，首先缺合格的连续无损观测条件与长样本结果，不凭当前材料认定必须再造一个性能执行器。K530/K584-R 也先等真实现场；仅当现场暴露现有采集缺口时，再补最小检测或取证代码。K613 中调用方提供的能力布尔值不是宿主探测结果，不能据此跳过 host 执行接线验收。

## 3. 方法已有、仍待执行或取证的事项

| 待完成项 | 需要具备的测试/技术 | 完成时必须留存的证据 | 当前状态与阻断 |
|---|---|---|---|
| K547 Codex 原生 legacy queue | 使用既有 `test_native_codex_legacy_queue_timeout_resumes_only_gap`，在指定生产 `CODEX_HOME` 下执行真实 initialize/thread/turn、自然 timeout、只恢复缺口、candidate/Task 结算和 cleanup；不使用离线替身 | Provider transcript、thread/turn、timeout 与 resume 关系、进程树、candidate/Task 终态、source-before/after、完整报告 | `method_ready`；按操作者要求 `SKIPPED_BY_OPERATOR_ENVIRONMENT`，未计通过 |
| Claude 原生 pipeline | 使用 `run_native_pipeline` 的 normal/impl/verify 节点执行 start→dispatch→submit→verify→cleanup；校验 CLI/model/auth、session、transcript、cwd、超时、重启和 candidate | 原生 CLI 版本、认证/模型 receipt、每阶段 transcript、退出码、session identity、candidate/cleanup 和同源报告 | `method_ready`；按操作者要求跳过，未计通过 |
| K530 watchdog 根因 | 等待 `worker.first_exit.no_liveness_evidence` 精确同签名新现场；测试首次退出、PID/启动时间、未被 replacement 覆盖的 snapshot、watchdog 判断、retry/respawn 和最终归属 | 首次 failure event、worker/PID、启动时间、原始 capture、retry briefing、Task/Judge/candidate 终态、source ref/SHA-256/时间戳 | `execution_open`；不能用正常 retry 或历史报告关闭 |
| K584-R shell handshake | 等待 `provider_restart.shell_handshake_timeout` 精确同签名新现场；执行 nonce、shell capture、command、restart、超时诊断和后续恢复链 | failure class、current command、nonce/capture、诊断、restart attempt、退出码、清理、同一 revision 报告 | `execution_open`；不得用一次未复现的正常重跑宣称 fixed |
| K552-F2/PERF-05 长样本 | 在重新进入证据通过后，执行单 Tracker/单 ALL_ALLOCATIONS capture、32 Task、1200 秒；持续记录 segment 水位、RSS/FD/PID、worker 子进程、capture 完整性和 cleanup | 连续窗口、Tracker PID/起始时间、唯一 capture、完整任务范围、预算、资源曲线、回读 SHA、最终泄漏/释放断言 | `execution_open`；当前只有分段与准入合同，未执行长样本 |
| physical-host-power-loss | 完成上节受控执行接线后，在人工批准的专用宿主上执行真实掉电、同 overlay 恢复和 durable prefix 校验；拒绝 SIGKILL/panic/reset 替代 | host identity、批准引用、电源事件 ID/时间戳、boot identity 变化、base/overlay/source SHA、恢复前缀、cleanup/source verification | 方法合同完成；执行接线与实际验收未闭合，不能仅算等一次运行 |
| guest-filesystem-corruption | 完成上节 host 执行接线后，在私有 disposable qcow2 overlay 中执行精确 guest 文件系统损坏，保持只读 base、guest 网络关闭、无宿主共享可写路径；同盘恢复并核对目标内容变化 | fault nonce、注入前后摘要、base/overlay/durable-prefix/source SHA、前后 boot identity、同 overlay 复用、cleanup | 方法合同完成；执行接线与实际验收未闭合，不能仅算等一次运行 |
| 历史 evidence 补证 | 按原 requirement、原 nodeid、原报告路径和原 SHA 回读缺失的 phase/JUnit/source/sidecar；校验历史选择与 current MAINLINE 隔离 | 原始字节、SHA-256、report ref、缺证原因、source revision；不能用新报告重建历史结论 | `blocked`；K597 仍有 missing/harness_failure，未闭合前不能进入最终验收 |
| 当前 MAINLINE 后续同源复核 | 在上述开放项有结果后，以当前注册表重新运行完整 MAINLINE，并回到 S0、requirement-audit、source observation 和统一审计；不复用旧 589/678 报告 | 新 testkit-run、phase JSON、JUnit、source-before/after、文件摘要、审计 manifest 和一致 SHA | `execution_open`；K598/K599 只代表当时确定性批次，不替代未来开放项加入后的复核 |
| 最终统一同源验收 | 将所有显式需求、产品 Issue、Provider/OS/性能/历史证据纳入同一 revision 的 requirement audit；逐项 `pass + source_verified` 后才进入人工 review | `same_revision_verified=true`、单一 source variant、gate exit 0、无 missing/harness_failure、统一 evidence index、人工 review receipt | `blocked`；K607 只有只读准备门禁，不授权发布 |

## 4. 后续测试的必要联调层

完成单个开放项后，不能只保存该项的局部报告，还需要依次做：

1. **直接合同回归**：验证测试本身能识别正例、缺证、错误模型和环境阻断。
2. **调用方影响闭包**：只验证本次变化涉及的模块、直接调用方和跨越的 EventLog/Store/schema/API 契约；并非每补一个测试都重跑所有模块。只有跨边界变化才扩展对应联调域。
3. **注册与覆盖审计**：更新 S1/S4/S5/S6/S10 或 MAINLINE 注册面、coverage matrix、Issue 引用和精确 nodeid；再执行 S0 parity。
4. **来源与清理验证**：检查 source-before/after、报告/JUnit 一致性、artifact SHA、临时 workspace、tmux/进程/容器/QEMU/电源控制器清理。
5. **统一报告汇总**：将 `pass`、产品 `fail`、`harness_failure`、环境失败、`not_run`、`operator_skip` 和历史缺证分开写入，不以总通过数替代逐项结论。

## 5. 明确排除或暂缓的项目

- `/issue-merge`：功能契约尚未冻结，本文件只保留未来测试位置，不设计、不实现。
- Feishu 联调：投影、入站意图、权限和回滚契约未冻结，本文件不设计、不实现。
- 明确标记为降级的工作包：非降级主线和对应恢复条件全部完成前不排期。
- 远端 PR、merge、push：不由 TestKit 执行；仓库规则禁止远端推送。`merge_authorized=false` 和 `release_authorized=false` 必须保持独立。

## 6. 当前结论

当前仍需补构建的明确部分主要是两类破坏性 OS 模型的真实执行、取证、恢复与统一审计接线；其它列明事项主要是方法已具备后的条件等待、实际执行、历史补证或产品验收，并非全部都是测试代码缺失。已有产品红灯不从框架完成能力中倒扣，也不因框架能发现问题而宣布产品验收通过。本章不是原始需求全量重新审计，不能据此声称除此之外不存在任何覆盖缺口。

K613 收尾后构建仍暂停；本轮仅整理待完成部分。后续需由操作者明确恢复，再按范围与前置条件选取事项；Codex/Claude 的跳过、Feishu/`/issue-merge` 的排除及降级约束不会因写入本章自动解除。
