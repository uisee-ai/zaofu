# Research fixed / adaptive：异常与同状态目录重建测试设计调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s8_research.py`。已有 Research 路由检测及重建合同；静态候选不自动变成已证实 bug。

> 2026-09-14；research 技能后台只读调研。本代理没有执行测试、CLI、Provider、服务或网络，没有修改 src/tests/config/registry/Issue；仅新增本报告。本文“重建/reopen”严格指重新构造 Orchestrator、TaskStore、EventLog，继续使用同一 state_dir；不指重新打开 terminal Run。本报告沿用 [K2 覆盖边界](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/02_research_reports/cli_workflow_coverage_research.md>)，以下是源码推导的测试设计，不是新一轮运行结果或生产 bug 认定。

## 1. 先区分两级入口，防止绿色基线走错路径

现有 [test_research_fanout_template.py](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:145)两个成功节点直接将 `workflow.invoke.requested` 交给 `run_once(events=...)`，没有 `request_kind=research`。它们确实跑了 Kernel 派发和聚合，却未覆盖正式 Research generation 准入：[DurableCallWorkflowMixin._on_workflow_invoke_requested](/home/uisee/zaofu_v7/src/zf/runtime/durable_call_workflow.py:206)只在 `request_kind=research` 时检查 [research_generation_binding_error](/home/uisee/zaofu_v7/src/zf/runtime/research_generation.py:146)。当前 [S8 注册表](/home/uisee/zaofu_v7/tests/testkit/suites.py:238)已接入这两个 mock 成功链，不再称其“未登记”。

下一阶段宜固定两层，不能混合计分：

- 正式路由入口基线：复用 [test_research_start_invokes_fixed_template](/home/uisee/zaofu_v7/tests/test_control_actions_research.py:160) / [test_research_start_explicitly_invokes_adaptive_pilot](/home/uisee/zaofu_v7/tests/test_control_actions_research.py:206)的受控启动方式，由 [ControlledActionService.execute](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_proposals.py:20)→[research-start](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_research.py:43)→[prompt/generation 物化](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_product.py:973)产生真实 invoke，然后交给 Kernel，不替换 `_try_start_declared_workflow_fanout`。
- fanout 异常机制基线：可复用已有两个成功节点的最小 mock 配置和报告，仅证明该协议层的完成/异常/恢复。若该基线没铸造 generation sidecars，报告必须明确“通用 invoke”，不冒充完整 Research admission。

关键限制：正式 adaptive admission 当前 [明确要求 declared backend=claude-code、reader、allowlist 含 Agent 且无 Write/Edit](/home/uisee/zaofu_v7/src/zf/runtime/control_actions_research.py:703)。因此不能把角色 backend 改为 mock 后仍声称经过正式 adaptive 准入；应保留受支持的配置身份，给 Orchestrator 注入 recording transport，阻断真实 Provider/host 调用。配置中的 backend 名称不等于实际执行 Provider。不要为测试放宽这个生产检查。

安全边界：复用 [recording transport](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:26)的 send_task/is_alive/capture_log/poll_events，仅记录发送并模拟存活；不调用 `zf start`、make_transport或真实provider runner。已读 research-start preflight是stage/角色/permission/provider-session配置检查，但本轮未核清受控启动至全部runtime间接依赖的每个host probe，**不以backend声明或无host marker保证整文件零探测**。正式测试运行前须由主线程保留禁止Provider进程/服务spawn的trap；任何未被fake接管的探测均应显式阻断而非执行。`validate --cold-start`及CLI startup不是本矩阵必经入口。

## 2. 最小有效输入及身份来源

| 层 | fixed | adaptive / 共同约束 |
|---|---|---|
| Stage / role | [research_fanout.yaml](/home/uisee/zaofu_v7/tests/fixtures/research_fanout.yaml:1)：`research-fanout`、`fanout_reader`、4 children=`source_researcher/product_analyst/technical_analyst/risk_critic`，synth=`synthesizer` | `research-adaptive`、单 child=`research_root`、无 synth。正式入口必须显式配置 children，不能照搬机制测试里的 `children=[]`；[research_stage_contract_error](/home/uisee/zaofu_v7/src/zf/runtime/research_templates.py:83)要求 children 精确匹配注册模板。最小正式 fixture 可从 [control-actions _config](/home/uisee/zaofu_v7/tests/test_control_actions_research.py:40)裁取两 stage，不加生产副作用 sidecar。 |
| 聚合事件 | child success/failure=`research.child.completed/failed`；stage success/failure=`research.fanout.completed/failed`；wait_for_all | adaptive child 类型相同，stage=`research.adaptive.completed/failed`。当前实际 profile 分别 timeout=1800/1500；测试可显式使用较小 timeout 并注入时钟，不能真实等待（[profiles](/home/uisee/zaofu_v7/examples/prod/controller/common/profiles.yaml:157)）。 |
| Task | 使用 TaskStore 新建隔离项目 Task；运行态目录不必叫 `.zf`。若要验 Research 自身完成 Task，开始前标为 workflow-managed、in_progress、无 Request binding | [complete_standalone_research_task](/home/uisee/zaofu_v7/src/zf/runtime/workflow_task_lifecycle.py:154)还要求 result lineage 匹配；request-bound parent 必须保持 active。新建 fixture 可以用 [mark_workflow_managed_task](/home/uisee/zaofu_v7/src/zf/runtime/workflow_anchor.py:100)；不要测试后直接把 Task 改 done/blocked。 |
| Invoke | 正式入口传 `task_id/topic`，fixed 默认模板或显式 `research-fanout.fixed.v1` | adaptive 显式 `research-adaptive.pilot.v1`；服务生成 request_kind、route/pattern、prompt refs、workflow_run_id、workflow_generation/expected_generation、effective-config/run-contract refs+digest、task/role/template/prompt digest；来源 [materialize_research_generation](/home/uisee/zaofu_v7/src/zf/runtime/research_generation.py:50)。不手填一组假 digest。 |
| 机制 invoke | 既有 fixture 使用同一个 Task id、`active_dispatch_id=disp-research`，invoke 中 dispatch_id 精确相同，expected_output 非空 | [task fanout handler](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_reactor.py:5423)检查 Task、expected_output、活动 fanout 与 dispatch；不要把 `workflow_invoke_pattern_id` 当外部必填字段，该字段由 [declared-stage bridge](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_reactor.py:5639)生成。 |
| child 回报 | 从实际 `fanout.child.dispatched` 读取 fanout_id、stage_id、child_id、run_id、role_instance、task/parent/run lineage；报告 actor 对应派发 role | 若实际派发包含 operation_id/request_hash/attempt/lease/contract 等身份，则按其 briefing/dispatch 复制，不能从上一代沿用。生产 [prepare_call_operation](/home/uisee/zaofu_v7/src/zf/runtime/call_result_runtime.py:69)负责铸造 call 身份；[briefing 合同](/home/uisee/zaofu_v7/src/zf/runtime/fanout_briefing_runtime.py:381)给出完成/失败结构。 |
| child 报告 | 最小成功 `report` 为 JSON object，summary 字符串、findings list、合法 recommendation（如 approve）；失败使用 child failure 类型、status=failed、reason 和失败报告 | 正常基线应给真实存在于 tmp 项目的可核验 evidence ref；不能把 `source:fixture` 当作源文件已核验。adaptive 加完整 root 语义字段和可验证 provider-operation summary，但不把 fixture 的自报 usage/cost 宣称真实测量。 |
| synth 回报 | 等真实 `fanout.synth.dispatched` 后，以其身份提交 `fanout.synth.completed`，report.recommendation=`approve` 或 `reject`；成功 summary 非空 | adaptive 不提交 synth。其 root report 包含 provider summary、findings/acceptance/test/task-map 等字段的现有例子在 [adaptive success node](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:364)。测试只核字段保持与机械证据关系，不评判研究内容质量。 |

持久化是本轮重建测试的前提：[run_once](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:864)的 pushed events 是 watcher 已读取的批次，[react_to_events](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:2412)不会自动把所有传入原事实写入 ledger。下一阶段应经 EventWriter append/emit 记录 worker 输入，再将返回的同一事件对象交给 `run_once`；不能只在内存 push 后期望新进程能找到原报告。重建时保持同一 config、project_root、state_dir，不复制私有 `_processed_event_ids`、manifest cache 或 transport 的历史 sent 列表。

## 3. 正常基线的独立 oracle

| 观察面 | fixed | adaptive |
|---|---|---|
| transport | 恰好 4 个不同 child 派发；未全 terminal 前 synth=0；最后恰好 1 个 synth | 恰好 1 个 root，整个过程 synth=0，不能把 Provider-native children 计为 ZaoFu 子 Task |
| ledger / aggregate | 每个 child 恰好一个 canonical terminal；一个 `fanout.aggregate.completed(status=completed)`，一个匹配 stage success；synth source/causation 链匹配实际输入 | 同样唯一 aggregate/stage success；root result lineage 来自实际 child result；没有 synth source |
| artifact | 从 aggregate descriptor 定位 `artifacts/research/<Task>/<fanout>.md`，独立读字节算 SHA-256，核对 task/run/fanout/result identity、关键语义正文保持 | 另 hydrate provider summary descriptor，检查 summary 的 run/op/session/settlement/counts；`available` 不等于真实 Provider E2E |
| result | 恰好一个 `workflow.result.available`，其 terminal_event_id/causation_id 指向真实 aggregate，ref/digest 等于实物 | `root_result_event_id` 非空、`synth_event_id` 为空；fixed 反向检查真实 synth identity |
| Task | standalone managed/in_progress：一个 research_artifact_delivery status_changed→done 和一个 done evidence；request-bound parent 保持 in_progress | 同左。普通非 managed Task 不应套用 standalone done 期待。已有 fixed fixture未检查 Task done，不能从 channel success 推定。 |

上述生产链为 [reader evaluation](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:5875) / [synth finalization](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:7182)→[research_delivery](/home/uisee/zaofu_v7/src/zf/runtime/research_delivery.py:23)→[result availability](/home/uisee/zaofu_v7/src/zf/runtime/workflow_results.py:21)→[Task lineage gate](/home/uisee/zaofu_v7/src/zf/runtime/workflow_task_lifecycle.py:459)。直接构造 aggregate/result/Task done 只能测试下游 helper，不能作为这条基线。

## 4. 有限异常矩阵：输入 → 分层期待

以下保持正常 baseline config，不额外添加 synthetic success criteria。每行使用独立 fixture；“源码预期”仍待正式复现，不能据此登记 Issue。

| ID / 适用 | 经生产入口提交的变体 | aggregate / Task / ledger / artifact / transport oracle |
|---|---|---|
| F1 fixed child 失败 | 四个真实 child 中一个提交 `research.child.failed`，其他三个成功；最后 synth 用 reject 报告收束 | child 失败**不立即等于整个 fixed 失败**：全 terminal 后仍派 synth。reject 后 aggregate=failed、stage failure 一次，无新的 result.available/Research artifact/Task done；第一次失败的 Task 通常仍 in_progress，随后可能有 bounded replan。若 synth 给 approve，当前 [finalize](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:7204)以 synth verdict 为准，不能预设它自动因 child failed 而拒绝。 |
| F2 adaptive root 失败 | 唯一已派发 root 提交 child failure + failed report | [无 synth 聚合分支](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:5909)直接 failed aggregate、research.adaptive.failed；该阶段 artifact/result/done evidence 均无新增，Task 不因 aggregate 自动变 failed/blocked。transport 不派 synth。 |
| M1 两路缺一条完成事实 | fixed 留 1 child 未回报；adaptive 不回报 root；先少量 idle ticks且时钟未过 deadline | 全体未 terminal，不得 success/result/done；fixed 不得提前派 synth。保留其他 child 原报告/运行态，不能把“还没完成”判失败。再单独推进注入时钟超过 stage timeout，走 [timeout sweep](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:6402)，参考 [真实 idle timeout 例](/home/uisee/zaofu_v7/tests/test_fanout_hardening.py:377)检查 pending children、timeout/failure 与 boundedness，不真实 sleep。 |
| M2 fixed 缺 synth 回报 | 四 child 全 terminal且 synth 已派发，留 synth 未完成；推进注入时钟 | 应经 [synth timeout](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:6729)产生失败终局，无 result/done；现有 [test_synth_timeout_fails_aggregate_instead_of_hanging](/home/uisee/zaofu_v7/tests/test_fanout_hardening.py:1202)有对应真实 runtime 形状，可替换为 Research stage。不要让仍在运行的 mock transport 被误当作完成。 |
| M3 两路缺 report/summary | completion 中去掉 report；分别保留/去掉所有顶层和 report summary；fixed 最终仍通过真实 synth 回报入口 | **不应统一期待 child failed。** [fanout_child_report](/home/uisee/zaofu_v7/src/zf/runtime/fanout_evidence_queries.py:632)把非 dict/missing report转 None，[validator](/home/uisee/zaofu_v7/src/zf/runtime/fanout.py:233)合成默认报告；若最终无 summary，[artifact materializer](/home/uisee/zaofu_v7/src/zf/runtime/research_fanout_artifact.py:23)返回空 descriptor。当前基础分支可能 aggregate completed，但无 Research artifact/result.available/Task done；[success payload contract](/home/uisee/zaofu_v7/src/zf/runtime/writer_fanout_data.py:789)无 Research 专属 summary gate。须将该组合如实记录为待验证行为，不拿 aggregate success 当已交付。 |
| M4 两路报告类型错误 | 使用 dict report，但 `findings` 为字符串或 report.status 非法枚举，确保命中 validator而非 missing-report fallback | canonical child failed含 `malformed_report/report_diagnostics`；fixed 继续等其他 terminal和synth，adaptive按 F2。源 [validate_fanout_report](/home/uisee/zaofu_v7/src/zf/runtime/fanout.py:233)与 [child failure projection](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:4975)。report=[] 并非可靠的 malformed 探针，因为上游会当缺 report。 |
| D1 两路重复 child | 对一个已正常接受的真实 child report，先重投同一事件 id，再用新 event id提交相同 child/run/body；fixed 在 synth 前做，adaptive在其终局后做 | 同 id由 processed 集合抑制，新 id应由 [child terminal guard](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:4755)抑制；canonical child terminal不增加，已交付 aggregate/result/done/artifact摘要不变。仅 transport.sent 不增加还不够，要查 ledger和Task。 |
| D2 fixed 重复 synth / 两路完成后回放 | 完整 baseline后，同 id/新 id 重投对应最终报告，再数个 idle ticks | [aggregate terminal guard](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:7201)不重复 finalize；result去重键为 terminal_event_id+artifact_digest（[workflow_results](/home/uisee/zaofu_v7/src/zf/runtime/workflow_results.py:54)）。只比较指定 run/fanout 的业务事件，不要求整个 ledger长度不变，因为决策/诊断记录可以增加。 |
| O1 fixed 合法乱序 | 逆序或固定置换提交四 child报告；每次读取实际已派发身份 | 前三份不派 synth/不终局；第四份恰好一次 synth。四 child report与独立 evidence各自归属不串，最后可达正常基线；不扩展随机排列全集。 |
| O2 非法提前 synth / 未派发身份 | 在 fixed尚有pending child时，尝试提前提交 synth completed；或 child report携带未发生的 child/run身份 | 这是保护性负向 case，期待不抢先完成/不污染正确 child。但 [direct synth handler/finalize](/home/uisee/zaofu_v7/src/zf/runtime/plan_synth_runtime.py:706)的可见分支不直接检查所有 child terminal，外围 admission/lifecycle是否拒绝须正式运行确认。不能把私有 finalize直接调用成功当漏洞，也不能先写死必有某个拒绝事件。adaptive无合法 synth身份，同样只作无越权终局断言。 |

F1/F2 的失败后状态应分时间点观察：第一次 failed aggregate没有调用 standalone completion；如果继续消费真正产生的 stage failure，[配置注册 reactor](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_reactor.py:464)按 [STAGE_REPLAN_CAP=2](/home/uisee/zaofu_v7/src/zf/runtime/stage_failure_replan.py:424)可能产生 rework trigger。只有真实 cap escalation→[run.goal.blocked](/home/uisee/zaofu_v7/src/zf/runtime/escalation_terminal.py:144)后，[Run terminal Task projection](/home/uisee/zaofu_v7/src/zf/runtime/workflow_task_lifecycle.py:269)才转 blocked。不要伪造 `run.goal.blocked` 来让 route失败测试“完整”。两路重复失败穿过正式 generation admission是否均能有界重开尚未实跑；本轮可先验首个失败边界，后续 bounded链单列，不统一断言 Task failed。

## 5. 核心 reopen 矩阵：同 state_dir 重建，不逻辑复活

固定 4 个切点即可，不与上一表做任意笛卡尔积。重建新 transport 的 sent 从零计数，另保存旧 transport发送记录作为历史证据；同一模拟角色的 is_alive/进度必须一致，避免“服务重建”变成“provider已死，合法重派”。

| ID / 切点 | 重建后的真实输入 | 独立期待 / 源码依据 |
|---|---|---|
| R1 child 尚未全回报 | fixed完成两 child后重建，回放其已完成原报告，再交其余两份；adaptive root已派发但未回报时重建，再交真实root报告 | 不重新派已完成 child；最终只一次 synth或零 synth、一次aggregate/result/done；保留 fanout/run/generation、已完成child报告/ref。读 [reader result replay](/home/uisee/zaofu_v7/src/zf/runtime/reader_fanout_result_replay.py:34)与 [recovery顺序](/home/uisee/zaofu_v7/src/zf/runtime/fanout_recovery_runtime.py:10)。 |
| R2 原报告已入 ledger、尚未被 Kernel消费 | EventWriter追加最后一份 child事实（fixed也可选 synth事实）后立即丢弃旧 Orchestrator；新实例读取 tail或收到同一持久化事件 | 真实恢复后产生缺失canonical child/aggregate，必须一次而非零次或两次；不手写manifest status/aggregate。`run_once(events=[])`会触发 [replay sweep](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_periodic_sweep.py:11)，但一次 sweep可能只做一步，允许小的固定 tick上限并在超限时打印真实状态。 |
| R3 fixed synth已派发、尚未回报 | 重建后交实际synth报告，再重投一遍 | synth不再次派发，正常生成唯一aggregate/result与同实物digest；[synth result replay](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator_fanout.py:979)要求children terminal、manifest非terminal，参考其限制。没有活跃carrier时合法恢复重派须另案，不混进本断言。 |
| R4 已完整成功或首个失败aggregate已落盘 | 重建后回放旧child/synth结果+idle tick；成功/失败各保留一个代表 | 成功不能新增result/done或重写不同正文；失败aggregate不能因旧成功回报变success。失败后的合法replan可能另起fanout，因此按原fanout/原generation分组，不错误要求全系统transport永久零增长。Task应与其已真实到达的in_progress/blocked/done一致，不能靠重建重新激活terminalTask。 |

重建 config必须保持原值：正式Research请求已绑定不可变config/prompt/role/task digest。配置漂移后 [startup generation reconciliation](/home/uisee/zaofu_v7/src/zf/cli/start.py:1037)→[supersede/cancel](/home/uisee/zaofu_v7/src/zf/runtime/research_generation.py:291)要求 `restart_from_admission`，这与本矩阵“同配置、同运行继续处理”不是一回事。逻辑 reopen、新generation授权和产品配置迁移不纳入本轮实现清单。

## 6. 可复用测试与真实覆盖等级

| 现有节点 | 可直接复用内容 | 不应声称 |
|---|---|---|
| [test_research_fanout_template :145 / :278](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:145) | 已登记S8，recording transport/Task/child报告/两成功链；可作为异常矩阵基础 | 已过正式research-start generation准入、真实Provider或语义研究质量验证 |
| [test_control_actions_research :160 / :206 / :609](/home/uisee/zaofu_v7/tests/test_control_actions_research.py:160) | 未见当前S8登记；真实service start、fixed/adaptive身份生成、stage缺失时无invoke | 整文件已完成host安全审计；本轮只推荐这些精确节点和fixture，不直接加整个大文件 |
| [config drift :626](/home/uisee/zaofu_v7/tests/test_control_actions_research.py:626) | startup reconciliation重复调用幂等、旧invoke拒绝、新generation准入的节点模板 | 真dispatch恢复：该测试把 `_try_start_declared_workflow_fanout` 替换成True；它不代替 R1～R4 |
| [test_research_artifact :136 / :177 / :224](/home/uisee/zaofu_v7/tests/test_research_artifact.py:136) | 未见当前registry登记；全文3例仅tmp sidecar/query/helper，可复用完整正文/ref保持与无summary不物化断言 | 全route closure；其helper fixture把adaptive结果写成 synth事件，而且query例人工构造aggregate |
| [test_workflow_results :46](/home/uisee/zaofu_v7/tests/test_workflow_results.py:46) | 未见当前registry登记；全文3例tmp EventWriter/helper，验证result键级幂等和origin诊断 | descriptor对应真实artifact；fixture sha=`a*64`且人工terminal，不是实物验真 |
| [test_provider_operation_summary :37～114](/home/uisee/zaofu_v7/tests/test_provider_operation_summary.py:37) | 未见当前registry登记；全文5例纯validator/tmp sidecar，无Provider调用；可复用身份/活跃children/count/并发上限负向数据 | Research整体必须因此失败；[materializer](/home/uisee/zaofu_v7/src/zf/runtime/research_fanout_artifact.py:302)会记录summary issues，是否无result另需route证据 |
| [standalone :348 / request-bound :384](/home/uisee/zaofu_v7/tests/test_workflow_managed_task_lifecycle.py:348) | 未见当前registry登记；精确证明Task单次收束与request-bound不关闭 | 上游报告→aggregate真实闭环：fixture直接建sidecar和terminal/result事件 |
| [control recovery :134](/home/uisee/zaofu_v7/tests/test_control_actions_recovery.py:134) | 当前已登记，说明显式新generation与Task重新激活属另外受控操作 | 本轮“新建Orchestrator对象”的前提；不要将它变成本轮必做审批链 |

## 7. 收束与实施防误判

先做两条可核验成功baseline，接 F1/F2、M1/M2、D1/D2、O1 和 R1～R4；M3/M4/O2用于明确格式与越序边界，不因静态推断新增生产Issue。正式route身份生成与fanout机制两个层级须在case元数据中明确，不能以去掉 request_kind/generation gate取得绿色后标“官方准入闭环”。

summary、evidence、recommendation、Provider child provenance及研究结论都是语义输入；[adaptive briefing](/home/uisee/zaofu_v7/src/zf/runtime/research_adaptive_contract.py:75)明确由Root负责填写。测试可以提交有意构造的合法/非法worker事实，但不得预写 `research.*.completed`、`fanout.aggregate.completed`、`workflow.result.available`、`task.done.evidence` 或直接Task终态来替Kernel完成。artifact正文/摘要缺失与telemetry缺失分开；机械Task lineage检查不等于逐条引用内容真实或用户产品验收已完成。

退出标准：每个有限case给出实际入口、独立ledger/Task/artifact/transport断言、原run/fanout/generation范围与重建切点；未确认边界保留“待正式复现”。不继续扩展其他Provider/archetype、Research adoption全功能或产品测试。
