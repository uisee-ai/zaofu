# 持续推进、恢复效果与无人资格

## 2026-11-19 冻结候选的验收接续

K5 已完成16项 Task，但最终 Reader/Goal 未完成，且诊断收尾已取消 Run。
`run-resume` 只恢复 paused；取消记录不能通过改状态复活。`candidate-continue`
增加显式操作员令牌控制的后继入口：先以 source_run_id、candidate_event_id 和
dry_run=true 获取预览，再提交同一输入、reviewed_digest 和 approval_ref。
入口为 `POST /api/projects/{id}/actions/candidate-continue`；同摘要重复提交不创建第二个 Run。

预览及实际 admission 均核对终态、最新冻结候选、Git ref/提交、当前代际完整 Task
集合、done 状态、独立 Verify 准入、当前 Task 合同权威、不可变 Plan/端口、原批准、
Goal claims 与执行策略摘要。预览可生成不可变校验快照，但不创建任务或派发 Provider。
任何输入漂移都需要新预览。新 Run 沿原 admission/队列/暂停/终态门禁进入配置的
Candidate Readers；不发布 task_map.ready，不重新执行 scan、Plan 或 Impl。

新 Plan package 显式继承原端口并记录来源包，Goal claims 与产品验收规格（PAS）只
重绑定运行身份，原始条款、用户旅程和断言保持不变；新候选合同
绑定新 Run，子 Task 权威仍指向旧 Run。旧 Run、Task、验证事件不改写，不建立 Run
别名，也不导入旧 Candidate passed。宿主采证仅复用同一目标的逐命令最新观察
（包括失败），原始执行事件仍保留；Reader 独立判断适用性和缺失项。最终 Judge 的
事实及必读证据包含接续授权、历史批准和 Task 结果来源，当前验收仍走原准入门禁。

受控动作回执先于 invoke 持久化；接纳、Goal、包及候选各步幂等。既有 Orchestrator
空闲重放入口补齐已接纳但入口生命周期不完整的崩溃窗口，paused/terminal 不补发。
Goal/Plan 事件必须通过实际 v5 schema，写入拒绝立即停止后续 Candidate 发布；
历史上已发 Candidate 但缺失 Goal/Plan 准入的情况同样幂等补齐。Reader 的 canonical
handoff 仅经接续回执鉴权后读取原 TaskRef 来源，新冻结候选和包仍绑定新 Run。
Reader 派发入口同样先补齐接续生命周期；不能让启动时积压的 Candidate 先派发，
再等待空闲巡检修复，否则会浪费一次 Reader 重试。补齐失败时禁止启动 fanout。
当前范围限定同项目、已取消/失败的原始 Task Pipeline Run；不支持接续的再次接续，
也不把历史8×8并发记作新源码从头无人资格。需要改产品候选时仍走正常修复规划。

任务：[冻结候选接续](../../tasks/active/2026-11-19-1008-candidate-continuation.md)。
验证记录位于 `reports/candidate-continuation-20261119/`；真实接续完成与否单独记录，
源码/mock 通过不能把未完成的 P2 资格归档。

## 2026-11-19 Reader 失败路由与 idle 预算

K5真实轮第二Candidate的输入读取失败被预授权宿主协调器错误选中，额外执行19条
命令。该协调器现在为Candidate读取已经准入、摘要绑定的结果；`reader_execution_failure`
留给既有诊断/Reader恢复流程，不以产品命令执行冒充输入修复。真正的runner失败
仍保留原宿主授权、冻结目标、合同与执行预算门禁。未改写历史失败或生成产品通过。

真实分页续跑已证明保持同一TaskAttempt，但隔离回归发现idle分支仍按全部dispatch
计算故障次数。现仅豁免具有kernel进展claim、前序dispatch和完全匹配的
role/Run/operation/request/语义attempt/transport dispatch身份的正常续读。
真正故障重试继续计数，未知、伪造、跨操作及未来claim不享受豁免；原idle边界、
暂停、租约、权限与最终结果门禁不变。该idle故障是mock复现，未冒称K5现场触发。

方法层补充恢复时逐命令对账：执行事实已覆盖原命令时，Reader继续必读和独立判断；
本地Docker权限拒绝不推翻既有授权执行事实，也不能把尚未阅读误写成缺少执行。
这一语义判断仍由agent/skill负责，runtime不根据exit0代填passed。

任务：[Reader恢复修复](../../tasks/done/2026-11-19-0917-reader-recovery-routing.md)。
K5的16项Task、8×8重叠89.420秒和原始浏览器通过保留，但最终Judge/Goal未完成，
诊断收尾取消1次，整体资格未通过。完整额度主动探测/自动恢复不在本补丁范围。

## 2026-11-19 OA 旧任务提示失效

同轮的 `TOOL-MAX` 已在 `evt-d90ef9d24cc1` 经独立验证和集成完成，但
`evt-5a8f4d563187` 仍被 Run Manager 列为 Run 级待人工决策。旧 OA semantic_failure
提示没有 Task 字段，原有 Task 故障失效逻辑无法识别其目标。

现在由既有 `task_failure_retired → Run Manager` 投影沿 kernel 决策准入、
checkpoint 和 Task 范围故障请求的因果链确认原 run/task/map。只有提示之后出现
匹配的 kernel `task.done`，authority 为 `task_integration_receipt` 且带集成回执绑定，
才使该旧提示失效。只改变派生投影；不改原账本，不伪造用户批准，不派发恢复命令。
没有准入、来源冲突、不同 Task/Run/map、worker 自报完成、普通审批或新故障都保留。
Agent submitted 事件本身不提供权限，必须由 exact kernel admitted 决策绑定；
诊断 correlation_id 可是诊断编号，其显式 workflow_run_id 才是 Run 身份。

任务：[OA故障提示失效](../../tasks/done/2026-11-19-0044-oa-task-escalation-retirement.md)。
当前冻结真实轮不热加载此修复；真实账本只读回放不等于新版无人全流程通过。

## 2026-11-19 工作区发布等待范围

额度恢复后的隔离32任务轮证明，worker名称复用不等于原任务工作区仍被占用。
`TOOL-MAX` 的 Impl turn 在00:02:35结束；`dev-lane-0`随后执行其他独立Task，
旧候选直到00:20:58才发布。隔离mock复现：8个待发布候选中，旧worker在另一个
工作区执行会使其中1项持续等待；该turn结束后才恢复。此前发布批量8没有消除这个条件。

发布协调器和受控宿主动作现在共享同一机械核验：只有真实kernel hook的
session/turn、causation指向的kernel派发、唯一当前running operation、run/task/role/
dispatch及cwd均对应另一个Task，两个工作区不重叠且均在state_dir内，原工作区
进程持有者检查确认空闲，才允许继续原授权动作。同任务、未知/伪造/不完整绑定、
同目录/嵌套目录或遗留进程均保持等待。没有扩大可写路径、Run预算或发布verdict，
同一任务的新operation仍由原binding gate拒绝。

本项见[发布工作区修复](../../tasks/done/2026-11-19-0033-publication-workspace-fence.md)。
冻结真实轮不热加载此补丁；mock/源码回归不能替代修复版真实8×8资格。

## 2026-11-18 真实资格复核纠正

32任务轮 `workflow-8cacd612404a641a` 已有32项 TaskVerify/集成完成及冻结候选的真实
Docker浏览器证据，但最终Judge/Goal未完成，实际8 Impl + 6 TaskVerify，8+8重叠为0。
批处理轮 `workflow-fd9e9212ab345b77` 停在第二次Plan Critic，尚未批准Plan。
两轮复核发现测试脚本指定的 `TMUX_TMPDIR` 目录均不存在，tmux回退默认server，
相同的product会话内混有两轮pane。此前“运行互相隔离”的判断撤回，重叠期间证据
不得用于无人资格通过；不删除原始账本。Provider同时报告usage_limit_reached，
外部额度不能由自动恢复绕过。两轮已正常停止并保留代码/证据，停止属于监工介入。

补充修复在 [隔离及外部门禁任务](../../tasks/done/2026-11-18-1545-isolation-and-external-gates.md)：
显式TMUX_TMPDIR必须是现存、可写且可搜索的目录，否则在执行任何tmux命令前失败，
不自动回退默认server。测试启动还必须实际确认socket路径、独占会话名与pane所属工作区；
环境变量本身不证明隔离成立。

Reader初始派发和重试将transport证明“输入未发送”的交互门禁记录为既有
fanout.child.dispatch_deferred。TaskAttempt仅在当前delivery claim仍为delivering时
原子释放到prepared，维持同一attempt/dispatch/operation及预算，并续接等待租约；
门禁解除后通过原入口投递。Task Pipeline的Impl/Verify在激活和投递边界同样记录等待，
不增加派发失败计数或触发冷却。已发送、被替代或结果不明的claim不会回滚。
只做机械等待，不自动确认额度、登录、信任或权限弹窗，不增加故障重试额度。
Mock、host隔离探针和真实provider资格分层记录；前两者通过不表示真实8×8已通过。

本批按用户批准的 P0→P1→P2 顺序收敛 [8×8 恢复](eight-by-eight-recovery-closure.md) 的真实缺口。
基线 dev `70295cc8`。来源为本地 ZF-SPEC-001/002、207–209；源码闭合与独立真实资格分别对账。
批准任务为 `tasks/active/2026-11-18-0933-*`；未取得完整验收证据的任务保持 active。

## 实现与边界

| 项目 | 行为与实际入口 | 验证/限制 |
|---|---|---|
| P0-01 | Reader 恢复扫查按同一 operation/request/冻结 attempt 的新增必读覆盖续跑；沿既有 continuation 事件记录 claim/投递 | 重复页不延长预算；failed turn 仍属故障恢复。暂停、审批、待完成工具/协助、未知投递不重放；单个等待不阻断其他 Reader 的恢复扫描，结果仍走准入 |
| P0-02 | 既有 CallResult correction + submit 保持原读取与结果身份 | 真实 Orchestrator 调用链测试证明无效 verdict 修正后合法结算，无须重读/重跑业务；本项 verified-existing |
| P0-03 | 已批准端口可声明 task_input_policy.candidate_paths；Candidate 必读 shared + 完整验收区，旧包仍全文 | 路径、digest、版本机械校验；语义完整由 Planner/Critic 从来源反向对账。contract/target/完整矩阵保留 |
| P0-03 | 新 Plan rework manifest 对相同大对象/数组只读一次；第二来源所有独有字段仍必读 | read_equivalents 在 access_scope 中绑定第一份 source/digest/path，原文不变。部分授权不裁剪，生产者不能自行声明内核分区 |
| P0-04 | 预授权采证返回 Reader 前对账整个冻结命令合同，缺证明确等待而不重复重开 | 不扩大白名单，不伪造产品 verdict；失败命令仍可提前交给独立评审。新 fixture 预授权全部19条命令，保留100次执行上限 |
| P1-01/02 | 正式失败 fanout occurrence 也结算实际 retry transport attempt，原故障归 workflow 处置 | 修复租约影子失败重复触发 AR。旧账本副本回放：4项遗留→0，重复回放0改动，不改真实旧账本 |
| P1-02 | 保留 RecoveryCase 单 owner、原故障 effect、诊断→提案→独立验证→授权应用/加载/恢复链 | 已有机制专项验证，不将 proposal_only 提升为自动应用；没有证据的恢复保持等待 |
| P1-03 | worker.state.changed 在实际 housekeeping 入口同步驻留状态与 RoleSessionRegistry | 只消费 kernel 来源，沿既有 task-generation fence，保护新 writer；跨进程 reset 清理驻留 circuit/counter，无须重建投影 |

代码没有新增任务状态机或 Task schema，不根据 token 数、运行时长、消息活跃或模型自述宣布完成。
Planner/Run 的时间与 token 限制仍为0。诊断、宿主探针各自已有边界保留；未通过放宽权限来修复失败。

## 验证分层

证据目录：`reports/unattended-progress-20261118/`（运行证据不随源码提交）。

- `p0-01-red.txt`、`p0-boundary-red.txt`、`p1-settlement-red.txt`：先复现进度误耗故障预算、等待保护、部分采证提前重开、失败重试结算遗漏。
- `p0-01-callers.txt`：185 passed；`p0-01-closure.txt`：261 passed；各集合重叠，不合计为唯一测试数。
- `p0-input-overlap.txt`：44 passed（后续补充防伪声明反例进入最终闭包）。
- `p1-settlement-green.txt`：127 passed；`p1-lifecycle-diagnosis.txt`：114 passed，包含8槽位与动态接续 mock E2E。
- `p1-replay-baseline.json` / `p1-replay-fixed.json`：真实旧账本的隔离副本复现与修复对照。
- `closure.txt`：4388 passed / 8 skipped / 12 failed；12项旧断言期待“无效补充证据仍重开Reader”，已改为验证等待及缺失命令，不删除安全反例。
- `closure-final.txt`：4402 passed / 8 skipped，sentinel 53 passed、flow-smoke 10 passed；此后跨进程生命周期修复另跑闭包。
- `closure-lifecycle-final.txt`：4920 passed / 8 skipped / 4 failed；4个历史正例未标 kernel 来源，已与真实调用方对齐并保留 worker/external/无来源负例。CLI unblock 同步标明 kernel 来源，重放按 instance_id 而不是 operator actor 归属。
- `closure-release.txt`：最终影响闭包 6074 passed / 8 skipped；合并哨兵 53 passed、流程冒烟 10 passed。不是全库或真实工作流验收。
- `reader-fairness-release.txt`：补充等待隔离修复的调用方/并发闭包 441 passed；`fairness-sentinel.txt` 53 passed。增加8槽位与恢复 mock 覆盖，所有等待防重放断言保留。
- `p2-terra-preflight.txt`：1 passed，157.97s；真实 GPT-6.1 Terra High 两个新会话、受控输入、未提交工作保留与继续实现。它不等于8×8工作流通过。
- `browser-preflight.txt`：现有 mcp/playwright 镜像内 Chromium 149.0.7827.22 正常启动；这只是环境预检，不是产品验收。

## 真实资格

旧实验 `/tmp/zf-eight-recovery-20261118T002529Z` 已正常停止，保存账本摘要和退出回执；原始状态/源码/介入证据保留。
准备轮 `/tmp/zf-unattended-eight-20261118T100101Z` 在 Plan 批准前停止：补充并发测试复现了“等待一个 Reader 提前结束整个恢复扫描”，故不能作为最终资格轮。
修复将无变更等待与已执行恢复区分；`reader-fairness-baseline-confirmed.txt` 用已提交代码和当前代码对照，证明前者阻挡独立 Reader，后者恢复且保留未知效果等待。最初测试遗漏 backend 和固定扫描顺序，修正后重新复现，未把测试装配失败计作产品缺陷证据。
新实验 `/tmp/zf-unattended-eight-20261118T102607Z` 使用冻结源码 `33a343ed`（已合入 dev `ebaa20f5`），已启动16个独立 TypeScript 合同和8+8池。
Plan/Critic 使用 Astra High，Impl/Verify 使用 GPT-6.1 Terra High；Plan `evt-adacb064b307` 经独立 Critic 通过、同版本预览/条款/32个互斥写入路径审阅后，由受控动作 `evt-c59b97660bc9` 于 2026-11-18 11:07:24 UTC 批准。
实际 Task 输入已验证只必读共享约束和自己的任务区；本轮 Plan 未声明可选 `candidate_paths`，因此不能把本轮算作该可选路径的真实验证。该路径目前只有确定性合同验证。
Plan 必须审阅批准；批准后冻结 config/合同/源码/授权，观察者只读。
任何追加提示、补采证、状态修复或加载补丁都记为本轮介入，不能回填无人资格。
分别报告业务 Goal、真实 Impl/TaskVerify 峰值与重叠、故障自动恢复和介入次数；配置8不等于达到8。
最终仍须真实 Candidate/Judge/交付证据及清理后才能归档 P2。

### 当前轮的并发证据限制

本轮只有16个独立任务。第1项 `TASK-unique_words` 于 `evt-e1b219f7fe27` 完成时，Verify峰值仍为2，未出现8+8重叠；其余15项不能产生16个独立任务阶段同时运行。
因此这轮即使业务 Goal 和交付成功，也只能证明所观察到的无人推进链，不能满足完整8+8验收。保持冻结合同，不追加伪任务、延迟完成或用两个Run的槽位合并充数。并发资格须另用供给充足的有用工作负载验证，P2在取得该证据前保持 active。

### P2 暴露的宿主证据接力遗漏

`TASK-positive_count` 首次仅提交测试后阻塞；预授权 Docker 已在 `evt-e3d3f7d67945` 产出明确 TODO/RED，下一 Impl 却再次报告缺少明细。既有接力索引在 operation 结算处停止，只覆盖 Provider 工具输出；宿主探针必须等 operation 结算后才执行，所以其输出总在截断之后。

新增 `task_host_evidence.capture_host_evidence` 由实际 `attach_task_handoff → capture_execution_evidence` 调用，把冻结交接前的同 run/task/operation/attempt/lease/dispatch/request/generation 宿主检查加入既有索引与受控分页读取。核对 kernel 起止事件、唯一完成、receipt/manifest/output 摘要与来源；保留命令、目标提交、退出码和失败原文，不产生业务通过或新执行权限。原生工具仍以结算为界，旧 handoff 重放保持冻结；新事实只进入新交接。

验证：`reports/host-evidence-handoff-20261118/`。
- 先验回归 2 failed / 10 passed，准确复现结算后输出遗漏；修复首轮18 passed。
- 调用方、宿主动作、发布与交接验证95 passed / 2 deselected；一次误填不存在测试路径的命令未执行测试，已修正，不算通过。
- 实际账本隔离回放暴露测试夹具把 operation_id 放入 manifest.identity、正式格式在 manifest 顶层的差异，已对齐；最终回放可读同一目标提交上的 TODO/RED 和 exit 1，原运行状态未改动。
- 最终 dev-verify 影响闭包176 passed（包括鉴权分页、实际dispatch、mock E2E），sentinel 53 passed。

此补丁尚未加载进冻结真实轮；确定性通过和隔离回放不等于新源码的完整无人真实资格。

### P2 暴露的并行诊断作用域误判

`TASK-reverse` 的驻留诊断 `evt-842c261510a9` 已匹配当前失败 operation/attempt，却因其他 Task 推进改变全 Run `progress_digest` 被挡下，消费记录停在 `consumed / controlled_action_status=blocked`。原账本只读回放确认，拒绝时唯一变化是该全局摘要。

诊断转 OA 仍走既有 Run Manager 受控入口，但前置条件由内核绑定到原 Task 的完整 operation/attempt/generation/准入结果以及同一 Goal objective。其他 Task 的正常推进不撤销诊断；原 Task 换代、结果变化、目标变化、Run 终结仍拒绝。暂停、人工决策、权限和预算门禁保留，其他 Run 级动作继续使用原前置条件。新范围不能授权业务结论或直接恢复执行。

验证记录在 `reports/task-scoped-diagnosis-20261118/`：RED 为4 failed / 9 passed，专项及直接调用方61 passed。扩大影响闭包5306 passed / 7 skipped / 3 failed；3项均为测试子进程 PATH 缺少 python，补齐已有虚拟环境 PATH 后定向重跑3 passed，无需修改生产代码或基线。sentinel 53 passed、流程 mock 10 passed。实际旧事件前缀只读回放由拒绝变为允许当前故障请求，原运行未改动。

真实轮另出现行为 RED 明细缺失导致独立 Verify 拒绝、OA 请求人工介入。该拒绝不作为需要绕过的门禁；下一冻结轮必须先证明测试入口能在实际沙箱输出失败断言和退出码。本轮实际 Provider 观测 Impl峰值8、TaskVerify峰值6、同时8+8时长0；不能以修复已提交替代真实无人资格。

本轮于2026-11-18 11:57 UTC以 `failed_qualification` 正常停止，停止前完成8/16项；父控制器、独立Run Manager及两个AR子实验均停止，私有tmux无残留。账本/任务候选全部保留，未覆盖判定或人工补采证。结束实验是操作员操作，与批准后的恢复介入0分开记录。下一轮前先对实际Provider测试入口做RED/GREEN预检。

### 发布队列的有限批量

32任务资格轮进一步观察到：已发布/待发布候选排队时，Impl槽位仍可能空闲；
`Run Manager → reconcile_task_publication` 每tick原来只执行一次commit或retry。
8个真实Git工作树的独立回归复现了一轮只能推进1项，不能用已配置8个worker推断发布吞吐。

`zf.yaml` 可显式设置 `runtime.task_publication.max_actions_per_tick: 8`，合法范围1–100，
默认1保留原节拍。每轮只遍历一次候选，每个operation至多执行一次原有发布或续跑动作；
Git效果仍逐个执行，继续使用既有受控入口、精确路径与Run总发布预算。
每项重新读取事件核对暂停、总预算、前置状态及回执，未知效果/活跃worker不被重放。
此项不调整宿主容器命令执行节拍，也不改变CAS集成或产生业务通过。

默认1的授权摘要省略新增字段，保持旧配置回执在代码升级后有效；显式批量纳入授权摘要。
批量应在新Run冻结前配置；带未结算旧授权效果的Run不能热改该字段并沿用旧回执。
当前冻结的32任务实验不加载这份补丁，批处理单测通过也不等于真实8×8资格通过。

验证与任务：[候选发布批处理](../../tasks/done/2026-11-18-1402-publication-batch-throughput.md)，
记录目录 `reports/publication-throughput-20261118/`。

批处理修复验证：8候选/真实Run Manager的RED复现4 failed、6 passed；专项60 passed。扩大闭包发现两项基线旧断言，已在未改动dev树复现并按现行Busy调用合同与既有token配置对齐，模块及caller70 passed。最终影响闭包8035 passed、10 skipped，哨兵53 passed、流程mock10 passed；尚不能替代新版本真实8×8。

隔离/门禁补充批次验证：专项60 passed；影响闭包4845 passed、7 skipped，串行4 passed，哨兵53 passed，流程mock10 passed；独立8×8 mock8 passed、真实tmux隔离1 passed。真实模型因额度门禁未重启，本批源代码修复通过不改变P2未验收状态。

最终合并候选验证（含Task Pipeline激活/投递补充）：专项64 passed；影响闭包5020 passed、8 skipped；串行4 passed；哨兵53 passed；流程mock10 passed；8×8 mock8 passed；host双socket隔离1 passed。证据：reports/external-gates-20261118/validation-summary.json；真实provider未重开，P2保持active。

## 恢复 Reader 的输入消费（2026-11-19）

宿主验证产生当前候选绑定的回执后，Reader 仍须独立判断；不能因为其沙箱无 Docker socket 就忽略宿主执行证据。新 canonical required-read 声明将 `critical-reader-repair-context` 排在产品大输入之前；既有已冻结声明及分页游标保持不变。

若当前 attempt 有该必读恢复输入，`verifier_execution_failure` 的只读预检和正式准入都会检查该输入的完整读取凭证。未读或部分读返回可纠正的 `recovery_input_unread`，不消费预检凭据，也不把命令成功判为产品通过。读取后仍可依据实际证据报告 runner 失败；真实 `reader_execution_failure` 仍可在产品输入未读完时报告。错误 attempt、摘要和损坏 ledger 不予接受。此检查仅证明输入生产的完整性，不证明模型理解。

运行时只维护输入/凭证边界，回执的适用性与产品判断方法在 `skills/zf-harness-verification-checklist/SKILL.md`。回归入口：`tests/test_verifier_environment_failure_reads.py`、`tests/test_reader_replan_feedback.py`。本修复不包含额度主动探测或 Docker 权限扩展。

### Critic 派发前的 Plan port 路径闭包

生产者本地文件可在正常 fanout 归档后成为 sidecar；原始输入的绝对路径不能直接当作
`plan_ports` 的已归档引用。`plan_synth_runtime` 在完成文件归档后，要求机械预检按
Package 的同一 sidecar 路径边界校验显式端口。绝对路径、非 sidecar 根、父目录越界或
逃逸符号链接返回 `plan_port_not_materialized`，包含具体端口与字段，并走既有协议修复
入口，不启动完整 Critic。inline body、已归档相对引用与正常生产者文件归档保留。

此检查不复制任意输入，不改摘要、不扩展安全根，也不代替内容/大小/摘要和独立语义审查。
生产者提交前使用的预检仍允许其本地文件，路径闭包在 Kernel 完成归档的实际调用点检查；
不能把生产者预检通过等同于最终 Package 已准入。回归见
`tests/test_prepared_plan_port_paths.py`（实际 Critic 派发入口及越界拒绝）。

### TaskVerify 的预授权宿主证据自动接续

混合 8×8 真实轮 `workflow-5d021bd50a5ec52b` 的只读审计任务因沙箱无法写入
构建目录而报告 `verifier_execution_failure`。其冻结合同将审计验收责任标为
`candidate_verify`，但宿主命令选择硬编码为 `task_verify`，导致已授权命令被误拒。
Task Pipeline 的 Verify 命令选择现在沿用冻结 snapshot 的验证责任；普通任务的
未来 Candidate 命令仍不可提前执行，调用者不能通过请求字段扩大授权。

真实 Run Manager tick 仍以原预授权 Docker 路径执行隔离快照。随后核对全部当前阶段
必需命令的完整回执、kernel 起止事件、唯一完成、策略/合同/源码/依赖摘要、输出摘要
和 operation/attempt/dispatch/lease 身份，才沿既有 Verify retry 入口请求下一代
独立验证。只重开执行失败且没有产品结论的 Verify，保留已通过的 Impl 和 TaskRef。
非零退出码也作为事实交给独立 Verify；容器成功本身不产生 task.done 或产品通过。

暂停、活动调用、目标/合同变化、其他 operation 已接管、缺少或损坏的回执均保留等待。
同一 operation 的回执不重复消费，宿主执行沿用原 Run 总预算；跨代反复失败并不能
仅凭此次自动接续宣称已自愈。接续后继续使用已有 handoff evidence 分页，不新增任务
状态或证据协议。既有 retry 原因前缀保留兼容；判断是否人工介入须看 kernel 事件的
actor/来源及完整原因，不能仅以历史 `operator_verify_execution_retry` 前缀计数。

回归入口 `tests/test_task_host_verification_handoff.py` 包含真实 Run Manager 调用、
隔离容器 mock、Verify 下一代调度、输出接力和拒绝反例。复现和验证记录位于
`reports/task-audit-host-recovery-20261119/`。源码通过不等于真实工作流通过；
在批准后的 Run 加载补丁必须记为人工维护，不能回填零介入资格。

### 无人资格的 TaskAttempt 策略

混合真实轮的 TOOL-LINES / TOOL-INITIALS 在没有有效结果的情况下租约到期，
按默认 `workflow.task_attempt.mode: shadow` 记为 `lease_expired_shadow_only`，
没有保留供同 operation 自动重派的 expired 状态。这是验收配置遗漏，不是额度故障；
启用 Run Manager、OA 和 Autoresearch 并不隐含启用所有执行策略。

8×8 生成器现在为新实验显式配置既有 `enforce` 模式，并用实际编译配置验证
租约过期进入 Run Manager 所有的绑定恢复路径。默认兼容策略、重试上限和安全边界
不变。启动前应核对实际加载配置及冻结摘要；旧实验保持原配置和失败证据，
不能把新实验策略回填到旧实验并声称完成了原资格。

### Task 审计不提前承担全产品报告

同轮 TOOL-NORMALIZE 的下一代 Verify 已读取成功的宿主回执，但其通过结果在预检
被误要求完整 `product_acceptance_report`。原判定以结果的 `verification_owner`
推断全产品职责，混淆了仅审计一个冻结 Task 的 `candidate_verify` 条款责任与最终
Candidate operation。不是缺少业务测试或模型额度，不能靠伪造全局报告放行。

共享 Product Acceptance 绑定器现在识别内核 operation 的三个同时成立的条件：
`task-stage`、`task_pipeline_stage=verify`、`output_profile_id=task-verify`。
这个 Task 范围仍经过原单Task条款、命令、目标快照、身份与准入校验；全产品门禁
继续由真正的 Candidate/Discovery 承担，未知或旧式调用仍保守保留原判断。
语义结果自行声明这些字段不能改变内核绑定的阶段或输出 profile。

回归 `tests/test_task_audit_acceptance_scope.py` 覆盖预检、正式准入和拒绝反例。
真实冻结 Task 的同一 scratch 在旧代码只读回放返回缺全局报告，修复后预检通过；
已停止的旧Run没有重新准入结果或补写通过。证据在
`reports/task-audit-acceptance-20261119/`，完整真实资格仍须新轮核验。

### 受控失败提交的字面量 stdin

真实轮 `workflow-863563136fc24d34` 的Mask和Average使用briefing生成的
`zf result validate --stdin --submit`报告执行失败。说明文字中的Markdown反引号
位于带引号的here-doc正文，原守卫却将其按活动shell替换扫描，误拒结果提交。
完成提醒不会改变同一解析行为；没有入账的失败结果也无法启动已有宿主证据接续。

共享shell分类器只识别单个受控`result validate --stdin`命令和完整带引号的
here-doc。正文作为CLI输入交给既有身份、权限、schema及一次性准入检查；这不授予
runtime目录写权限，也不把hook放行当作提交成功或产品通过。未引用delimiter、
头部替换/重定向/管道/分号、尾随命令、提前结束、其他程序等仍走原保守检查。

回归`tests/test_hook_literal_result_stdin.py`直接使用真实提示生成器和Codex/Claude
hook入口，覆盖合法正文及shell副作用反例。运行中的旧实验不因源码修复而变为
新源码验证；任何批准后的源码维护仍须明确计入人工介入。

### Task宿主动作绑定当前派发

同一operation在租约恢复或受控resume后可以有多个`task.pipeline.stage.dispatched`
历史记录。宿主动作和Verify证据接续按当前operation的dispatch、attempt、lease及
Task/Run/map/stage/generation识别唯一内核派发，不按整个历史记录数量，也不选择
账本中的第一条或最后一条。原有合同、目标、工作区、静默、审批和副作用幂等检查保留。
重复当前派发、缺失当前派发、身份冲突或非内核来源仍拒绝执行。

回归`tests/test_task_host_redrive_binding.py`覆盖实际RunManager tick→隔离命令→独立
Verify接续，并保留错误绑定不产生宿主效果的反例。真实轮维护前后证据分开记录。

### 区分Reader修复源与Task宿主事实

恢复不必然带有`critical-reader-repair-context`。失败提示只要求消费当前manifest
实际绑定的恢复源；Task宿主重试通过既有`task-handoff`携带证据。缺少未绑定的
可选恢复源不构成读取失败，实际绑定的源不可读仍须报告真实失败。

handoff中的`host_execution_evidence_reads`从已核验、已冻结的宿主证据索引生成，
精确选择前序operation及完成事件，避免把回执`source_event_id`的动作创建事件
误作可读取的完成事件。原索引、摘要、完整输出分页和CLI身份鉴权继续有效；没有
宿主事实则导航为空，不新增执行权或产品结论。历史handoff保持不可变。

回归`tests/test_task_execution_evidence.py`覆盖实际派发产生导航、受控读取非零
退出事实、拒绝创建事件及跨代读取；`tests/test_reader_repair_navigation.py`覆盖
提示与实际来源一致。真实Average重复输入失败的证据在
`reports/recovery-input-navigation-20261119/`，真实验收状态单独记录。

### Provider重启后的同attempt接续

Task Pipeline当前attempt已经sent而Provider进程退出时，重启并等待就绪后的
恢复briefing必须走既有continuation校验，不再次申请首次派发claim。读取当前
canonical attempt绑定的operation/dispatch/lease，保留原typed输入和输出合同；
不扩展租约、不生成新attempt，也不把进程启动或提示送达当作业务完成。

仅sent使用此路径；prepared/delivering保持首次投递语义。暂停、过期、Task/role
不匹配、operation已结算、预算限制及不在线的目标仍由原门禁拒绝。回归
`tests/test_pipeline_respawn_continuation.py`直接覆盖实际恢复入口，且保持TaskAttempt
未变。初始Provider退出原因及最终真实验收分别记录，不因此修复而假定全部通过。

### 环境失败报告的证据定位

只读Verifier可以在失败结果中引用当前实际工具调用/输出标识，附命令、退出码和
原始诊断；已有持久ref时优先引用。无需另写探针文件或从账本查event ID，不能
编造标识、复用其他attempt冒充本次输出。方法在Evaluator skill与派发提示一致。
这是明确既有failure admission能力，不放宽产品通过、身份、必读输入或权限门禁。
真实Reader反复因寻找额外持久引用而不提交的记录在
`reports/failure-evidence-guidance-20261119/`；真实CLI回归同时覆盖文件和原生工具输出
引用，均只准入failed/abstained，未执行的产品报告保持不存在。

### Reader分页续跑与TaskAttempt enforce

`reader_progress_continuation`在必读材料唯一覆盖增加后，使用原生session和原简报继续原sent TaskAttempt。它不调用故障重试分配器，不改变lease、dispatch、durable call request/attempt，也不因续读增加故障重试次数。真实K3暴露旧实现保留了逻辑WorkflowOperation attempt，但重新分配底层TaskAttempt，导致正常读取耗尽3/3预算；原shadow测试未覆盖该边界。

新的发送仍经过统一预算及Run门禁，再校验kernel claim、当前Reader/operation/fanout/dispatch、独立的transport TaskAttempt身份、未到期lease和活worker。发送成功才记dispatched；结果未知保留原claim，不盲目重送。重复页不产生新进展，真实失败仍走原有有界故障恢复。该路径不赋予Reader写权限，也不替代最终结果准入。回归入口见`tests/test_reader_attempt_continuity.py`和`tests/test_reader_progress_continuation.py`。
