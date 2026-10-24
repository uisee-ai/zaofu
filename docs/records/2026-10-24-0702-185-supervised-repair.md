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
