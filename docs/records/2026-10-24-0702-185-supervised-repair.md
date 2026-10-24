> 状态: active

# 184/185 监工修复记录

关联[监工任务](../../tasks/active/2026-10-24-0702-185-supervised-repair.md)、
[设计185](../design/185-agent-routing-delegation-and-workflow-recovery.md)。
基线0ac28644，用户授权逐一修复1—9，并由当前session监督暂停、修复、断点继续。

## 签收边界与工作顺序

| 项 | 当前具体问题 | 下一验证 |
| --- | --- | --- |
| 1 | 最新PRD源码外部65项通过，CLI writer超2M而未准入结果 | 原Run保留blocked；预算恢复口径待Owner选择，不称为完整交付 |
| 2 | 旧候选reader取消修复尚无真实新Run验证 | 断点及迟到结果/旧Provider退出均取证 |
| 3 | Task Map后配置变更缺新代际受控链 | 实测复用/变化两分支，旧批准失效、新消费者生效 |
| 4 | reader/writer重复读、大量失败堆栈和历史会话放大输入 | 索引/方法及恢复计量已修，原预算内完整PRD仍未通过 |
| 5 | 通用源码single入口未接通；light原合同继承有缺陷 | light边界修复已验证；新入口及独立交付链仍待完成 |
| 6 | Codex原生助手身份/权限/子树取消未签收 | 当前adapter资格逐项验证，未支持能力保持拒绝 |
| 7 | 主机unshare拒绝；其他Codex正向能力已有证据 | 查实际隔离能力；Kimi不调用 |
| 8 | 已补Git快照、当前Attempt消费者和预算续算，完整接替仍缺 | 跨计量域归一化、权限/副作用胶囊及v3 fanout预算恢复待完成 |
| 9 | PRD未交付，新修复尚未真实重验 | 各分支独立记录pass/fail/blocked，不把mock当真实E2E |

## 监工协议

每条问题记录：发现时间、Run/Task/Operation/Attempt、源码/配置、复现证据、暂停动作、
现场与checkpoint、根因、修改、验证、恢复动作及结果。禁止直接改canonical状态。
当前run-pause只阻止后续派发，不能据此认定在途Provider已停；预算墙钟仍继续计算。
暂停需要另行通过现有checkpoint/停止机制核验进程已停；不重置token预算或伪造新Attempt。
需替换冻结运行源码时必须先停相关进程，记录新版本及兼容性；配置变更走受控批准。

已清理的第五轮没有完整Git现场，不能伪称原地恢复。新运行建立新的、明确记录的起点；
后续失败现场保留直到完成诊断和恢复。终态后才归档和清理。

## SUP-001 · Verify上下文放大（2026-10-24 07:02 UTC）

证据：第五轮`verify-lane-0`的01:59:01 Codex transcript（独立CLI Verify），
证据根`/home/min/workspace/public/zaofu-evidence/185-prd-planner-20261024T050500Z/live/prd/`。
实际出现：必读集合截断后重复输出大段正文；对artifact read正文误取`.content`字段；
hook查询包含查询自身的大输出；约1秒yield的反复wait。上下文从15,597输入token增长
至124,808，单操作累计2,066,868触发原上限。不是简单的“产品测试太慢”。
脱敏归档第207行不是有效JSON，分析明确排除，不能声称完整重建所有工具调用。
修复方向：增加可回溯的事件索引输出，保留按ID取完整正文；澄清artifact read输出形状；
对在途工具使用合理等待，减少不产生证据的模型轮询。不得用索引代替实际验收证据。

SUP-001实施：新增 `zf events --index`（可配合`--json`）；先筛选再取last，不改ledger，
按ID仍返回完整原事件。索引仅供发现，语义验收必须读原证据。补单项artifact read正文
与批读JSONL形状、适度等待方法，三份skill同步。2项RED、36项CLI/方法及20项
输出合同/工件读取调用者通过；Ruff/diff通过。dev-verify保守闭包106文件升级全仓，
本次只读CLI增量用上述直接闭包，自动宽deterministic/serial层显式跳过，非full pytest。

真实旧ledger确定性重放：同样5条hook记录，完整查询193,383 bytes、索引5,271 bytes，
ID集合完全一致，输出减少97.27%。这是定位开销证明，不代表整轮token降幅或新Provider
通过。自动run未执行被跳过的全仓层，直接测试36+20为本批证据（不相加为唯一测试数）。

SUP-001提交：`08b5d85f fix: bound event discovery output for verification agents`。

## SUP-002 · 恢复后预算基线被覆盖

复现：首次 Operation start 的 total_tokens=100；正常 graceful_stop 中断后通过
workflow resume 受控 redrive，新 Attempt 在 total_tokens=1800 启动。原 reducer
把基线改为1800，使之前1700消费从 Operation 计量中消失；续行 reservation 也会覆盖。
另外，两个不同 worker 的累计计量直接相减可能得到负数并截为零，伪装成无消费。

3个RED覆盖两条恢复路径及计量域变化（最初fixture用了不存在的 zf_stop 原因，
该次失败不算产品缺陷证据；修正为实际 graceful_stop 后重现）。修复保留首次启动
基线；显式不同instance域为 meter_unavailable，沿用 fail-closed。35项聚焦通过，
扩大闭包见下。没有提高上限，也没有豁免暂停墙钟；跨worker预算归一化仍待实现。

## SUP-003 · checkpoint 未保存实际工作现场

3个RED：只存 git diff 遗漏暂存/未跟踪文件；controlled maintenance 使用主项目而非
assigned worker 的配置工作区；秒级ID覆盖前次checkpoint。
修复用 WorkdirManager 与 canonical assignee 解析工作区，CLI及受控入口均接入；
错误owner或缺失工作区拒绝，不能退回主仓库。唯一ID；required digest sidecar 保存
HEAD、两套binary patch、未跟踪内容/模式/符号链接。原始数据2MiB、编码载荷3MiB限制；
捕获两次不一致、非Git、超限、子模块返回 unavailable，保留原工作区。

测试从快照在独立clone还原index、worktree及未跟踪二进制/执行位；验证自定义state
排除、不读取符号链接外部内容、同秒不覆盖、失败不冒充完整、CLI推导worker及Web
maintenance入口。checkpoint 不包含ignored文件/Git历史，provider_quiescence 明确
unverified；不是完整恢复胶囊，更不是旧Provider已终止的凭据。

闭包：106项 runtime/CLI/sidecar/budget/recovery及两个相关mock E2E通过；入口补测
10项通过（120 deselected）；sentinel 51项通过。自动增量验证48项通过，receipt另存证据目录；base dev累计闭包792文件触发全仓，
本批按新增测试及直接调用者缩小自动范围，其他闭包由上述直接命令覆盖，
flow smoke 10项、文档合同8项通过。不宣称full pytest或新的真实断点恢复已经通过。

## 新真实 PRD：保留现场与版本切换

证据根：`/home/min/workspace/public/zaofu-evidence/185-supervised-20261024T070200Z`。
工作区：`/tmp/zf-185-supervised-20261024T070200Z/prd`；冻结源码08b5d85f。
07:14 UTC起从Channel原始需求运行，需求摘要仍为3cd601932928aa294ba9cfde85ebaec9d51457f5827da6014a9b68278e393e62。
07:22独立PRD评审 `evt-ee8fa81f2d9b` PASS（28个产品要求、5个Owner决定），
canonical摘要f72fd9f6ef25c752dd98c9bfb865edba4b7def5b14555f5c384e5f5ec6a11aff。
产品尚未实施，未批准启动Workflow。为升级SUP-002/003，先执行本项目 zf stop，
18步完成、watcher退出；保留PRD/评审/完整Git与状态，待修复提交后冻结新版本续行。

SUP-002/003提交：`faf8cd5b fix: preserve supervised checkpoints and resumed operation budgets`。
已在无旧tmux/Provider/watcher的边界切换到该冻结提交，配置字节摘要保持216d309f…，
源码归档及切换身份记于`source-upgrade-1.json`，保留原08b5d85f归档。
07:33真实入口批准后产生 `TASK-5554BD / workflow-3ba5d2aa7ac4bb65`，
invoke接纳事件`evt-c2b4211340a4`；开始两路Scan。新PRD验收编号为AC-01至12，
原始完整需求正文摘要不变，独立28项语义检查通过；编号变化不代表缩小验收。

## SUP-004 · checkpoint 接入恢复消费者

发现保存checkpoint并不使它自动进入Run Manager上下文或worker恢复briefing。
补充生产者的canonical Run/Attempt身份，以及两条已有恢复消费者的同身份引用；
必须匹配Run、Task、Operation、Attempt、lease、dispatch、instance并验证sidecar摘要。
旧Run/旧dispatch、损坏或未捕获的最新快照均不暴露可用引用；不自动退回更旧快照。
正文仅保存引用和元数据，避免恢复提示重复塞入完整源码。依旧不授予续行权限。

5项RED验证缺失接线（修正测试fixture的TaskStore更新调用后重跑），27项聚焦与117项
恢复/briefing/long-horizon/sidecar/受控动作闭包通过；真实PRD仍冻结faf8cd5b，未热更新。
自动增量18项、sentinel 51项、flow smoke 10项、文档合同8项通过；与前述闭包有重叠，
不汇总为唯一测试数。没有声明full pytest或真实恢复已经通过。

## 第6/7项能力原因复核

本机Codex CLI仍为0.163.0。生成其experimental App Server协议schema，无模型调用：
存在`SubAgentSource.thread_spawn.parent_thread_id/depth`、协作调用的sender/receiver
thread IDs、`thread/tokenUsage/updated`及按thread/turn中断参数。因此不能写“Codex没有
子Agent”。ZaoFu现有adapter声明仍为native_multi_agent=false，尚未把这些字段接成
受控子树身份、结果收敛、预算去重和全树终止；生成schema不等于真实操作资格验证。

[官方子Agent权限说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)
明确继承父会话权限，custom instructions不能建立独立只读沙箱；
[App Server文档](https://learn.chatgpt.com/docs/app-server)说明逐thread用量通知。
由此，读写root下的“只读helper”不能仅靠prompt冒充隔离，须先接入受控外部只读worker
或在已隔离只读root下验证原生子树。当前不启用native delegation，不绕过现有准入。

主机复测`unshare --user --map-root-user true`失败于`/proc/self/uid_map: Operation not permitted`；
unprivileged_userns_clone=1、apparmor_restrict_unprivileged_userns=1。仅能确认当前权限
环境拒绝user namespace映射，不能仅凭这两个值断言唯一根因。源码补丁不能证明操作系统
隔离已恢复；不更改宿主安全策略，也不把danger-full-access当read-only通过。

## SUP-005 · checkpoint 标识路径越界

复现输入`A/../../escape`：sidecar最终拒绝路径，但此前旧diff已经写到
`state/autoresearch/maintenance/escape-<timestamp>-<uuid>.diff`，越过checkpoints目录。
反斜线输入在当前平台未被拒绝。2项RED；修复在任何状态读取/目录创建之前验证
task_id非空、UTF-8长度不超过128字节、无斜线/反斜线/NUL且不为点目录。
不能依赖最后的sidecar校验替代入口校验。
30项直接闭包、20项自动增量、sentinel 51项、flow smoke 10项通过；没有声明全仓测试。

namespace细分探针：单独`unshare --user true`成功；添加root UID映射失败，单独mount
namespace也失败。故限制应精确描述为UID映射/mount权限，而非笼统声称所有user namespace
都不可用。输出保存在`namespace-probes.json`，未改变宿主策略。

## 第3/5/8项仍待实施的准确边界

第3项不是“缺少难度打分”：`plan_execution_review.py`已将真实Task Map与策略摘要
提供给Agent。缺口在受控执行链：`control_actions_workflow_replay.py`禁止修改active
Request；`workflow_config_consumer.py`只校验新admission，不替换运行中的config；
`stage_replan_generation.py`生成语义重规划，不执行配置重载。需要独立的受控配置换代
事务串联暂停/旧执行权终止、精确候选批准、配置CAS、运行消费者重载、新代际绑定、
规划产物重验和原预算续算。不能只改lanes或重启后沿用旧批准；当前未实施完整事务。

第5项不是“headless开关”：`bounded_agent.py` v1明确只接reader和artifact_delivery；
`generic_workflow_catalog.py`登记的generic-delivery-v1目前没有template builder，
`generic_workflow.py`的software_delivery仍为shadow结果协议；目录仅暴露reader入口。
源码single须接canonical Task的精确scope/AC，单writer的Task/Operation/Attempt、候选
集成和独立Verify/Judge，并走blocking结果/交付门禁。现有light虽有确定性单任务映射，
真实dirty恢复也通过，但不能改名后宣称通用源码入口已经实现。

第8项本批补齐了可验证Git快照、当前Attempt消费者和同Operation预算续算；仍没有
跨计量域的预算归一化接替、完整权限批准与外部副作用回执胶囊。未知事实继续拒绝，
不能从transcript猜授权。第9项完整交付仍依赖当前PRD真实结果；所有未签收项保持active。

SUP-004提交：`ce9d0924 fix: bind maintenance evidence to current recovery attempts`。
SUP-005提交：`7e6a28a9 fix: reject unsafe maintenance checkpoint identifiers`。

## 新 PRD 计划批准与实施

07:43 Planner结果接纳`evt-0a9b51013c18`，07:48独立Critic通过
`evt-e46d64f606d8`。监工实际读取完整计划、六个必需端口、Task Map、Source Index
及Critic正文；精确核对正式package摘要dd0de82d…、计划修订8758d790…和24条
mandatory claim（12条目标、12条显式GOAL重复绑定），没有用摘要代替评审。
正式PAS相对Planner正文只增加5个确定性绑定字段，包括schema_version。
第一次批准脚本只预期4字段而停止于断言；修正测试驱动后才执行批准，不是产品门禁失败。
07:51批准`evt-30fa4982d97a`，任务映射准入`evt-1432ac377724`。

两个串行任务TASK-PRD-API、TASK-PRD-CLI保留原需求、五文件范围与全部负向用例，
一个writer和独立候选Verify/Discovery/Judge。07:57 API实现结果接纳
`evt-a5a8c9659563`，11项writer自测通过；CLI随后开始。自测不等于独立验收。
当前运行仍冻结faf8cd5b，无运行中配置/代码替换；新增修复等待边界使用。

## SUP-006 · 同域计量回退与采样上限漏拦截

4项RED：同一instance的累计token或entries低于Operation启动基线，原实现把负差值
截成零；仅配置max_usage_samples时meter_available=false也被放过。真实runtime
测试中模拟不完整cost投影重建，原预算门禁未阻断Provider。
修复把两类累计计数回退判为meter_unavailable，并将采样上限纳入失效计量拒绝。
不改Provider累计快照转delta语义，不从缺失投影猜用量，不提高预算；跨worker归一化
仍未实现。41项预算、恢复、价格精度、归档与instance聚焦通过。
自动增量18项、sentinel51项及相关flow smoke3项通过，Ruff/diff通过；非full pytest。
首个闭包命令误写一个不存在的测试路径而零执行，纠正后才得到41项通过结果。

SUP-006提交：`d4e4b544 fix: reject regressed and unavailable workflow usage meters`。

## SUP-007 · 新真实 CLI writer 超限，保留原断点

08:03预算事件`evt-cd8871e698e2`：Operation
`wop-prd-lanes-impl-queued-TASK-PRD-CLI-2-trig-evt-0-3b7a18d5c6d2`
累计2,140,249 token/13样本，超过原2M；Run blocked `evt-af2eaee43edb`。
API Operation此前已准入；CLI源码已提交e9e8ab0207d3f8d6618ea09892576fd5682f8e78，
但CLI结果尚未准入，没有candidate.ready/独立Verify/Judge/最终交付。

真实transcript定位：writer第一次必读批量输出44,089字符，因输出形状/截断又读
39,403及40,284字符；CLI复用同一会话后，重复失败subTest日志又输出41,209字符。
这是上下文和模型往返放大的具体证据，不能仅归因于业务代码复杂；未知实际费用不报零。
补writer方法：一次读取、在工具内解析JSONL后按未读正文分批呈现、仅补截断缺失行；
完整测试日志保留，按不同失败原因查阅，避免重复堆栈进入上下文。6项方法/分发检查通过。
这仍需要真实后续验证，不能据方法修订保证2M内完成。

08:05受控CLI checkpoint
`ckpt-TASK-PRD-CLI-20261024T080504-2c901b948a71` 保存实际writer HEAD/工作区；
快照摘要62c2ef241947f26432efbf68c76c0b34871e87912ab8320a96a81228912b2c72。
随后 `zf stop` 18步完成，独立进程检查确认本模拟watcher/Provider退出；保留完整Git、
原工作区、state与ledger，没有simulation.done，没有删除可恢复现场。
冻结faf8cd5b的checkpoint尚不包含SUP-004新增Attempt绑定，不能伪称已实测后续代码。

暂停源码的独立detached worktree执行原始外部oracle：65项通过，五文件范围符合。
人工读取业务函数/CLI/README确认标准库、只读输入及错误说明；这只是暂停源码验收，
证据目录`live/prd/paused-source-oracle`，不称为候选或Kernel交付成功。
恢复投影实测为0个operation checkpoint（`blocked-resume-projection.json`）。
根因：既有预算恢复只覆盖Task Pipeline；当前v3 fanout writer未接对应受控恢复。
原Operation预算已耗尽，也不能通过刷新session/Attempt清零。保持原预算及blocked事实。

## SUP-008 · 恢复后第二次超限未重新阻断

检查恢复链复现1项端到端runtime RED：Task Pipeline首次超限后，旧Run预算修订允许
redrive；但Operation本身冻结上限未变。新执行超过同一上限时，旧预算事件被全生命周期
去重，未停止Provider。历史run.goal.blocked也让后续Run阻断事件被忽略。
修复预算事件按Operation受控redrive计数分代去重，Run终态判断复用当前admission投影；
重试仍继承原消费/开始时间，不把Run预算修订当作Operation上限变更。
30项预算/受控恢复聚焦通过。当前真实PRD继续保持停止，未应用该修复到现场。
SUP-008追加：Run admission/Attempt终态/指令及flow smoke共57项、自动增量19项、
sentinel51项通过，Ruff/diff通过；与聚焦组有重叠，不汇总成唯一测试数，非full pytest。

SUP-007方法与SUP-008提交：`f796a2a8 fix: rearm budget guards after controlled operation recovery`。
暂停快照独立进程观察：对应tmux session不存在，/proc中可读取的工作目录没有落在
本模拟根的进程；详细范围见`paused-process-observation.json`。Run归属计量5,712,647
（按当前已准入cost delta及事件归属），不把隔离项目入口用量混成Run用量。
已询问Owner是否允许单独的3M Operation恢复实验；未收到批准，不改原2M/12M预算。
即使后续预算实验成功，也不把当前原预算失败改为PASS。

## SUP-009 · light入口丢失canonical Task边界

3项RED：原Task明确两个文件，合成任务却采用默认app/**加README.md；结构化AC/ID
被泛化文本替换，验证命令ID与AC链接丢失；过期root contract digest仍生成task_map。
修复继承canonical scope、exclusions、non-goals、AC和goal IDs，复用命令规范化保留
ID/owner/tier/timeout/证据绑定，保留额外配置检查；绑定过期在物化前发出明确blocked。
没有已跟踪Task的legacy入口沿用既有合同，不在此切片宣称新source-single已完成。
79项light/输入继承/writer准入/相关policy mock通过；实际Orchestrator入口到writer
合同有测试。追加legacy字符串命令/额外检查去重，自动增量30项、sentinel51项通过。
首个闭包命令误写不存在的验证命令测试路径，零测试执行；纠正后才执行上述闭包。
SUP-009最终闭包：`tests/test_light_flow_contract.py tests/test_task_map.py
 tests/test_writer_fanout_runtime.py` 224项通过（311.56秒）；文档指令8项通过，
Ruff/diff通过。与前述79项有重叠，不累计为唯一测试数；不是full pytest或真实Provider重验。

SUP-009提交：`e3cb4c8f fix: preserve canonical task boundaries in light delivery`。

## SUP-010 · Owner批准CLI writer取消token上限，原Run续行验证

Owner明确授权“CLI writer 可以取消 2M 上限，token 限制，暂时不设置”。
只给dev-lane-0增加cli-writer-recovery-v1，token_budget=0；保留Operation的
1200秒/60样本/10美元、Run的12M/7200秒/30美元，其他reader仍为2M。
原blocked Operation不改写、不清零；复用既有repair.action.requested的
rerun_fanout_child，由Kernel创建有parent_operation_id的新writer Operation。
其运行仍属于原Run，Run累计用量和时间不清零，API已准入结果和CLI已有提交保留。

恢复前聚焦验证：`tests/test_repair_action_executor.py tests/test_workflow_budget_guard.py`
加writer retry的fresh blocking Operation、dependency base两个案例，32项通过（20.71秒）。
这不是full pytest，也不替代后续真实Provider闭环。已停止原watcher/Provider，
源码从faf8cd5b冻结升级至e3cb4c8f，归档保留两版；停止状态下按Owner授权原子替换
zf.yaml，校验原字节摘要，未直接修改TaskStore或ledger文件。配置候选逐角色比较，
只有writer execution profile改变；首次候选漏写backend导致其默认为python，
在应用前断言拦截，补codex后通过。恢复脚本首次误以worker目录为Git根，
在变更前HEAD断言拦截，改为checkpoint记录的project子目录后继续。

运行证据：`185-supervised-20261024T070200Z/live/prd/recovery-*`，
Owner授权、配置前后全文/摘要、源码版本、恢复intent与watcher日志分开保留。
此轮按新预算策略验证，不能把原2M失败改记为通过；未通过项目继续记录。

09:02:31真实repair准入`evt-77d1ab48befd`；新Operation
`wop-prd-lanes-impl-queued-TASK-PRD-CLI-2-trig-evt-0-7bfbf3a0e17e`
于`evt-26722dcb830e`开始，父Operation仍blocked；dispatch为原dispatch加-retry-1。
request不可变sidecar714b78f2606c0da59ed6bd7d55ae59ad6befcfa7b0a535f7943343bceab12076
验证profile=cli-writer-recovery-v1、token_budget=0、其他限制不变。
HEAD仍e9e8ab0207d3f8d6618ea09892576fd5682f8e78；API未重跑。
更正SUP-007入口结论：自动resume projection不覆盖v3预算blocked，
但既有受控repair重试入口已支持此次恢复，因此本步骤无需新增第二套恢复状态机。
产品demo合同漂移预检为WARN而非STOP，按Owner批准的精确配置变更继续；
不是严格合同CAS在线换代已实现的证明。

### SUP-010a · 恢复会话沿用旧scratch，守卫正确拒绝

09:05:19 `evt-3c8825c1610c`拒绝writer写旧Operation 3b7a18d5c6d2的scratch。
当前Kernel生成briefing第118行和提交命令均指向新7bfbf3a0e17e/retry-1，
因此不是新scratch绑定错误。writer根据旧会话记忆误报runtime缺陷并停止；
监工通过同一Codex tmux输入提醒重新读取当前briefing、逐项核对执行身份和路径，
不创建额外dispatch/Attempt，不放宽守卫、不代写结果。完整提示保存
`recovery-operator-correction.json`。这是有人介入恢复，不能记作全自动恢复成功。
修订writer方法并同步三份skill：恢复时先重读本次briefing；代码证据可沿用，
旧身份/旧scratch不可沿用；拒绝后先核对实际路径，不根据旧上下文认定runtime错误。
当前运行仍冻结e3cb4c8f，方法文件未热替换，现场改进由明确记录的监工提醒完成。

方法修订验证：13项方法/指令检查通过；针对本切片显式paths执行
`dev-verify.py plan/run --base dev --tests tests/test_plan_readiness_skills.py`，
自动45项通过、1项跳过（14.52秒），三份skill字节相同，git diff --check通过。
额外15项指令/Attempt终态检查通过，含旧终态不得关闭后续redrive Attempt。
上述集合有重叠，不累计唯一总数；非全量验证。无paths的整个184对dev planner
覆盖历史多批次并选794个测试文件、退出2，未作为通过证据，也未因此触发full pytest。

SUP-010a方法修订提交：`d160bdd7 fix: revalidate writer identity before supervised retry submission`。
09:09:25新CLI结果`evt-4c93ed7d3573`准入，dev.build.done=`evt-82b412858ee0`，
TaskRef e9e8ab0保留，child.completed=`evt-2dcbc9ba03e7`；恢复Attempt成功终局。

## SUP-011 · 预算恢复后writer汇总保留cancelled导致死端

真实API/CLI两child均completed，但manifest.aggregate仍为cancelled，原因
workflow_run_terminal:run.goal.blocked。`_evaluate_writer_fanout`把历史cancelled
当不可恢复终态；已有恢复判定只允许timeout，因此没有candidate.ready。
09:11后再次zf stop（18步）并保留所有现场；原Run仍running，不清零预算。
新增实际Orchestrator入口回归：Owner重新active后所有writer完成仍无候选，
1 RED/2原有负例通过。修复仅允许“Run blocked导致的cancelled且全部child完成”
进入既有候选集成；调用前仍校验当前Run终态和fanout代际，普通取消不能恢复。
同时覆盖运行中完成、重启/idle收尾、未重新active、Owner取消和重复tick幂等；
不得重派已完成writer，不直接改manifest或伪造candidate.ready。

SUP-011验证：新增6分支与repair/budget/Run admission闭包75项通过（65.44秒）；
完整writer runtime模块158项通过（4进程，94.03秒），sentinel51项、flow mock10项、
指令8项通过，Ruff/diff通过。自动选择器显式传本切片source/test/doc并记录receipt，
宽deterministic/serial层由上述已执行闭包替代，自动contract/mock层单独核对。
这不是全仓pytest，不改变真实运行的预算和验收口径。

SUP-011实现提交：`1b2b19e0 fix: resume writer aggregation after a blocked run is reactivated`。
自动验证receipt确认选172个受影响测试文件，按前述声明跳过宽deterministic/serial，
实际自动sentinel51项（32.89秒）与flow smoke10项（20.49秒）通过；不是全量通过。
停止状态升级冻结源码e3cb4c8f→1b2b19e0，预算配置摘要不变，原Run不变，两个
child保持completed。09:16:43 Kernel生成candidate.ready `evt-06bcd430b1b6`，
候选99b0ed09447eba3d95ea8cb1ce9a6cce0b0c9471；没有重复writer派发。
候选独立外部oracle65项通过，五产品文件与暂停源码逐字节相同。人工读取API/CLI/
README确认标准库、纯整数校验、只读输入、错误和运行说明，证据分别保留。
09:16:59独立Verify启动`evt-ef1d63d71b21`；最终交付尚待后续证据。
当前Run原配置12M总token仍在，已向Owner澄清“暂不设置token限制”是否也覆盖
Run总上限；答复前保持，不能将CLI writer的授权自动扩大到其他限制。

恢复writer实际增量2,891,985 token/14计量样本，超过旧2M仍于09:09准入；
`recovery-writer-usage.json`记录原baseline和当前role counter，包含同Attempt的
监工纠正。未知价格不能据total_usd=0报告免费。
09:26:56第一项独立Verify结果准入`evt-778354b54ea5`，
verify.child.completed=`evt-ea9434757d94`，目标99b0ed0；四条命令、19测试与独立
边界检查通过，无blocking finding。该结果不等于CLI第二项Verify或最终Judge通过。
09:27:39 reader_root_context_changed实际换会话`evt-4f0f53b27ba3`，
09:27:43 CLI Verify新Operation 71ed6a94b426启动`evt-ce654083c7e7`。
另向Owner澄清原7200秒是否延长14400秒（计入停机修复，约09:33 UTC到期）；
答复前不变更。第一项Verify通过不能提前标整个任务或1—9完成。

## SUP-012 · goal CLI激活Run后根任务仍blocked

真实Run已running并执行Verify，但根TASK-5554BD仍blocked。根因：goal CLI只写
run.goal.updated，已有reopen_workflow_managed_task_from_run_recovery只接在
stage-replan受控动作中，goal CLI未调用。修复CLI先验证当前Run admission为active，
再通过既有EventWriter/TaskStore生命周期服务同步根任务并清除阻断原因；不直改现场。
验证blocked恢复、重复调用幂等、completed/cancelled/failed不被重新打开。
初始测试fixture漏写canonical parent anchor，补workflow.invoke.accepted后校验；
尝试跨冻结源码运行被仓库imports-from-this-repo守卫正确拒绝（7 setup errors），
没有弱化守卫。随后在本worktree短暂加载原HEAD CLI、finally精确恢复当前文件，
得到真实1 RED/6 PASS；新实现13项CLI/生命周期聚焦通过，最终恢复/Run/受控动作
闭包69项通过（39.65秒）、sentinel51项、flow mock10项通过，Ruff/diff通过。
自动selector选择106文件而升级full suite；发现意外启动后只终止自有selector/pytest
进程树，未把中断的宽套件算作通过。验证结论仅为上述闭包，不称全量验证。
此修复尚未加载到真实冻结Run，需下一次获准恢复时验证根任务投影。

## 本轮暂停结论 · 原Run总时限到期

09:33:15预算事件`evt-b8946c13013d`，elapsed=7202.156秒；超限维度仅wall_clock，
当时计量11,914,542/12,000,000 token，113条未知价格记录（金额未知，不能称零成本）。
09:33:17 Run blocked=`evt-e3f4a368f7d1`，第二项CLI Verify被取消，未准入最终结果。
已执行zf stop，18步完成；保留原Git/state/workdirs/TaskRefs/候选/两个Verify的
transcript与scratch，没有simulation.done，没有伪造Judge或最终交付。
完整恢复描述见`live/prd/run-timeout-checkpoint.json`。
本轮CLI writer取消2M上限已经实际证明有效，恢复后用量2.89M且准入；候选外部
65项及第一项独立Verify通过。剩余CLI Verify、Discovery、Judge、Kernel交付，
以及SUP-012真实根任务同步，仍未完成。Owner关于Run token/总时限的澄清尚待答复。

收尾验证：针对CLI/lifecycle/本次文档的显式自动闭包21项通过（15.15秒）；
69项恢复闭包、51 sentinel、10 mock记录已归档，集合有重叠，不累计唯一总数。
Docker Playwright真实看板断言根任务blocked及wall_clock原因、零pageerror通过，
没有mock路由、没有mutating browser action。证据`browser-live/blocked`；
它不是done看板/最终交付证明。容器首轮遗漏entrypoint、第二轮输出目录权限、
第三轮自定义UID无可写浏览器HOME导致crashpad失败，纠正为容器root后通过；
未安装宿主浏览器、未改变宿主安全设置，失败命令不算产品验收失败。
自有API/UI进程组已停，tmux不存在；可读取/proc cwd扫描没有本模拟根下的进程。
所有恢复必需工作区和Git/state保留；等待Owner答复Run token与总时限范围。

SUP-012实现提交：`c831a904 fix: reopen the parent task on authorized goal recovery`。
本轮实现链d160bdd7→1b2b19e0→c831a904，仅本地提交，未push/merge；
真实冻结运行最后使用1b2b19e0，c831a904尚待下一次真实恢复加载验证。
整体1—9任务继续active，不归档，不将候选/局部Verify通过写成完整交付。

## SUP-013 · Long horizon 总时限与 reader 受控恢复（实施中）

Owner批准不再延长为4h，而取消Run总时限；本轮Run token上限也取消，计量继续。
公共prod/refactor profile仅修改Run timeout=0，Operation timeout与公共token/cost保留。
当前真实Run仍暂停，准备通过goal CLI预算修订恢复，不能仅改YAML。

复现reader两处断点：受控重试仍引用已cancelled Operation；Run恢复后reader aggregate
仍因旧cancelled状态不能收尾。修复通过已有repair入口新建关联旧调用的reader Operation，
释放中断dispatch；保留已完成reader、原candidate与契约。汇总live/idle路径只恢复
Run blocked造成的取消，仍blocked/人工cancel被挡住；失败子项继续失败/返工。

验证：58项配置/预算/goal/repair通过；195项reader/结果准入/Attempt闭包通过。
扩展live/idle与失败分支后170项通过、1项测试断言误将后续合法返工派发算作原fanout重跑；
改为按原fanout计数后13项回归通过。5h/72h使用受控时钟测试，非真实等待72小时。
planner保守选出176文件并建议全量；依仓库验证规则以显式影响闭包替代全量，
自动contract/mock层仍执行，receipt留在外部证据。原PRD最终交付尚待真实续行。

SUP-013提交前最终验证：旧reader迟到结果回归1项通过；51项sentinel和10项flow mock通过。
新增恢复模块通过Ruff与diff检查；直接调用者reader/fanout恢复闭包已覆盖。真实续行待执行。

## SUP-014 · 真实reader恢复的输入引用遗漏

6513375d实际恢复原Run成功：evt-c7c87dc36488将Run timeout/token改0，根任务回in_progress；
repair evt-1fcdd21ccf18创建第二Verify新Operation afaddf2a72b4，旧71ed6a94b426保持cancelled，
第一Verify不重跑。10:06启动后，agent受控读取发现impl-self-check source not found。
证据文件仍在且SHA一致；重试清理误用了writer输出清单，删除了reader消费的自检输入引用。
这不是产品缺陷，也未通过降低读取门禁绕过。session停止18步骤，现场和未准入报告保留。

新增受控artifact读取回归，先准确复现source not found；修复reader重试保留writer自检和
固定target引用。新Operation仍重建call身份、Attempt、scratch及读取清单。此前58/195等
测试缺少跨writer-reader输入回归，不能当作完整真实成功；本次补上该边界。

SUP-014修复验证：181项reader/受控输入读取闭包通过，51项sentinel通过，10项flow mock通过；
Ruff/diff通过。初次新增测试缺少sidecar created_by参数已修正，随后才复现真实source缺失。

## SUP-015 · 释放dispatch后reader上下文切换仍被阻断

4e60ec17启动后，第二次repair被evt-3786c50cba63拒绝，原因
reader_context_switch_waiting_for_settlement:fanout_child_active,task_active。
没有派出第二个新Operation，原Verify仍suspended；第一Verify保持completed。
已停止现场。根因是_active_fanout_child_for_instance忽略fanout.child.dispatch_lost，
而同模块task状态查询已经处理该事件，形成不一致。

修复只按fanout_id/child_id/run_id三元组释放丢失的dispatch，缺失run_id不释放，
旧dispatch迟到的loss不释放新dispatch。新增测试1 RED/12 PASS复现，再执行影响闭包。
同时把长时测试的局部时钟基准改为真实operation.started时间，避免测试初始化耗时造成抖动。

SUP-015验证：75项lifecycle/repair/reader/长时闭包、51项sentinel、10项flow mock通过，Ruff/diff通过。

## SUP-016 · 重启把workflow根任务重新排入backlog

Docker真实看板检查期望根任务in_progress，但页面ready；进一步检查并非前端投影缺陷：
10:04 goal CLI确实把根任务恢复in_progress，10:05启动的task.requeued evt-ac2e6f2da659
又把它改为backlog（source=zf_start_boot_reconcile）。Run及Verify仍running。
当前调用链为zf start -> requeue_stale_inflight_tasks；它误把持久Run根任务当成失去worker
session的普通任务。GracefulShutdown复用同一函数，也存在此问题。

新增真实CLI启动helper/Shutdown cleanup四个分支测试（managed parent/legacy anchor），
全部先RED；修复按既有execution owner/anchor身份排除Run根任务，普通中断worker仍requeue。
当前现场无需打断Verify，通过已有goal set --status active纠正根任务状态；
运行源码仍0fe2af8f，新的boot/stop修复真实启动尚未使用，不把它宣称为已真实重启验证。

SUP-015真实恢复已成功：evt-483356cc445e应用repair，新Verify Operation25ad09c75126，
15个required source引用/摘要完整，第一Verify仅原来1次dispatch。独立agent随后确认
11业务测试、8CLI测试、19 discovery测试、compile和100位整数/输入不变/错误输出探针通过；
这些是进行中的provider证据，尚需结果准入及Judge/Kernel交付。

SUP-016验证：84项boot/stop/goal/根任务生命周期闭包、51项sentinel、10项flow mock通过。
受控root修复事件evt-4a81485100e7未重启任何child；原Run继续。第二Verify准入
evt-32f22f70d5c6，aggregate evt-cd1cf7374456 completed；Discovery实际启动evt-422e80030de1。


## SUP-017 · Discovery真实缺陷与返工需求引用丢失

两个Verify已准入，Discovery在原candidate99b0ed0独立发现AC-05缺陷：合法4300位整数
加1后输出4301位整数，CLI的json.dumps越过Python默认转换限制，抛traceback而非返回JSON。
flow.discovery.failed evt-6fb9b02fe210生成单项TASK-GAP-CLI-INTEGER-OUTPUT，仅允许改
ledger_cli.py/test_ledger_cli.py，保持原12条AC；这是有效产品缺陷，不降低要求。

返工TaskMap evt-43cf304aa685产生generation8579ed56，但PlanPackage准入
被evt-7ed8b5a968b4拒绝：12条OBJECTIVE声明不连续。根因是Verify结果仍带需求快照，
build_flow_discovery_context未传requirement_spec_ref/digest；新代计划正确丢弃旧代claim
引用，却无法从原需求重建声明。保留连续性门禁，仅补这两个字段。

新增payload/fallback及真实Orchestrator桥接回归先3 RED；另一个gap测试最初用了不存在的
需求引用，正确导致writer未派发，修正为有摘要的真实fixture后验证。覆盖需求引用到
Discovery，再经gap bridge到新TaskMap；不复制旧代PlanPackage/claim-set替代重新准入。

真实现场已停止，原Run、通过的Verify、candidate、amended TaskMap和所有历史失败保留。
计划冻结升级后经zf emit重新发布同一amended TaskMap，仅补回原Request需求快照引用，
由正常PlanPackage/approval/claim连续性门禁重新准入。这是监工修复，不宣称全自动恢复。

SUP-017验证：88项Discovery/gap/GoalClaimSet/PlanPackage闭包、51项sentinel、3项flow smoke mock通过；Ruff/diff通过。未运行全量pytest，真实续行待验证。


## SUP-018 · Gap返工继承的Product Acceptance绑定旧版本

7c35ecb8冻结恢复后，原需求引用/声明连续性已通过，evt-df88a126edef在下一道门禁拒绝：
继承的product_acceptance_spec仍绑定8758d790，而返工generation是8579ed56。
evt-2b69987ccde0因重规划预算耗尽升级，Run evt-d1cba22112d6 blocked；已正常停止。
这不是总时限/总token阻断。8b8cdd58根任务启动修复已真实加载，启动未再requeue根Task。

历史补充：此前10:34还出现一次旧generation Discovery自动重试evt-d5cfa88d5b2c，
10:38停止后本次启动自动redrive，未成为新候选交付证据。保留其事件，不宣称只运行过一次。

修复限定显式gap_tasks_only返工，验证amend_of内容摘要和supersedes generation对应
同Run/flow的原PlanPackage，校验旧Product Acceptance绑定，然后以相同语义体生成
新版本不可变spec。记录rebound_from_ref/digest和原Package来源；旧spec/report不改。
普通重规划、错误generation、不同base内容不自动重绑，仍拒绝。正常Plan审批和结果
版本校验保留。新增准入入口回归先1 RED/3 PASS复现。初次实现严格比较ref字符串，
fixture的输入ref被正常端口物化成另一ref，导致合法继承被拒；改为固定来源摘要校验。

SUP-018验证：扩展闭包73项通过、1项合法ref别名回归失败后修复；最终28项PlanPackage/ProductAcceptance闭包通过，含5项新增返工测试。51项sentinel、3项flow smoke mock通过；最终源码再执行sentinel，未运行全量pytest。

真实不可变输入的隔离准入回放通过：24条必需声明全部保留、无waiver，Product Acceptance重绑新generation且语义不变。初次隔离fixture遗漏RunContract快照，补齐后通过；未修改真实state。最终sentinel结果单独归档。


## SUP-019 · CLI审批展示错误地检查历史任务

6be5b399实际恢复成功：evt-e60a55a8e689准入新PlanPackage，evt-7caebdcae46b请求审批，
仅1个gap task。CLI plan review却重读完整amended TaskMap，误报旧CLI与gap任务路径重叠；
Kernel生成的digest按实际派发集检查，全部绿。监工核对digest、原需求、两文件修复边界后，
按Owner授权批准evt-3df2b7f0ee9b；evt-9c62cbfcbcd3已启动真实Codex gap writer。
完整评审差异保留在acceptance-revision-operator-review.json，不把误报隐去。

修复CLI优先显示Kernel产出的共享digest；老事件没有digest时保留原检查。
无法读digest时显式告警后降级展示，不静默装作成功。只读CLI，不改审批/派发机制。
回归1 RED/4 PASS复现；第一次闭包命令误指不存在的test_plan_preview_artifacts.py，
没有执行测试，已改用真实plan_digest/operator_plan_preview测试，不计入通过。

SUP-018补充直接writer admission闭包40项通过，真实准入/审批/派发也已证明接通；
当前尚待writer结果、后续Verify/Discovery/Judge与Kernel最终交付。

SUP-019验证：29项CLI/digest/operator preview只读闭包通过，Ruff/diff通过；无需为只读展示修复中断真实writer，冻结执行继续使用6be5b399。
