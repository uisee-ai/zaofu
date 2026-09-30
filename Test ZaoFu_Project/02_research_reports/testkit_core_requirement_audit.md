# ZaoFu-TestKit 原方案完成度核查：核心、恢复与证据

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/suites.py`。原 2026-09-14 有限缺口审计不能覆盖后续 K598～K613；当前开放项查 need_to_do_inventory.py。

> 日期：2026-09-14。目标仍为 active。本报告是测试实现审计，不是产品通过报告，也不授权修改生产代码。

## K4 异步普通用例取证（当前状态优先）

本地 pytest 9.0.3 / pytest-asyncio 1.3.0 下，新增异步普通取证探针覆盖 pass、call/teardown failure、setup failure 与 `CancelledError`。async fixture 生命周期、checkpoint、phase 原始事实和正常清理均由真实 nested runner 验证；5 例独立通过。完整 S1 `/tmp/zf-async-s1-20260914T054359Z-0385at18/testkit-run.json` 为 **175 passed**、155.192853 秒，verdict=pass，无取证或基础设施错误。此处仅关闭当前版本 function-scope async 合同，不关闭 xdist/rerun/timeout/强杀/module scope/custom protocol。

## K4 并行/重试兼容性审计（当前状态优先）

调研 [ordinary_parallel_retry_research.md](ordinary_parallel_retry_research.md) 已核对 pytest 9.0.3、pytest-xdist 3.8.0 及未安装的 pytest-rerunfailures。当前 plugin 以单一 report 路径和 nodeid 为键，没有 worker/testrun/attempt 隔离或父进程合并，因此不能宣称 xdist/rerun 支持；这不是已完成测试，保留为下一实现缺口。目标合同是 worker 独占目录、显式身份合并、冲突/未完成 fail-closed，以及首轮失败后重试通过仍非普通 pass。

已新增 `tests/testkit/parallel.py` 的合并原语及合同测试，并接入 runner 的显式 `xdist_workers`、worker 独占 report 路径和父进程合并。真实 S0 xdist 子进程合同与完整 S1 联调通过：`test_parallel.py` 5 passed，S1 180 passed；缺失/冲突身份、未完成报告和 collection `not_run` 占位均有 fail-closed 处理。普通快照也已在真实 S3 七用例多 worker 合同中迁移、核摘要并通过；普通快照与 rerun 的组合、超时/强杀和自定义 protocol 仍未关闭。

同一阶段的 `dev-verify` xdist 闭包首轮因 nested runner 泄漏外层 worker 环境而出现 65 个 harness 失败；清除 `PYTEST_XDIST_WORKER/TESTRUNUID/WORKER_COUNT` 后重跑为 **124 passed**（113.25 秒）。这属于 TestKit runner 缺陷，已修复并保留首轮日志；不登记为 ZaoFu 生产 Issue。普通快照与 rerun 轮次的组合仍是未完成合同。

普通快照 + rerun + xdist 三者组合已用真实隔离进程验证：2 worker、1 次 rerun，父侧迁移 worker snapshot 后合并报告，attempt 0/1、普通 snapshot 和 `flaky_pass` 均保留；相关合同通过。timeout/强杀和自定义 protocol 仍未完成。

rerun 语义已增加 `_final_case_status` 合同：保存每轮 phase，并将“首轮失败、后续通过”标成 `flaky_pass`，runner 的 bad 状态集合拒绝普通绿灯；真实隔离插件合同已验证两轮 attempt。普通快照 + rerun 组合也已通过 2 个合同，保留 snapshots 且维持 `flaky_pass`；xdist + rerun 三者组合、timeout/强杀等仍未完成。

该状态已用隔离的真实 pytest-rerunfailures 进程验证：`test_rerun_evidence.py` 通过 `uv run --with pytest-rerunfailures` 执行一次失败/一次通过，报告保留两轮 attempt，最终 `flaky_pass`，外层测试与并行合同共 7 passed。插件没有加入仓库 dev extra；普通安装环境仍不会默认执行 rerun 专项。

## K4 S3/S4 普通运行时取证接入（当前状态优先）

现有 S3 七例与 S4 四例显式接入普通取证；不改原断言、共享 fixture 或生产状态规则。两个真实 dispatch 崩溃检测器在私有父目录留存 baseline/fault 两树及子进程回执；动作前未知的 Run/Attempt/Operation 身份明确 null+原因，不把 scenario correlation 冒充准入身份。新增 S1 接入审计实际执行这 11 例，核对登记、清理、既有红灯和证据副本。

最终 `/tmp/zf-runtime-evidence-adoption-integration-20260914T053154Z-c858jlna`：35 入口 **430 passed/15 既有 strict xfailed**，S1 **170 passed**，316.380444 秒；完整 session、外层 fail，无新增普通失败、取证/基础设施错误。35 个 bundle 文件、20 模型/11 普通快照、2028 嵌入文件及普通源摘要已核验。当前普通生产节点共 **14 个**，并非全部域；异步/并行/重试插件仍需独立验证。完整 TDD 回执及剩余范围见 steps.md 最新 K4。

## K4 清理/提交窗口补测（历史子阶段）

普通快照所有权声明现先于第一次写入落报告；新增父 runner 未绑定诊断回收，不覆盖原 ref、pytest phase 或状态。固定槽无绑定文件只用于诊断，触发 harness_failure；`.tmp`、未知槽、未注册源和非法路径不提升为快照。真实子进程证明首注册/新槽/覆盖槽写入后退出、真实原子替换前退出、截断 tmp、注册前退出。多 finalizer/清理中改写 guard/无 guard 删除/pytest.exit(0/17) 与普通 skip 原因也有明确探针。

四个普通证据模块 66 passed；最终 `/tmp/zf-evidence-windows-integration-20260914T051329Z-w2mx5rtp`：28 入口 **416 passed/13 既有 strict xfailed**，S1 **169 passed**，309.145778 秒，外层 fail，完整 session、无新增普通失败或取证错误。最终 bundle 27 文件、20 模型/3 普通快照、1684 嵌入文件已独立核验。独立覆盖 checkpoint 后退出的诊断检查另验证旧摘要不改、1 份诊断入包、合成源清理后副本仍可读。详见 steps.md 的 K4。

这关闭所列固定清理/提交窗口的实现缺口，不关闭全部 K4。普通生产适配仍为三个明确节点；并行/async/rerun/自定义协议未验证，不能声称任何退出时刻现场完整。K2 其余路线、K3/K5/K6 仍保留。以下首批的“相关窗口待补”是历史时点，不覆盖本节。

## K4 普通状态取证首批（历史子阶段）

新增 `tests/testkit/case_evidence.py`，已接入 pytest 的显式 fixture/报告/清理前 hooks 与真实 runner；不是无调用者的 helper。明确所有权、合成无秘密声明、身份及 Given/When/Then/Forbidden，保留原始二进制字节与源文件/快照摘要；有界文件/条目/深度/字节/次数，拒绝 symlink/特殊文件/共享 hardlink。父 runner 核对 run/case 固定引用、节点和内外层摘要，取证错误单列且非绿；不能代替业务 oracle 或对抗任意恶意测试代码。

44 项新增契约包含 setup/call/teardown、pytest.fail、owner guard、xfail/XPASS/skip、双阶段失败、ready 后 timeout、exit/SIGTERM/SIGKILL、篡改和缺失报告。已适配 S2 的 EventWriter 因果链、TaskStore 更新和一个既有签名篡改检测。两普通成功例仅在所有阶段通过后清理自有快照；失败例保存原字节。不是全部普通用例自动取证。

最终 `/tmp/zf-ordinary-evidence-final-v8c7r2sv`：26 入口 **394 passed/13 既有 strict xfailed**，S1 147 passed；183.418583 秒，外层 fail，完整 session，无普通失败/取证/基础设施错误。27 个包内文件、20 份模型快照与3份普通快照、1684 个嵌入文件已独立核验摘要。完整红绿回执、适配节点与范围见 [steps.md](../03_plans_and_steps/steps.md)。

K4 其余普通 case 的适配、清理中途/多 finalizer/pytest.exit/注册前强退与快照-报告提交窗口、并行/async 插件兼容仍待补。K2 其余路线、K3/K5/K6 均未关闭。以下前置阶段“raw capture 未实现”的描述保留其历史时点，不覆盖本节。

## K4 阶段报告持久化（历史前置阶段）

新增五个真实 setup/call/teardown 硬退出及 SIGTERM/SIGKILL 探针：原始五项均检出已完成 phase 丢失；TestKit plugin 改为 collection 显式 not_run、每次 logreport 原子保存已观察事实，复用原分类优先级。中断仍 session_finished=false/harness_failure，不能由前序通过推断后续成功。原 child-ready 超时用例也新增实际 setup 保留、call/teardown 缺失断言。

首次联调因矩阵漏登及模型测试全局 os.replace 注入而中断，保留报告；注入范围收窄到模型 output 且明确验证注入仍发生，不修改生产代码或掩盖 reporter 错误。最终 `/tmp/zf-phase-facts-final-integration-7lm5emhm`：23 入口 **333 passed/10 既有 strict xfailed**，207.195986 秒，S1 103 passed；无普通失败或基础设施错误。完整范围、失败包摘要和前后证据见 steps.md K4。

原方案 §3/OBS-07/08/§20.2 的普通状态注册、清理前原始字节、checkpoint 与 snapshot 入包仍待实现；本次只是其报告基础。方法依据 [ordinary_case_evidence_research.md](ordinary_case_evidence_research.md)，不能把该前置阶段标成整个 K4 或目标完成。

## K2 独立 External Issue triage（当前状态优先）

新增并注册 `test_zaofu_testkit_s8_triage_route.py`：真实 ingress→reader/父聚合→独立 verifier→artifact.delivery.verified→triage outcome→Run 名额释放及 Goal gate。使用真实 blocking semantic submit、已签发 scratch 与 role capability，凭据随机来源仅在签发期间替换为公开临时测试值，不能据此宣称真实 Provider/凭据熵验证。十五例覆盖正常及四个重建切点、pending/终局重复处理、错误凭据/重复提交、缺输出/必读/证据、产物摘要/证据归属，以及 signed identity 不被 worker 字段覆盖和负向 verifier 无假成功。

`/tmp/zf-triage-route-integration-z4_a3ru6`：22 入口 **328 passed/10 strict xfailed**，156.311452 秒；新 triage 15 passed，既有 036/037/044/045 留红，无普通失败或基础设施错误，总 verdict=fail。包含全部 S0/S1、之前 Issue/Research 与直接契约/两个 Mock E2E，不是全部 S8/ALL。24 个 bundle 文件、20 份快照及 1678 个嵌入文件摘要核验。初期失败属已按源码纠正的夹具假设，不登记新生产 Issue，详见 [triage_route_execution_research.md](triage_route_execution_research.md)。

独立 triage 不再是完全未实现，但有限 unverified_candidate 矩阵不能外推到 ready_to_fix 契约提升、needs_info 往返、完整 replan/cap 或硬进程恢复；PRD standard、K2 其余入口/身份行为及 K3–K6 保留。历史段落的“triage 尚未实现”以本节为准。

## K2 Issue 默认路线补测（当前状态优先）

已新增并注册 `tests/conformance/test_zaofu_testkit_s8_issue_route.py`，由 `tests/testkit/issue_route.py` 装配正式 light route：真实 request/proposal/submit、TaskContract/package、writer worktree/commit/必读输入、candidate 质量命令、verify 与 Thin Judge、Kernel Goal completed 和 Task done。十一例中八个控制通过，三个旧 candidate 重放复现 ZF-TEST-045；检测器只对精确身份漂移异常 strict xfail，其余错误保持普通失败。新 I-02 在 worker 绿色自测来自错误脚本的条件下，实际候选重新执行原回归 exit=1，拒绝进入 downstream 成功，不只检查自测报告结构。

最终 `/tmp/zf-issue-route-stable-integration-2lvybfvc`：24 入口、266 passed/10 strict xfailed，276.498059 秒；覆盖全部 S0/S1、Issue、两 Research 矩阵和明确直接契约/两个 Mock E2E，不是全 S8/ALL。24 个失败包文件、20 份 first/latest 内 1678 个嵌入文件及源/helper/trace/config/lock 摘要核验；无普通失败、收集错误或超时，总 verdict=fail。先前联调暴露 S1 的 pytest 启动／child 信号就绪竞态；已依据调研 §8 修测试准备同步，0/2.5 秒准备条件均执行真实超时和进程组清理，旧失败报告仍保留。

不把较早重建和固定状态快照外推为硬进程崩溃、普通失败自动取证或完整 Git 备份；内部受控动作入口不证明 Web/token 授权；默认签发 legacy 结果不证明强制 semantic submit。完整 retry/cap、PRD standard、独立 triage reader→verify、parent/startup 行为和 K3–K6 尚待继续。旧 Issue smoke 只到派发的缺口已补到本矩阵，不能再说该路线完全没有正式终局测试，但整体目标仍 active。

## K2 Research 失败重规划（前一子阶段）

已新增并注册 `test_zaofu_testkit_s8_research_replan.py` 的 8 个 case。正式 fixed/adaptive 失败后，无论原对象还是同目录重建，Kernel 改写 reason 后生成的 invoke 均被自身 generation gate 拒绝，登记 ZF-TEST-044。三个 idle tick 后仍无重派发或明确升级，Task=in_progress。内存只读诊断恢复旧 reason 可使 binding_error 为空，但没有执行该副本，也不据此修生产代码。人为篡改拒绝、同失败重复唤醒/重建只生成一个 replan 的四个控制通过。

正式联调 `/tmp/zf-research-replan-integration-9y_056bh`：19 入口、129 passed/7 strict xfailed（044 四个、036 一个、037 两个），62.134351 秒，verdict=fail；18 个 bundle 文件、14 份快照内 632 个嵌入文件摘要核验，无普通失败/收集错误/超时。选定通用 cap 节点通过，不证明 Research 已跑到 cap；当前检测器在首次 replan 即检出缺陷，其后半断言保留待维护者修复后的实跑。完整边界与后续 delivery route 工作见 [steps.md](../03_plans_and_steps/steps.md)，不把此进展升级为整个 K2/目标完成。

## K2 Research 补测（前一子阶段）

新增 `tests/conformance/test_zaofu_testkit_s8_research.py` 并注册 S8：正式受控启动铸造 generation/prompt/config/run-contract，经真实 Kernel 派发、固定 worker 事实、实物产物/ref/digest/result 到 standalone Task done。27 个有限 case 涵盖 fixed/adaptive 成功、失败、缺报告、超时、重复、逆序和同目录对象重建；保留 adaptive 的正式配置但阻断实际 Provider/网络调用。

原始 `/tmp/zf-research-admission-reproduction-f3l4iu7p` 为 24 passed/3 failed，扩展既有 Issue036/037：正式路径中错误 child run 被接受、提前 synth 可在 reader 未完成时交付并完成 Task。三个 latest 快照含 137 个文件，已校验字节和摘要；其余控制通过。详细步骤及后续联调见 [steps.md](../03_plans_and_steps/steps.md)。旧 template 的准入缺口已由新增模块补到所列场景，不能继续说完全未证明；完整失败 replan/cap 链、其他官方 route、K3–K6 仍未完成。

最终联调 `/tmp/zf-research-k2-final-integration-rdcl5jog`：S0/S1/S8 全注册与直接契约共 35 入口，491 passed/4 strict xfailed，292.175287 秒；新模块 24 passed/3 xfailed，另一个为原 Candidate Issue006。此前联调的六个 provider summary 夹具错误已补合法合成 usage/cost 并保留其失败记录，未修改生产校验。最终无普通失败、收集错误或 timeout，外层 verdict=fail；10 个 bundle 文件和 6 份快照内 276 个嵌入文件摘要均核验。S8 其余 workflow 仍不因已有注册套件跑过而宣称原要求完整。

## K2 CLI 子阶段纠正（历史子阶段）

已接入十个既有 CLI/route/Research 测试文件，新增根及 301 条命令路径的真实帮助/参数错误矩阵（604 case）、validate 项目根/子目录与显式/默认配置及文本/JSON 矩阵（8 case）。原始执行 610 passed/2 failed；两个默认子目录 validate 问题已登记 ZF-TEST-043。帮助测试有拒绝业务 handler 的陷阱，不执行真实 push/merge/start，不能计作所有 CLI 业务/状态根覆盖。

正式 S0/S1/S2/S6 全部及选取的 Research 机制、Fix Run、验证 Mock E2E：`/tmp/zf-cli-k2-integration-6toc3vu6`，1402 passed/36 strict xfailed，66 入口，439.786483 秒，verdict=fail；没有收集错误、超时或新普通失败。42 个 bundle 文件、38 份快照、382 个嵌入文件及其来源/trace/config/lock 摘要核验通过。完整过程和执行边界见 [steps.md](../03_plans_and_steps/steps.md)，源码映射见 [cli_workflow_coverage_research.md](cli_workflow_coverage_research.md)。

特别注意：Research template 成功链没有 `request_kind=research` 的受控准入身份，只是机制层证明，不关闭官方 route 的完整准入到终局格。K2 完整 workflow、未证明的 CLI/ownership 路径、K3–K6 仍未完成；不累计历史用例数量或将一次组合通过当成原方案全部覆盖。

## K2 Store 子阶段纠正（当前状态优先）

Store 相关未注册测试已接回：TaskStore、Task/Feature 归档、文件锁、Role instance/backend、i1 rebuild、TaskStateMachine、scheduler TaskAttempt 共九个既有文件。新增四个模块的 40 个 case 覆盖完整 Task/Contract/Binding/Evidence 冷读与归档/owner reopen、四 Store syntax/wrong-root 的 cold/warm 检测与合法空对照、Feature/Session 两进程非冲突更新、完整/单份 spine 投影重建及旧 worker 事件终态保护。普通 owner API 是受信任初态构造，不冒充 workflow 准入或 worker 权限。

检测出 ZF-TEST-041（错误根被静默重写）和 042（单份 spine 投影丢失导致漏重建/其他投影翻倍）。全删投影、Session 拒绝、并发更新、旧事件终态保护等控制通过。原始正式运行 30 passed/10 failed；最终 56 入口的 S0/S1/S2/S3 全部及选定 S4/mock E2E 闭包为 **613 passed/29 strict xfailed**，`/tmp/zf-store-k2-integration-ixdg1f2a`，fail-closed verdict=fail。38 个失败包文件、34 份 first/latest、362 个嵌入文件以及当前测试源摘要已核验；不是 ALL/全 S4/发布。精确映射、基础设施错误分类和未运行边界见 [steps.md 的 K2](../03_plans_and_steps/steps.md)。

因此不再把上述 Store 文件视为“尚未注册/未实现”；也不把零字节截断、所有内部字段损坏、间接 ownership 全路径或 K4 普通用例自动取证说成已覆盖。K2 CLI/官方 workflow 映射和 K3–K6 继续，原方案目标没有缩减。下文 K1 及首轮快照保留历史用途，不覆盖此状态。

## K1 后续纠正（当前状态优先于下文首轮快照）

EVT-05 的 helper-only 实现缺口已补为 `tests/conformance/test_zaofu_testkit_s2_event_ingress.py`，注册 S2。真实默认阶段报告、原始 ledger/重开读取/Index/透明调用 observer 和 TaskStore 联合检测覆盖同 Writer、重开与正常 size archive 下同 ID 重送/冲突；6 个精确检测器复现 ZF-TEST-039。新 ID、blocking 更正、真实 created→run_once 的两种消费对照通过，不能外推所有消费者去重。

联调 `/tmp/zf-event-ingress-integration-lwb2gfn3` 为 473 passed/18 strict xfailed，38 个入口；异常签名收紧及 index-only canary 加入后的 S0+最终模块为 `/tmp/zf-event-ingress-closeout-nlur0b76`，16 passed/6 strict xfailed。检测器/证据完整不等于生产通过；固定输入模型的捕获也不关闭 K4 普通 case 自动证据缺口。后续重点为 K2–K6，不再重建 EVT-05 检测器。

另有结构哨兵 47 passed/4 failed（`/tmp/zf-event-ingress-sentinel-ot9qfahh`），登记 ZF-TEST-040。受影响源文件及结构测试与 HEAD 字节相同，保持既有门禁红灯，不在本任务抬高行数阈值。精确执行、摘要核验和边界见 [steps.md 的 K1](../03_plans_and_steps/steps.md)。下文“最新 J”及 K1 待实现描述保留为首轮审计快照，不覆盖本纠正。

## 1. 审计基准与判断边界

- 唯一需求基准：[ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，本次读取全文，SHA-256 为 `8c44ff776aa9876290b72c9bc1666e585a536bf96c2b19422140e1040c0c522a`。
- 当前源码 HEAD：`142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`，工作树 dirty；本轮 `git diff --name-only -- src` 为空。
- 本文定向核对 §1–9、§11–13、§16、§19–22。Provider/API/Web/安全/性能/安装另见 [表面与发布层审计](testkit_surface_requirement_audit.md)。未完成所有源文件逐函数审查，不以“未在选定文件中发现”断言整个仓库绝无测试。
- 依据是测试函数实际调用与断言、TestKit registry、runner/plugin 和 CI；`steps.md` 的历史 done、文件数量或 `coverage_matrix.json` 的文字不单独构成完成证据。
- 本次最新实跑是阶段 J 的 53 个入口：S0/S1/S4/S10 与精确直接调用者，**492 passed、29 strict xfailed**；不是整个仓库或整份方案的全量执行。其他域在本文主要为静态审计。

四种状态必须分开：

| 状态 | 含义 | 下一步 |
|---|---|---|
| 有实现与对应执行证据 | 声明范围内的断言实际执行过 | 保留回归；不外推为整个域完整 |
| 已检测生产缺陷 | 测试存在，精确失败对应开放 Issue | 保持红灯，维护者修复；本任务不改 src |
| 有入口但显式层未验证 | 测试/工具调用路径存在，缺当前环境运行证据 | 按授权、环境和 tier 执行；不能算 pass |
| 测试缺口或待核验映射 | 只验 helper、静态配置，或需求尚无充分行为证据 | 优先找已有用例并接入；确实缺失才新增 |

本轮未把下面的静态缺口登记为新生产 Issue。要登记生产缺陷，仍必须先通过实际入口复现，并与既有 Issue 去重。

## 2. 原始需求到真实测试的映射

| 原方案要求 | 已核实的测试/实现 | 实际证明与剩余边界 |
|---|---|---|
| §3/4：隔离场景、身份、spy、故障、清理 | `tests/testkit/scenarios.py::IsolatedProject`；`tests/test_testkit_s1_lifecycle.py`；`tests/test_testkit_runner.py` | 有独立 state、Run/Task/Attempt/Generation/Operation 字段；进程组超时/子进程退出、证据目录互斥等有测试。本轮 S1 执行；不是每个存量 case 都已自动提供全部 §3 元数据。 |
| CFG-05/08/10：全入口 state_dir | `test_zaofu_testkit_s2_config.py` 的 validate/emit/doctor/status/state clean 等行为测试；`test_zaofu_testkit_s2_surface.py::test_registered_cli_stateful_entries_use_one_state_root_resolver` | 代表入口有实际行为断言，attach/panes/bug-fix-cycle 有 019/023 探针。全注册 CLI 扫描检查 import/名称，不等于每个子命令分支动态验证；还需逐入口映射，不自动补一遍已有测试。 |
| EVT-01/02/03/06/09/10/11：追加、schema、因果、archive、损坏、签名 | S2 EventWriter/损坏矩阵；`test_zaofu_testkit_s11_eventlog_model.py::EventLedgerMachine`；`test_events_log.py`、`test_events_archive.py`、`test_event_writer_schema.py` | 有真实账本和 blocking schema、非法行/UTF-8 半写、archive、事件顺序测试；001/013/034/035 为已有检测器的生产缺陷。schema 的 disabled/warning/blocking 是不同配置，不可把 warning 成功宣称为 blocking 拒绝证明。 |
| **EVT-05：重复 ID 只接受一次、projector 不重复** | `test_zaofu_testkit_s2.py::test_duplicate_event_id_is_detected_as_conformance_failure`；EventLedgerMachine 的重复破坏输入 | 当前 S2 case 主动写两次相同 ID，再期望 **oracle 抛错**；生成模型也有此类 canary。这证明检测器敏感，不证明生产拒绝。`EventWriter._append_with_projectors` 调 append 后运行 projector，仍需真实 ingress + 非幂等观察器的拒绝/无额外投影用例；不能用 helper 绿灯完成 EVT-05。 |
| EVT-12/STO-09：replay 等价 | `test_zaofu_testkit_s2.py::test_replay_snapshot_is_semantically_equal`；`test_workflow_spine_projection.py`；FanoutMachine 的冷投影重放 | S2 示例把同一手写 state dict 传给两个 snapshot，只直接证明 EventLog 读回；真实 spine/fanout 投影另有测试。应逐种声明可重建 projection 的实际 reducer 比较，不把 S2 helper 示例外推为所有 Canonical Store 重建。 |
| STO-01/02/03：当前状态与合法迁移 | `test_task_store.py`；`test_task_statemachine.py::test_backward_transition_rejected` 等；S3/S4/S11 实际 runtime 入站测试 | 仓库有直接 Store/纯状态机测试；当前 SUITES 未直接列出 `test_task_store.py`、`test_task_statemachine.py`（本轮 J 额外执行了前者）。需证明调用闭包或显式接入，不能因某个模块被 import 就声称其 test_* 也执行。纯状态机拒绝不是所有写入路径拒绝。 |
| STO-04/05/06/07/08：Store 所有权、原子性、并发、损坏 | S2 注册 Feature/Session/Role Store 测试；`test_zaofu_testkit_s2_store_crash.py`；S2 multiprocess；`test_task_attempt_recovery.py` | 有真实三类 Store 的 os.replace 前 os._exit，检查旧文件并重新调用正常更新；夹具主动移除孤儿临时文件，不证明生产自动清理它们。并发与损坏测试需各自证据，不能把一种 Store 的结果推广给其他 Store。 |
| STO-10：禁止绕过规范接口 | `test_zaofu_testkit_s2_surface.py::test_integrations_and_web_do_not_write_canonical_files_directly` | AST 只检查指定写方法调用表达式中出现的规范文件名字面量。变量路径、open/write、委托函数等不受这条规则完整覆盖；这是有限静态哨兵，需与行为安全测试联合解释。 |
| ORC-01/02/03/04/06/09/10/12：派发、依赖、重复、报告、重试 | S3 conformance；`OrchestratorDependencyMachine`；`TaskAttemptLifecycleModel`；`ReworkMachine` | 真实 run_once、独立预期、记录型 transport；新增固定和生成序列检测 033/038 等。缺陷留红不意味着要重写这些测试；模型覆盖默认 lane/cap 的范围不等于任意路由。 |
| ORC-07/08/11、§13：fanout/fanin/cancel/restart | `test_zaofu_testkit_s3_fanout.py`；`test_zaofu_testkit_s11_fanout_model.py` | required reader barrier、重复完成、取消/下一独立 run、同目录重开都有实际断言；036/037 开放。顺序 run_once 或服务重建不是并发或硬进程崩溃证明。 |
| **§8.3：每条官方 workflow 的成功/失败/缺依赖/重复/乱序** | `test_flow_smoke_e2e.py`、`test_controller_flow_smoke_matrix.py`；S8 多个 mock E2E | refactor smoke 跑 writer→verify→test.passed；PRD smoke 实际断言 task-map 入库/依赖/verification contract；Issue smoke 为配置 inspection。配置矩阵与共用 Kernel 测试不能直接证明每条官方路由的五类完整运行；先列当前官方路由，再补映射/缺口。 |
| ORC-13/14/15：fingerprint、delta、candidate patch-id | S3 注册 `test_judge_convergence_gate.py`；S8 注册 `test_candidates.py`；阶段 I 的 fingerprint 直接闭包 | 已有相应回归入口，勿因没有统一新模块而重写。最终完整矩阵仍需把每条方法落到明确节点，不能仅用 broad 文件名称作全覆盖证据。 |
| EVD-01…11：artifact/ref/digest/身份/gate | S2 的 sidecar/payload tests；S3 generation；S8 product_acceptance/candidates/artifact_delivery_result | 这些是 ZaoFu 机械准入/交付边界测试，不是产品业务测试。本轮 J 不重新证明整个 EVD 域；生产 sidecar 写入与 ledger 的崩溃窗口仍应单独映射。 |
| REC-02…06：各提交点硬崩溃 | `test_zaofu_testkit_s4_commit_matrix.py`、`s4_action_crash.py`、`s4_persistence.py`、`s2_store_crash.py` | 通用矩阵 `_run_transaction` 是测试自行编排，用真实文件 API 和自身可去重 spy；不能替代生产编排。后补真实 Orchestrator、Action、archive、Store 窗口分别增强证明；Action 032 有两条硬崩溃红灯，不应把它们视作缺检测器。 |
| **REC-05/§11.4：每个实际持久化边界** | 通用矩阵 `after_artifact_write_before_digest_event`；现有 production persistence probes | 通用 artifact 流程用 TestKit `write_json_atomic` 和测试编造的 digest event；尚不能证明生产 sidecar writer→descriptor/ledger 的硬崩溃恢复。需先定位生产入口，复用已有测试；`before_recovery_commit` 等命名需映射实际持久化点而非只注册字符串。 |
| REC-07…12：恢复责任、迟到结果、shutdown | S4 recovery/operation/attempt 注册集，两个 `test_recovery_control_loop_serial.py` case | 本轮 J 两个 serial mock E2E 实跑通过；002/007/013/018/032 等精确红灯保留。对已检出生产缺陷不继续扩大测试变体以等待“自然变绿”。 |
| CON-01/02/03/07/10：共享资源竞态 | S2 multiprocess append/rotation/TaskStore；S4 multiprocess run_once；仓库 `test_state_locks.py` | 真正多进程和 Barrier 已有，S4 独立 Kernel 2/4/8/16/32 进程均检测 003。锁测试有入口但未直接列入 SUITES；是否由明确闭包执行需写清。 |
| **CON-04/05/06：action、fanin、retry/rework 竞争** | `s4.py::test_external_effect_spy_is_at_most_once_under_concurrent_retries`；S3 fanin 与 S11 rework/action 模型 | spy 自行去重的并发测试只证明 spy；顺序模型不能代替 Barrier 下生产 action/fanin/retry。已审查注册文件尚未给出这三条方法的完整共享资源映射，列为待核验，而非断言全仓库没有。 |
| §13：四类状态模型、固定反例、逐步 invariant | S11 EventLedger、TaskAttempt + ContractGeneration、Dependency + Fanout + Rework、ControlledProposal + ActionLoss | 原方案列出的四类模型与动作族现在均有实现，受控预算、固定轨迹、失败快照与 oracle canary 已补。生产缺陷保持红灯；无需默认把每种后端/路由/版本的笛卡尔组合全写成新 Hypothesis 模型。 |
| OBS-01…08/§20：证据与失败回读 | `runner.py`、`pytest_plugin.py`、`failure_bundle.py`、`model_evidence.py`；S9 issue/regression 测试 | JSON 保留三阶段、xfail longrepr；模型快照保存有界 first/latest 原始状态并可用可信模型重放。非模型测试默认 bundle 仅收 JUnit/pytest JSON/stdout/stderr；没有统一为每个普通 case 捕获 fixture 原始状态，也没有自动保证每例身份完备。**“完整 failure bundle”仍有适配缺口。** |
| §19/20/22：入口、门禁、失败→Issue→修复验证 | `tests/testkit/__main__.py`、`SUITES`、`.github/workflows/zaofu-testkit.yml`、S9 tests | 真实 CLI 与只读权限 CI 已存在；runner 把 xfail、skip、未执行、超时挡在绿灯外。S9 mock/临时 Git 证明受控交接，不授权远端发布。CI 当前执行 deterministic ALL，不等同 T5/T6 与全部浏览器/发布层均已运行。 |

上表中的文件除另有说明外均位于 `tests/` 或 `tests/conformance/`；具体注册以 [suites.py](../../../../tests/testkit/suites.py) 为准。

## 3. 不应继续扩大的范围

1. **四类模型不再从零重做。** writer fanout、更多 fingerprint、v0 intent、同版本跨 surface、更多生成式故障排列首先是模型边界，不自动新增为原方案的硬性子任务。只有落到原方案具体行为且确无既有测试，才进入补测。
2. **生产红灯不是改 src 的授权。** 现有 Issue032 的重复提交、033 的预算、034/035 的事件索引、036/037 的 fanin/身份、038 的重试计费均已有检测器；按用户要求交给维护者修复。
3. **不假设全量事件溯源。** AGENTS.md 将 Canonical Store 与事件账本分工；不能删除必需 canonical 文件后让测试从不完整事件猜回语义。replay 等价必须指定哪类投影可重建、保留哪些 canonical 输入。
4. **不把模拟恢复算生产恢复。** helper/fake sink 的自有去重、相同手写 state dict、命名 failpoint 可触发均是基础设施测试，不能单独完成对应生产要求。
5. **不执行远端发布来“证明闭环”。** 原方案 §20 最后一步本就归人工；本仓库规则进一步禁止代理 push/等价远端引用更新。临时 Git/mock 验证和人工操作说明足够界定本任务的安全边界。

## 4. 下一轮有限收敛步骤

| 顺序 | 原要求与动作 | 停止条件（step → verify） |
|---|---|---|
| K1 | EVT-05：研究重复事件的生产准入/consumer 合同，再补真实检测器 | 原入口重复提交 → 事件/非幂等 projector observer/Store 联合断言；原始失败先保留再去重登记 Issue，不修 src |
| K2 | CFG/STO/ORC：当前入口、官方 route 和明确未注册基础测试的可执行映射 | 用明确节点证明覆盖或补确实缺少的行为；成功/失败/依赖/重复/乱序的缺项可逐个关闭，不扩展所有历史 profile |
| K3 | REC/CON：替换“仅 helper 证明”的缺项 | 生产 artifact/恢复提交点硬崩溃；真实共享 action/fanin/retry 同步点；每个原方法至少一条真实、可复现行为证据或精确缺陷检测 |
| K4 | OBS/§3/§20：普通 case 证据接入 | 普通断言失败、setup/teardown 错误、超时均有可分类报告；声明需状态证据的 case 在清理前保留身份、输入、EventLog/Store/产物与摘要，不能只留消失目录的路径 |
| K5 | API/Web/发布层 | 按配套审计列出的原要求补 Schemathesis 状态序列、实际负载/扫描场景及安装矩阵；已有浏览器真值链先复用，不重建浏览器框架 |
| K6 | 原方案最终验证 | 需求→节点→执行 tier→报告→Issue/未运行原因可追溯；分层执行已授权范围，保留真实红灯。测试实现完成和一次完整 run 通过分别判定 |

K1–K6 是后续工作包，不是声称本轮已经实现。条件层缺运行环境时准确记录；仍可独立推进的测试代码缺口不能被包装成“环境原因”而跳过。

## 5. 本轮可核验产物

- 阶段 J：`/tmp/zf-action-loss-model-integration-p70n74md/testkit-run.json`，492 passed/29 strict xfailed，520.043292 秒，pytest exit=0、外层 exit=1/verdict=fail；无收集错误、deselection、timeout，session_finished=true。
- 新模块：22 passed/4 strict xfailed；原始不加 xfail 的报告为 `/tmp/zf-action-loss-model-reproduction-5tprmzn9`，22 passed/4 failed。
- 最新失败包：34 文件、30 模型快照、530 个嵌入文件均已重算字节数与 SHA-256。
- 元数据更新后 S0：11 passed，2.39 秒；ruff、git diff --check 通过。没有当前 J runner/pytest 子进程继续运行；报告和失败证据保留。
- 以上不累计历史结果、不声称新的 ALL 全量绿灯、不提交、不修生产代码。
