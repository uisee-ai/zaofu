# 自动源码修复闭环与无监工资格验证

> 状态: active

Owner 已批准按下一步执行：现有真实 Run 继续独立验收，同时核验并补齐源码修复链；条件满足后再开始新的零人工干预窗口。基线 a93fc04f，沿用隔离工作区。当前 120 次宿主采证授权保持，不合 dev、不 push。

| ID | 工作与验收 | 当前状态 |
|---|---|---|
| SR1 | 核对 RM 诊断、修复合同、隔离工作区、发布与独立验证实际调用链 -> verify: 覆盖真实入口的确定性测试，未知原因、越权、失效绑定保持拒绝。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR2 | 故障注入应用与重启边界，修复已复现问题 -> verify: 缺少重启前提不改源码；失败不假报已恢复；正确版本重绑后才恢复原检查点。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR3 | 配置受限的自动修复实验并验证 -> verify: 路径范围、合同/验证证据、授权和检查点均有效；模拟通过后才部署真实实验；完整保留失败与人工介入记录。 | 原生候选已验证、应用、重启；恢复Plan需owner审批，已执行并进入新一轮Plan返工；五步无人闭环未通过 |
| LIVE | 既有 Run Reader → Judge → Goal → 本地交付 -> verify: 每阶段正式结果、同候选/目标/合同绑定，不将代码测试冒充真实交付。 | 16/16业务完成保持；11-22最终Judge正式rejected：19项产品claim关闭，但历史完整实验claim未满足。旧Run受控暂停保全失败，Goal/本地交付未通过；NEW准备中 |
| SR-WATCHDOG-WAKE | 静止后唤醒的正常刷新窗口不误报投影陈旧 -> verify: 刚唤醒及600秒边界不报警，601秒仍未刷新继续报警；覆盖实际watchdog与调用方。 | 当前HEAD先RED复现；移植原生候选最小修复并补600/601秒边界，直接8、自动闭包256/7skip、sentinel53、flow10通过；cfa4c855已隔离部署，旧候选队列尚未收尾 |
| SR-WAKE | 源码修复步骤终态及时驱动下一步 -> verify: 真实tick入口在Supervisor周期前运行一次，普通bookkeeping不形成循环。 | 已验证并随f2e927da部署；236项直接闭包、6149项自动闭包、sentinel53、flow10通过 |
| SR-STRICT | 严格修复队列不能由普通进展假结算 -> verify: human/business/Run完成不能消除严格pending，legacy行为保持。 | 严格计数及显式disposition入口已验证部署；受控处理仅关闭有有效替代的旧候选 |
| SR-DISPOSITION | 明确关闭已被有效替代的失败候选 -> verify: 真实ControlledActionService/RM入口，旧失败保留、原案卷不变，陌生/过期/未应用替代与门禁拒绝。 | c7e403ac已验证部署；341直接闭包、7143自动闭包通过，真实受控disposition保留旧失败 |
| SR-POLICY | 投影不把路径风险建议冒充审批门 -> verify: medium未知审批需求，实际受控apply策略保持。 | c7e403ac已验证部署，实际gate未改 |
| SR-BOOT | 区分历史重启证明与当前版本可恢复性 -> verify: 后续升级不把已证明重启误判未知，失效/缺失/merge前证据不采信，当前不匹配仍不自动resume。 | c7e403ac已验证部署；原生源码修复有匹配boot证据，当前不匹配仍拒绝resume |
| PLAN-SOURCE | Plan/返工接收明确声明的原始正文，受控恢复可补充已冻结的证据 -> verify: Planner/Critic实际handoff与必读声明包含正文；摘要/缺文拒绝、原案卷不改、普通中间产物不全量升级为必读。 | ae125f6d已部署；真实Planner必读包含两份原文；20:02新代际纠正操作员附加约束，正式Plan仍待审核 |
| SR-ENV | Worker 与独立验证进程不继承运行项目定位和控制面环境 -> verify: 实际子进程在隔离工作区执行，真实项目及 workspace registry 不被测试指向，旧失败保留。 | 1d1ba2d7/1a9e8667 已部署，1852项影响闭包通过；可信检查点恢复，17:15重新派发原Plan |
| SR-PROCESS | 源码修复终态与子进程退出一致 -> verify: provider 结束/超时后测试后代不会继续写入；状态不确定保留诊断。 | 24420dc0已验证部署，真实入口/独立review与最终34项边界通过；全资格故障实验仍待完成 |
| RESUME-TERMINAL | 审批旧Plan不能重开已完成任务或把恢复冲突当规划缺陷 -> verify: 旧Run真实重放、Task合同/完成证据保持，准确受控恢复继续Judge/Goal，不重跑已完成产品任务。 | 6b9d72e3防复发及14项受控补偿已部署执行，22e6e1f0补齐Goal证明消费；原Judge正式结果仍待收取 |
| READER-TERMINAL | 协议耗尽有明确fanout终态与合法恢复入口 -> verify: 当前精确失败投影、崩溃重放、暂停门禁及受控重试，不制造语义结论。 | 7becfc7b已部署；2380项自动闭包、sentinel53、flow10通过，真实历史前缀只读匹配通过；OLD正常child-rebuild已派发 |
| QUAL-TOOLS | 真实并发采证及预声明故障工具 -> verify: 原生时间/turn绑定、准确审批、项目暂停、当前派发、源码加载及共享选段拒绝，外部工具不代做恢复。 | 实验目录6份工具已冻结，25项检查及独立静态review通过；真实旧Task Verify字段兼容检查通过，故障尚未注入 |
| QUAL | 新窗口验证 Worker 失败、控制器重启与 Reader 缺证恢复 -> verify: 同 Run 真实 8 Impl + 8 Task Verify 重叠，16 项独立验收、Candidate、Docker 浏览器、Judge、Goal、本地交付完整，零人工恢复。 | 前置条件未满足，不宣称通过 |

当前 Run 已有人工修复/部署，原无监工资格失败永久保留。新配置不允许 Agent 扩大授权，不将 `enabled` 或单条测试通过视为无监工完成。测试按影响闭包、sentinel、相关 mock E2E 执行；实现和实测记录提交，未满足验收保持 active。

2026-11-21 16:23 UTC：隔离新控制面已部署f2e927da，包含SR-WAKE/STRICT/
DISPOSITION/POLICY/BOOT及解释器冻结修复；原Worker与配置保持。受控disposition
evt-1a6b5db55281仅关闭已被原生已应用替代接替的旧枚举候选，原失败和原案卷未变；
约2秒后RM再次动作。准备期人工介入已记录，不计无人成功。原生CLI修复自动启动并
于16:28被收取完成，独立验证失败于writer briefing长度边界（2751 passed/1 failed），
未应用。Plan仍未审批，下一资格窗口仍未开始。

PLAN-SOURCE：Plan Critic已正式rejected，自动返工Planner也因缺少原正文abstained。
RM原生建议evt-68f6e695aa8c独立确认TODO只有可选引用、SOURCE-PROMPT没有输入，
未把数据缺失当作已证产品失败。8项回归RED复现传递/摘要/受控补充缺口；首轮两项
fixture调用签名错误已更正后重新RED，不计产品缺陷。修复保留准确需求/owner amendment
优先级，不解析Markdown链接，不把全部artifact_refs升级为必读。后续仍需独立审核准确16项Plan。

PLAN-SOURCE最终：独立复核无阻断；收窄后的自动影响闭包6150 passed/9 skipped，
sentinel53、flow10通过；之后补畸形读取限制拒绝分支，最终直接调用110、sentinel53、
flow10通过。完整受控redrive→Plan→必读回归已覆盖，旧manifest隔离回放确认TODO正文
现在可到达且必读；SOURCE-PROMPT仍需显式补充。代码待部署，不宣称实Run已恢复。

16:41:51 OLD按既有委托准确批准当前完整16项Plan（evt-fd09060d18a6），原无人失败不变。
后续重放却在准入拒绝前重开14项历史完成Task，terminal_tasks误判schema_gap又开Planner。
16:44:47经ControlledActionService暂停旧Run（evt-67ef2172796f），保持账本与工件；
新Run未暂停。此新缺陷与恢复义务保留，不能把Plan审批回执当Goal已完成。

16:51 SR-ENV事故扩大确认：NEW账本被测试force-init重建，仅66条，Kanban原anchor被5个
测试Task替代。现已停止NEW控制器、私有tmux与autoresearch，保全现场；原始检查点
2818条截至16:22:19，可恢复该点，之后差量完整性未知，不能以索引补造权威账本。
Plan输入修复已提交ae125f6d（尚未部署）。SR-ENV实际子进程隔离修复42直接、1823自动
影响闭包/7skip、sentinel53、flow10通过；待提交部署和检查点恢复，整批继续active。

17:09 UTC：检查点安装完成。通过 EventLog/TaskStore/SessionStore 重建可信2818条前缀，
保留6个有原账本绑定的终结TaskAttempt、原sess-6aa27f13及701个可校验ref描述符。
完整取证副本保存（活跃候选worktree原地保留），隔离污染投影、过期消费游标、pane/
process登记、检查点后repair结果与provider session绑定。实际安装后以dev/inode绑定
新消费位置，追加受控run-pause及maintenance-prepare，不从0重放。16:22:19之后原差量
完整性未知；旧enum候选disposition需重新受控决定，不能凭遗留投影宣称已关闭。

追加发现：修复Worker的三个孤儿测试进程也继承NEW项目环境。已核验PID、cwd、
ZF_PROJECT_ROOT后定向TERM；原独立验收隔离不足以覆盖Worker内自行测试。
共享环境工厂现接入self_repair_process真实启动及RM独立验证，保留provider凭据与
既有sandbox，仅剥离运行项目/控制面/Git/tmux/pytest环境并使用临时workspace。
实际provider替身及其测试子进程证明真实账本不变、临时workspace清理；51直接通过，
1852自动影响闭包/7skip、sentinel53、flow10通过。首条测试命令有不存在的文件路径，
未运行测试，纠正后重跑；不计产品缺陷。环境隔离不是进程树终止证明，SR-PROCESS仍待完成。

12:32–12:33 UTC真实Reader新增两项执行失败（lane3/4，19/40必读）：lane3提交的cursor解码后摘要缺少`c9b`，与实际已交付bundle不符，现有拒绝正确；lane4遇到事件日志持续追加，前缀复核期间签名变化导致两次读取拒绝。两项均不是产品失败，也不是宿主媒体缺失。先补此运行前置：证明正常并发append不会饿死读取且仍拒绝改写/旋转漏终态；提供基于已交付页面的恢复方法，不允许猜游标或绕过必读。

## 已实施，待整体验证与真实签收

- RR：正常append争用独立进程RED复现；短时前缀锁修复后artifact闭包51 passed。
  游标报错有受控恢复指引，skill的两套既有分发副本同步；不接受损坏cursor。
- SR1：实际RM独立验证前后绑定Git候选；apply要求tested_candidate。补齐显式provider、
  结构化focused命令与可信解释器的生产/消费；必需manual review没有证据不通过。
  条件性governance scope不一致按原policy交集修复，未扩大默认授权。
- SR2：先检查重启目录、明确spawn失败的受限revert、正确boot证据才恢复、未知apply/
  restart超时产出RM诊断。初次新测试因fixture父目录未创建失败，不算RED；随后加载
  a93fc04f原三个RM函数，实际9失败/1通过，证实两个旧边界。最新直接调用闭包208通过。
- 主自动闭包6716 passed/8 skipped，后续增量检查另记。以上为确定性验证，不是
  真实OS故障、provider自动修复或8×8资格通过。代码仍未部署；LIVE/QUAL保持未完成。

12:40 UTC lane3由ZaoFu自动创建新尝试，无监工重派；其余Reader仍在执行。
原16业务任务done与当前候选签收分开；不能把8路最终Reader算成8路Task Verify重叠。

后续补齐resident→CLI accepted-only选择器，真实Git/CLI（仅provider mock）及调用闭包
68 passed；测试编写中曾使用错误fixture参数/过宽Popen替身，已更正，不计为产品缺陷。
生产者/provider/解释器闭包243 passed；新增纵向mock 1 passed，实际Git和pytest证明
失败fixture→RM接受→隔离候选发布→独立验证→应用→匹配boot后原checkpoint重新可执行。
该测试的诊断/provider/reboot为测试输入，不证明真实OS重启或最终业务工作流执行完成。

最终主自动闭包6780 passed/8 skipped；resident CLI最后补丁后相关闭包68 passed；
最终sentinel53、flow mock10通过。只读研究复核SR1/3/4/5/6代码缺口关闭，SR2撤回
必现判断；其余仅skill旧文案一致性提示已同步。12:56真实账本只读探针两次读取
37201条，冷1.468秒/复用0.002秒，无控制状态写入；没有观察到两次之间新增事件，
因此并发正确性以独立进程竞态回归为据，不把这次计时当作实时竞争负载证明。

Reader修复提交：`01acdbdb fix: keep artifact reads live during concurrent event appends`。
源码修复按独立提交保留，整批在真实签收前继续active。最后skill/CLI闭包65通过、
1跳过。范围审查：变更超过25文件，含调用者、回归、方法副本与记录；采用Reader/
source-repair两提交，前半链独立只读复核及影响闭包，没有runtime状态/秘密文件入库。


13:00 UTC：`f3dbea84 fix: bind autonomous repair validation and checkpoint recovery`
与Reader提交已部署隔离控制面。runtime-load确认PID2795180、f3dbea84及源码摘要；
所有既有worker pane保留，配置sha与120额度不变。前文“未部署”为部署前观察。

13:10后核查：12:45 lane3自动retry-1并非再次游标错误，而是新consumer清单已更新，
required_reads仍同时保留祖先c8354…与当前fe573…两个摘要，前者在新manifest中不可解析。
19份回执均存在且可用；精确摘要拒绝正确，不能修改原冻结请求或放宽读取校验。
新增修复：新Reader派发前只移除经不可变祖先请求/清单证明的、完全相同的candidate-host-execution派生
必读条目，再由新清单重建；自定义选段/约束保留，已存在的坏retry祖先同样可恢复。
真实prepare调用链回归先2失败（首轮fixture API误用不计RED），修复后18通过。

RM在12:52已准确诊断上述故障（evt-d8e74e6c5787），不是未发现；随后runtime把建议
标记superseded。只读核查证实target_current=true、全Run progress_digest前置=false，
其他Reader进展使仍有效的本案诊断过期。聚合失败仍可生成诊断，但完整RM投影为空；
其过滤/案卷准入仍待核验，不能宣称自主恢复闭环已完成。

调用闭包第一轮638通过/7跳过、4失败，发现初版泛化清理过宽，会干扰已有critical
修复输入的校验；已收窄为仅刷新candidate-host-execution派生绑定，其余必读完整
保留。33项直接回归（含原四项安全回归）全部通过，完整闭包重新验证中。

收窄后的Reader调用闭包650 passed/7 skipped；sentinel53、flow mock10通过。


Reader绑定修复提交`9cc84d3a fix: refresh reader consumer bindings on retry`，13:17 UTC
已部署，旧pane保持，两个child-rebuild均获controlled action准入。对应原失败与
已通过6项保留；核验新尝试的manifest/read-policy并观察实际派发，尚未签收。

RM恢复继续补两项真实问题：
- 正式诊断请求绑定的故障Reader，用原operation/request/attempt/结果与Goal/代际核
  前置；同级Reader进展不使建议过期。新尝试、目标替换、终止仍拒绝。
- 旧建议已被kernel以operation_precondition_changed消费为superseded后，严格更新的
  同Run基础设施失败可重新进入原案卷的只读诊断。未返回请求、伪造消费、其他回复、
  人工等待和变更动作不放行；不把“诊断结束”视作故障修复。
后者真实RED2失败/3通过；前者初始fixture缺kernel origin已更正，回放9cc84d3a原函数
得到真实RED1失败/3通过。当前直接调用闭包94通过，整体影响闭包待执行。


RM主影响闭包5984 passed/7 skipped，sentinel53与flow mock10通过。随后实账本回放
发现role-v1 TaskAttempt没有request_hash，增补与workflow.operation.settled正式准入
回执、dispatch及原admitted_call_result_ref的绑定；不会把TaskAttempt ID当call attempt。
修复后12:52原快照scope=reader_operation且precondition=true；13:18已重试后的旧目标
current=false，符合预期。格式与调用链最后335项通过。研究复核同时检查原diagnose
转resident reprompt后的再次准入；补两项RED并使同一只读恢复规则覆盖实际转换形态。

下一窗口隔离目录已准备（未启动）：/tmp/zf-eight-unattended-20261121T132814Z；
对应独立harness worktree。新配置OA Astra high、Judge/RM 6.1 Sol high、其余Luna high；
Impl/Task Verify各8，采证120。源码修复仅新隔离实验采用验证后checkpoint应用，
限制runtime/events/tests/impl文档/active审计范围，拒绝配置/security/runtime状态文件；
主dev与原Run配置不动。旧准备脚本watchdog60短于同步宿主命令180，被配置校验正确
拒绝；新实验设watchdog600后重做准备，不算业务或无人验证失败。最终版本与预检待完成。


新窗口产品基线`a049638635b2d55944cfb4b833f50ee50c315839`；构建通过，准备阶段8项
文本工具34条冻结断言通过，后8项仍TODO。不计本轮Impl或无人交付。Docker预检镜像
sha256:8342e03e…、Chromium149.0.7827.22，浏览器可启动；没有用这次环境探针冒充
16项产品浏览器验收。新项目尚无runtime state或Run，待最终修复版本与冷启动预检。


最终独立只读复核签收：无阻断项，核对role-v1绑定、resident转换准入和重复分诊抑制；
复核会话未重跑测试或操作Run。最终sentinel53通过，flow mock10通过。
此修复尚不证明真实无人交付；原窗口保持失败，两个Reader仍执行中。


RM Reader诊断修复提交 `61bd4219 fix: preserve reader diagnosis scope across sibling progress`。
13:38 UTC通过受控控制器停启部署，原配置摘要不变、全部Worker pane/PID保留。
13:45:46 lane4重试正式passed，旧Run累计7/8 Reader passed，lane3仍在补齐结果；
19条当前目标命令证据保持有效，本次未新增宿主采证，原窗口失败结论不变。

下一轮规划正式启动：Run `workflow-ddd320aa0dc7c2ef`，anchor `TASK-127CE3`，
提案 `evt-7ab47f70fcf7`经委托审阅受控apply。产品基线 `ed631c3`，harness `61bd4219`。
`zf init`及静态Provider/派发检查通过，workflow inspect为0 STOP/7 WARN；
通用cold-start硬编码根src，实际app/src导致4/5（CLI退出0），记录未伪造目录。
准备阶段外部systemd监督接管，保留所有扫描会话；Plan准确版本审批仍开启。
新worktree官方8槽/Reader/宿主恢复加新增RM回归合计59 passed；仅准备期mock证据。
新资格窗口尚未开始：须准确Plan批准及前置条件复核后，才计零人工恢复。
预声明Worker退出、控制器退出与可重建Reader投影丢失，未实际注入均保持未证明；
Reader投影丢失不能冒充不可恢复语义正文丢失。模型/宿主120/权限边界均已记录。
准备过程操作员合同缺字段/错误tier、临时脚本属性错误均已纠正并在incidents.md记录；
它们不冒充运行中自动恢复，未生成业务通过结论。


新增真实问题RC-QUERY：13:46 lane3首次提交被门禁退回30项，自动修正正在执行。
进一步核实发现监工此前看到“missing=[]”不是完成：CLI查询遗漏不可变Reader策略，
实账本required=0与canonical=40不一致。最小修复统一required读取入口；未知策略明确
unknown/null、不伪装0项。已有4项RED（首次运行缺PYTHONPATH是环境错误，未计RED）；
43项直接调用测试通过，扩展CLI/Web/共享工件闭包及真实回放待完成。


RC-QUERY最终验证：188项影响闭包（含CLI/Web入口与mock E2E）、sentinel53、
flow smoke10通过。测试环境曾缺httpx；补齐项目dev/web依赖后重跑sentinel通过，
没有把环境启动错误计作产品RED。独立只读review未发现阻断项。
真实被拒绝sealed ledger摘要3d133cf3119088ee…回放：新查询required40/missing30，
与提交门禁一致；旧查询0/0。未写业务状态或消费凭证。

旧Run在13:59:52由lane3自动修正后正式passed；8/8 Reader通过，13:59:54产生
`test.passed evt-059b07206036`，14:00自动进入post-verify discovery。
这次补读与重新提交没有人工代交，之前的整轮人工介入记录仍保留，整体无人未通过。

新Run规划阶段13:51失败：Planner未产出必需Plan且失败报告recommendation枚举不一致。
RM在13:54自动诊断并接受修复evt-5fba52d8c454，已派发Luna隔离源码修复
（/tmp/zf-self-repair/self-repair_recommendation-evt-1d0ac4bd0779-e890eeeb6f07e70a-a0），
同时在原Run下发起缺失Plan语义诊断。当前只观察，不与其并发改动新harness HEAD；
独立验证、应用与恢复仍待真实结果。Plan尚未批准，8×8资格窗口仍未开始。


### 独立源码验证命令兼容修复（2026-11-21）

新Run的原生Luna修复在14:02:37完成，14:04:19由受控publisher发布候选1d39f418；
独立验证evt-77004db6fefb失败于command_not_allowlisted。冻结合同中的
`PYTHONPATH=src uv run pytest ...`未被验证器接受，未运行合同测试，未应用候选。
仅将已知pytest写法归一为可信解释器`sys.executable -m pytest`，保持候选cwd与
PYTHONPATH=src；只接受既有固定环境值，不执行候选venv入口、不扩大shell/任意环境权限。
冻结合同原文和原失败记录保留。非法引号也返回拒绝，不使验证器异常退出。
3项真实RED后，252项影响闭包、53项sentinel、3项flow smoke测试通过；
原生候选另由监工独立运行原合同96项通过，不等同正式自动验证receipt或自动应用。

新发现SR-FAIL-FEEDBACK：失败队列进入needs_review后没有待执行动作，RM observing；
14:16投影pending_actions=0、repair_closeout_pending。medium风险旧展示并非实际权限
阻断：router在显式verified_checkpoint_apply授权及绑定满足时允许medium。
下一步补失败到只读诊断路径并重新验证，不能用解析兼容修复声称运行已恢复。
旧Run仍在post-verify discovery；新Run Plan未批准，资格窗口未开始。

命令兼容最终自动影响闭包（dev-verify基线23782907）：1831 passed/7 skipped，
sentinel 53 passed，flow mock 10 passed。没有全仓或真实Provider验收冒充。
原生候选独立影响闭包539 passed；仍没有原生重验/应用receipt。


SR-FAIL-FEEDBACK最小修复：`needs_review + validation.failed`现在派生一次只读RM诊断，
checkpoint按失败验证event ID去重，携带原Run、queue/candidate、Git base/commit和合同
摘要。保持失败队列、候选与原验证结果，不自动改成通过，不盲重试或自动rebase。
已合并/丢弃候选不再开案。真实账本回放从0动作变成一个可派发diagnose-attention，
精确绑定evt-77004db6fefb；该回放不写业务事件。1项RED后251项直接调用闭包通过，
包含实际run_manager_tick派发诊断、重放去重和不产生apply/restart的断言。
独立只读研究确认部署后旧候选会base漂移，必须保留旧失败，通过Agent新合同产生
新候选；不将旧候选强行应用到新基线。controlled重验尚未从Agent输入完整接通，
属于已记录后续缺口，不能把当前转诊修复称作完整自动修复闭环。

SR-FAIL-FEEDBACK最终自动影响闭包：5931 passed/7 skipped（184秒），sentinel53、
flow mock10通过；resident实际二次准入形态另3项通过。准备部署新隔离harness，
保留旧候选及其失败，不动dev、不push。当前任务仍active，真实自动应用/恢复未证明。


2026-11-21 14:24:45：新实验harness已加载d1b173b7，loop.started evt-d1a15de78209
与runtime-load receipt一致，配置sha fadd068e…不变，全部worker pane/PID保留。
14:21 Anchor的1800秒orphan计时升级产生evt-dd6422696838，叠加等待门。14:28监工
依既有继续恢复授权，通过controlled human-decision-dismiss确认该计时告警，记录
operator.intent evt-3e382fb5e1a5；未删失败、未改任务结果，属于准备期人工介入。
14:29:55 runtime原生派发新的验证失败诊断evt-edb724d5b0ba（request rmd-d1244b1228c8ddfa）；
14:33:47 RM返回evt-bf72aa9523ef，完整保留旧候选/旧失败，指出controlled重验入口缺失。
因此已证明失败会回到RM，但尚未证明自动完成修复。

新增两个复现与修复：
- SR-SELECT：Autoresearch单飞槽忙时，普通attention占队首，空闲resident的后续诊断
  被饿死；原逻辑只对no_progress特例让行。改为按实际可用执行角色选择：保留旧诊断，
  优先已有合法可派发的resident/OA动作；人工门、pause、case和去重仍检查。
  1项RED，292项定向调用闭包通过；独立只读review未发现放宽权限。
- SR-CONT：原合同continuation的agent_recommendation是建议身份，不是workflow
  checkpoint。真实Git apply+mock restart复现后，未知/缺失恢复动作改为绑定合入与
  load receipt后的RM新诊断，原case保留在continuation，新诊断不复用已结束动作身份。
  没有匹配新load receipt仍不恢复；1项RED，268项调用闭包通过。

后续已确认能力缺口仍在本active批次：
- [ ] SR-REVALIDATE：Agent在既有授权内请求重验 -> kernel依据queue/候选/原失败/冻结
  合同重建验证动作，不能信任Agent重新传入的路径/命令/required标志；保留旧失败，
  有界的新验证记录通过后才回到apply检查。
- [ ] SR-DISPOSITION：旧候选因基线变化不可直接应用 -> 受控记录替代/丢弃关系，
  保留提交与失败，不以新候选存在就关闭原业务故障。
- [ ] SR-POLICY：medium风险和实际预授权apply准入展示一致，避免把已授权恢复误写为
  必须再次请Owner授权；真正缺失执行入口应报告能力缺口，不冒充权限限制。

新资格窗口仍未开始，旧Run42项post-verify必读已读齐，正式discovery/Judge/Goal待结果。

SR-SELECT/SR-CONT最终自动影响闭包：5944 passed/7 skipped，sentinel53、flow mock10
通过；独立只读review未发现阻断项。新Run的RM当前已返回human请求，因为缺少受控
重验入口；这不是新授权自动出现。下一步补kernel重建入口及当前基线漂移检查，
不让Agent重传命令/合同、不靠不断换失败ID重置次数。每候选+冻结合同+可信验证器
版本最多一次，失败ID仅校验当前性。旧失败与新结果均留账。


SR-REVALIDATE实现记录：复用repair-closeout-validate，已同时接入真实RM recommendation
消费与ControlledActionService；Agent/操作员只给queue_id、原失败event ID和合同digest，
kernel重建原worktree/base/commit/命令/required合同，忽略调用者传入的替代计划或路径。
仍检查source_repair启用、当前Owner路径范围、原失败当前性、pause/human/case门、
冻结合同与候选前后Git身份。每候选+合同+可信验证器源码digest仅一次；更换失败event
不能重置次数。原始失败保留，新结果携带previous_validation_event_id和validator_digest。
只有绑定成功的新结果才能使needs_review回到closeout_required，错误来源/旧候选/
旧失败/merged/discarded不恢复。活跃harness HEAD偏离冻结base时，验证前即明确失败
repair_validation_live_base_changed，避免用旧候选测试通过冒充可应用的新候选。
原生RM入口另有RED，已验证重复tick只执行一次且不采用Agent注入命令。
这仍不等同真实自动应用或最终无人验收通过。

旧Run补充：post-verify discovery已正式passed并进入最终Judge；14:35与14:42两次
提交被child_authorities的contract_revision/task_map_generation当前性门拒绝，
14:45:10 operation失败evt-91d70f6f1c21（output_repair_exhausted）。原16业务任务和
8Reader结果未改，不能把Judge的尝试算通过。独立研究正在对照不可变输入与门禁，
核查旧权限版本来自Agent输出还是派发上下文绑定。


2026-11-21 15:02 UTC复核补充：SR-REVALIDATE首轮自动影响闭包6397 passed/9 skipped、
sentinel53、flow mock10通过；独立只读review发现RM入口覆盖了当前诊断的recovery_case_id。
已用真实tick复现：原诊断case为blocked却执行验证。修复后保留已绑定诊断case，
Run/Task与冻结continuation矛盾即拒绝；操作员入口仍只采信原失败case。
新测试先证明case_blocked，再验证命令执行次数为0；相关闭包107 passed。

SR-JUDGE-REUSE：旧Run的42条拒绝来自14项已审核复用任务各3项身份。Candidate准入
已验证复用链并比较prior_identity，但Thin Judge直接与当前Task身份比较；第二次
Agent修改输出child_authorities没有作用，因为门禁读取不可变snapshot。
已抽取共用child authority校验；Judge先绑定当前冻结Candidate的Run/目标/generation/
Plan package，再核验完整复用证明、原Verify/整合及当前合同，普通任务继续校验当前身份。
不修改快照或原生结果，不以存在reuse字段放行。相关直接调用测试66 passed；
旧Run原始b8f5f2e1提交只读回放从42条错误变为0，尚未正式重试，不代表Judge通过。
篡改证明、当前合同、普通任务合同、Candidate目标、generation或原Verify ID均拒绝。
SR-REVALIDATE+SR-JUDGE-REUSE最终自动影响闭包与部署结果待记录；任务保持active。

最终review收口：RM仅继承已成功绑定的durable request原字段；无request或无case的
request不得用自报case绕开原失败门。相关109测试通过。Judge另外绑定准确closure
fact ref/digest和snapshot，从其kernel-admitted Verify追溯原native Candidate event、
freeze ref/digest及完整child authorities；新freeze同内容不能偷用旧验收，相同snapshot
的新closure有独立回归。Judge最终直接调用闭包68通过。独立只读review无剩余阻塞。
旧原生Judge的语义意见为blocked：产品旅程通过，但8+8真实重叠、自恢复、零介入证据
不足；本轮只修机械误拒，恢复后仍由Judge如实裁决，不能把旧窗口改判无监工成功。

本批自动影响闭包6562 passed/9 skipped，sentinel53、flow mock10通过；自动测试采集后
补充的closure pin回归及最终helper另跑直接调用闭包68 passed，恢复入口最终109 passed。
原始Judge结果完整_goal_closure_issues只读回放为0问题，其语义blocked原样保留。
以上是确定性/模拟与历史证据回放，不是新的real-provider无监工资格通过。待真实恢复。


15:12–15:15 UTC真实部署与恢复：实现提交539bd829（fix: preserve recovery and retained
verification authority）已分别fast-forward到两个实验harness分支，未合dev/未push。
两次runtime-load均证明源码539bd829；配置摘要不变、全部Worker pane/PID保留。
旧Run按既有Owner授权解除本次修复请求等待，精确恢复Judge，原生派发evt-0679d912c398，
新operation为wop-prd-lanes-final-judge-prd-trig-evt-60082029-retr-bf26469e5409。
Judge新会话启动Luna，按既有授权在tmux切换6.1 Sol high并确认；单列人工模型介入，
未把它计入无监工窗口。旧业务/Reader证据保留，最终语义裁决待正式回执。

新Run首次受控重验遇到human解除尚未进入当前投影，门禁拒绝且未执行命令。
只读确认gate为空、case admitted后才再次调用；evt-131cc16f41e8以
repair_validation_live_base_changed失败，results为空，冻结候选未应用。
原候选1d39f418/旧合同/evt-77004db6fefb原失败均保留；当前基线不允许强行rebase/apply。
RM已经收到另一条恢复诊断，新增验证失败应由调度继续交给原生RM。
资格窗口仍未开始；SR-DISPOSITION与真实自动apply→restart→resume仍未签收。


15:27–15:34 UTC原生恢复实测：RM自行产生两项repair建议并派发Luna workers。
原枚举修复候选22630368由独立验证evt-30e3183682a9通过，runtime应用为3d9b917f，
merge回执evt-944b7e8dd7ab；systemd自行重启，evt-fd1659b0e696的runtime-load匹配
applied commit/source tree/state，repaired_runtime_observed=True。根会话未代替本轮
生成候选、应用或启动重启。原业务Planner尚未恢复，不能将四步当作五步闭环通过。
CLI包装事件注册候选dfb5f95c受并行前一项应用影响，验证拒绝live_base_changed，保留失败。
另有watchdog候选b3e965a4验证通过，但缺少continuation，保持应用门拒绝。

发现SR-WAKE：15:27:12 repair-closeout-validate失败后，post-repair诊断和新失败诊断
均可派发、门禁为空，但self-event被NOOP过滤且不在快速恢复输入集合，实际到15:32:17
才由300秒Supervisor周期唤醒。4条真实tick入口RED已复现；仅将kernel-origin、带
queue+contract绑定的repair-closeout-validate/apply成功或失败收据作为新输入。普通
bookkeeping、blocked、未绑定或worker事件不快唤醒；cursor消费后不重复循环。

SR-STRICT：strict repair_contract_required队列原来可因human确认/普通业务进展消失，
且terminal_run_passed可绕过它。修复只针对strict非merged/discarded行；legacy不变。
初始fixture缺resume_original_workflow已更正，对642ec609实跑RED为3失败/3通过。
两个修复的直接调用及相关mock闭包236通过，独立review和自动影响闭包待完成。
当前还存在活跃原生repair worker，暂不部署根会话更新去改变其冻结基线。

15:40 UTC：SR-WAKE/SR-STRICT 自动影响闭包6149 passed/7 skipped，sentinel53、flow mock10通过；独立只读review未发现阻断项，确认cursor取执行前快照，不吞掉执行中产生的后继回执。仍不代表原业务恢复或无监工资格通过。原生第三个repair worker已终结，候选6aea45e2同样因live_base_changed被拒绝，尚未应用。


15:41–15:56 UTC增量：559f9f00提交SR-WAKE/STRICT；将原生应用3d9b917f并入本隔离修复分支，合并提交604b25ab（未合dev/未push），38项交叉测试通过。NEW于15:41:14受控部署604b25ab，停前/停后均无活跃源码repair worker，全部Worker pane/PID保持，配置摘要fadd068e…与120额度不变；备份before-repair-wake-deploy。此部署是准备期人工介入。

RM原生建议evt-3ecdc71f434a选择精确从已完成Scan evt-35aeed9fff80重触发缺失prd-plan。原恢复尚需owner审批，不宣称五步无人闭环。按既有授权经共享审批服务提交evt-a36f394ab063，仅批准hdec-049cf7fbfe66；未批准尚未生成的Plan，另一个side failure-closeout审批不动。15:46:24内核stage-retrigger applied evt-ba56da6bcd1d，15:46:29请求新Planner wop-prd-plan-planner-trig-evt-3ca27989-rework-af2db023aeda，实际running。8×8资格仍未开始。

SR-WAKE补充：上述审批15:42:04→15:46:24才消费，真实入口RED证明human.escalation.acknowledged不在快速输入集合。将其作为唤醒输入，审批自身的token/当前性/授权准入不变。
SR-DISPOSITION：新增受控动作repair-closeout-dispose，仅允许严格failed旧候选由已admitted、验证且应用的明确关联替代候选接替。CAS queue/合同/当前失败，核对替代请求原失败链、合同、验证、apply及Run/Task范围。只发append-only处置，不改Git、不删除旧证据、不恢复业务。原请求case准入后另查独立处置case，处置回执不改变原案卷。无此入口RED7失败/11通过；直接回归287通过后，独立review指出resident案卷覆盖，已收窄并强化不变性断言。兼容旧operator验证未设origin只允许完整external intent→同绑定RM planned→failed链，worker仍拒绝；实际旧evt-131cc16f41e8只读预检可通过，未执行收尾。
SR-POLICY：medium路径风险只给policy_evaluation_required，审批需求未知；high原审批提示保持，实际source-repair准入未改。
SR-BOOT：原生3d9b917f在15:27有精确boot证明；root升级604b25ab后15:46又误报原repair_restart_outcome_unknown。历史证明与当前resume门分离，历史精确证明只用于分类repair_runtime_binding_changed，不放行当前未知版本。RED1失败/3通过。未篡改旧事件或把根会话升级算作原生自恢复。

16:10 UTC：补齐SR-DISPOSITION历史链的前次失败/source commit绑定与双case拒绝回归；测试fixture曾把首次失败误标为重验，现通过真实ControlledActionService生成合法失败重验链，不把fixture错误记为生产缺陷。最终直接调用闭包341 passed。首轮自动闭包在补用例时出现xdist收集不一致，7个collection error，该轮无效；冻结候选后重跑，禁止以此宣称验证通过。

SR-FREEZE：原生CLI候选续作evt-3e61598caa20多次因repair_contract_freeze_failed拒绝，原因是当前可信sys.executable位于harness目录，被字符串检查误当旧工作区测试目标。保留全部失败，先识别受控pytest命令，仅豁免可信解释器argv[0]，其余测试/config参数仍拒绝原目录，未知解释器与shell形式保持原安全检查。真实冻结入口RED1失败/1通过，修复后直接闭包包含此项。

NEW第二项原生修复（Supervisor已dismissed attention投影）已自行应用为f5eb24e7并重启，根会话未代替该项应用；待核对独立收据、保留到根修复分支。Planner首轮重触发passed后，合成评审16:07 rejected：必读bundle缺精确docs/prd/TODO.md与SOURCE-PROMPT.md，不能凭Planner摘要独立审阅。运行时已自动派发下一Planner代际，尚未解决此authority-input缺口，不能批准Plan或宣称无人8×8开始。

16:22 UTC：冻结提交c7e403ac加原生f5eb24e7整合为4ab2e751，自动影响闭包7143 passed/9 skipped。随后sentinel52通过/1失败，定位原生Supervisor修复使supervisor_attention.py超过1000行；未绕过哨兵、未将失败当通过。沿同一职责提取supervisor_human_decisions.py（79行），原文件990行，行为不变。最终增量自动闭包111 passed、sentinel53、flow mock10通过。7143是在提取前的冻结候选运行，提取后的证据是111+53+10，不称为全pytest或真实无人E2E。

旧Run最终Judge15:57:15正式settled/blocked，准入evt-7bf557eee79e、结果bf040c32…，69必读全部完成。产品验收passed，19目标声明closed、1 blocked；明确未获得16任务Plan的准确审批（evt-1695fe7d4282）。此外监工仍保留原无人窗口失败、真实8+8重叠和故障恢复未证明，不能因后来审批或本次机械门修复宣称原轮无人通过。旧Run尚无Goal完成或本地交付通过。

新Plan第二轮16:17:08正式abstained，missing primary authority bodies仍在；RM16:17:13自行进入语义诊断rmd-ffb913102f3bd820。只读研究确认：trigger artifact_refs与Task spec已声明TODO但plan handoff未消费；SOURCE-PROMPT只在正文引用中，未显式声明为原文输入。应由上游声明并冻结两份正文再进入新审阅输入；不递归猜链接，不靠重复Planner改文案掩盖输入缺失。此后续项尚未修复。

17:35 UTC 当前执行记录（先前时点记录保留）：NEW已部署1a9e8667，配置摘要不变。
精确恢复Scan来源evt-59044df11201→evt-84bd357500c4，新Planner于17:15运行；
真实manifest中TODO.md与SOURCE-PROMPT.md正文均以显式声明成为必读。首次operator_feedback
类型错误被受控入口拒绝，修正后执行；控制面启动因私有transport已清理失败，完成
transport bootstrap后启动。两项操作顺序/参数错误及全部人工介入留档，不计无人成功。
原生RM另自行产出a51bb6c7候选，17:31验证并应用f3d42868；仍待核验重启及原任务延续。

RESUME-TERMINAL：写Task前预演admission，capacity门前置；晚到的准确Owner批准只有
相同package正文/显式ports、Candidate、当前子任务及完成复用证据全有效才抑制重复派发。
integration捕获authority时核对语义revision，写Candidate前检查authority未变；terminal
校验receipt绑定及后续revision/dispatch，再CAS，旧integration不能完成新合同。terminal_tasks
仅产生机械冲突，首次即交RM诊断，不继承schema_gap；cancel与diagnosis之间崩溃可重放补派。
独立review复现4条RED，修复后33直接测试通过。先前影响闭包4276 passed/7 skipped，
sentinel因Supervisor超过1000行拒绝；已提取同职责helper，尚待最终闭包。
不宣称跨多Task事务或14项历史完成记录已经修复；补偿仍需受控版本递增及原完成证明。

最终检查补充：自动影响闭包11失败/4277通过/7skip，定位三处旧integration测试夹具
使用假revision常量，已改用生产相同effective_contract_revision，未放宽门禁。
独立review另发现相同合同/状态的新dispatch可被terminal CAS清除；RED复现后Store
锁内增加expected_active_dispatch_id。相关61测试通过。cancel本身已足够生成稳定RM
诊断，append间崩溃不依赖原trigger再次执行，15项路由回归通过；最终闭包重新运行。

17:48 UTC：扩大到Store影响闭包10535 passed/9 skipped/1 failed。唯一失败为source-index
旧fixture：两Task配置一个positional writer，原代码capacity拒绝后留下canonical backlog，
普通scheduler漏派一项，测试只数send误认为成功。先给两role又暴露positional不提供依赖
排队；最终使用该用途的affinity lane，并增加fanout.started及无cancel断言。不是放宽capacity，
也不把遗漏的依赖执行算作正确结果。原1role capacity零落盘回归继续保留。
诊断投影改从全history的kernel cancellation生成；reject不拥有诊断身份，窗口裁剪不改source。
真实Planner17:40:29正式passed、synth17:40:35已派发。两份原文在Planner及synth端的字节数
分别18366/7613，SHA与完整选段一致，报告plan-original-input-handoff-proof.json；评审结果待收取。

防复发最终验证：修正后的直接调用闭包273 passed；sentinel53、flow mock10通过，
独立只读review无确定阻断。扩大闭包10535通过/9skip是在最后fixture修正前，其唯一失败
已由真实fanout入口回归修正并重验，不能把两组相加当唯一用例数或全pytest通过。
14项历史补偿、源码修复进程树治理及新无监工资格仍未完成，整批保持active。

18:00 UTC后续补偿实现中：防复发提交6b9d72e3未合dev/未push。新增受控恢复分支独立返回，
不走stage-retrigger普通重发；要求暂停Run、准确Owner Plan批准、已拒绝的terminal replay、
原归档/变更ref及全Candidate完成证明。预览冻结当前/原始/向前新版本，精确Owner intent
绑定manifest和digest。TaskStore仅允许同一既有归档桶，整批一次原子append，索引不变，
重放只接受全旧或全新，无backlog过渡、不发task.done。Candidate消费者显式携带补偿ref，
原前缀中的完成证明经认证只读视图解析，后续authority变化拒绝。
真实OLD只读preview已核验14项全部完成链，未执行恢复写入。新增及调用方100测试通过，
尚待独立review及最终闭包；一次错误测试路径未运行任何测试，纠正后重跑。
NEW Plan synth17:50正式rejected：8+8/自动恢复/零人工恢复只有说明文字，缺独立验收项、
owner、证据及分别裁定标准；属于有效计划合同缺口。17:50:35运行时自行派发返工Planner，
保持16项、不加预算/权限、不伪造未来收据。原始输入正文缺失已不再是本次拒绝原因。

终态补偿独立review发现重放审计P2：already-restored分支只验证sidecar，未重绑原mutation
event ID。按review用已恢复后替换为不存在event的场景实跑RED；共用incident_event现覆盖
preview/首次apply/重放/证据消费，核对kernel来源、Task/Run/ref/map及批准→mutation→取消。
完整replacement机械校验、当前所有Candidate成员完成证明、原归档绑定均保留。最终调用
闭包127 passed，独立复核无剩余确定阻断，冻结进入自动闭包。真实Run仍未补偿或恢复。

终态补偿冻结自动检查10522 passed/9 skipped、serial4、sentinel53、flow10通过。
最后Doctor一致性断言复现旧格式空binding的token失配；preview与manifest校验均先按
既有task_execution_binding规范化，再生成向前authority，历史原body不改。修正后直接
调用78 passed，独立复核无阻断；新预览需按最终代码重建。一次不存在测试路径导致
未执行测试，纠正后重跑，不将该次当产品失败。真实补偿仍待执行。

18:21–18:24 UTC：OLD部署2ca7d927，控制器PID692864、Worker pane全保留、配置摘要
cd98304a…不变。新代码重新预览14项，manifest e15f8055…；原完成时间/完整合同保持，
authority由错误seq4向前到seq5。受控intent evt-5f83dd321561、补偿完成evt-cc91713f50c3；
精确14替换及Doctor一致性通过，无新增task.done或业务派发。预览首次按单秒筛选仅2项，
断言拒绝未写；纠正为准确批准→取消事件区间14项后执行。
18:26最终Discovery aggregate已受控请求重建evt-7fc60ea3f3c2；run-resume拒绝，因为
错误Planner链已把Run从paused变为blocked。经既有goal CLI仅恢复active
(evt-7aa25aff36ff)，保留旧cap/失败和120额度，真实最终验收仍待结果。

NEW17:31原生源码修复f3d42868独立验证/应用/boot，原Planner17:40继续passed；
本分支99c23cd5保留该修复，没有代替原生apply。18:17 Plan rejected并达cap；只读
研究确认candidate_verify owner可进入task-stage verify，Critic按owner筛选Task Verify
的理由不成立。实际产物使用不识别的work_mode/verification_mode，且审计命令遗漏build。
18:26受控新规划代际evt-93abdded5768交回源码证据与准确字段，保持16项/owner/权限，
Planner运行中；没有批准Plan或开始新资格窗口。

SR-PROCESS：四项真实替身入口RED证明正常退出/超时/双fork及独立验收遗留后代。
私有subreaper接入Worker与RM验证，清理后才完成，unknown阻止optional成功。首次
GREEN失败定位uv Python3.11未编译pidfd绑定，采用libc同名接口而非宽松kill PID。
增加调用Worker被SIGKILL后清理、无关进程存活、后续写入停止及unknown持久化回归；
97项直接调用闭包通过。一次不存在lifecycle测试路径未执行，纠正后重跑。

SR-PROCESS最终：首版自动闭包1870 passed/7 skipped、sentinel53、flow10。独立review
指出全局PID祖先快照复用风险；改为只枚举直接子进程、pidfd打开后复核PPid/birth，
逐轮收养后代，增加拒绝过期归属负例。最终34个真实边界/直接测试、sentinel53、flow10
通过，独立复核无剩余确定阻断。上轮1870是此前冻结版，不称最终全量或无人E2E。

18:33 UTC：24420dc0进程治理部署NEW，PID756777，所有Worker pane保持，配置SHA
fadd068e…不变；部署前没有活跃源码修复Worker，属于准备期人工介入。

AGG-RESUME：OLD受控聚合请求evt-7fc60ea3f3c2在Run仍blocked时被消费，原reducer
返回workflow_run_terminal，随后active并没有重试。实账本只读比较证明请求时terminal、
当前无currentness阻塞。首次两版回归没有真实SessionStore，重启从0重放造成假GREEN；
补齐真实持久游标后RED。新增kernel已准入请求的idle/解除阻塞重放，继续由原reducer
校验Candidate/source/子项/去重，177直接调用通过；待独立复核与最终闭包。

AGG-RESUME首版自动3322 passed/7 skipped、sentinel53、flow10。独立review发现
fanout取消未拦截、completed→publish崩溃永久缺驱动。补精确publication sidecar并复用
原generic payload builder，首次/直接重试/周期重试共用，lane分支保持。增加原生冻结
身份、真实Git移动、取消以及三持久边界恢复回归。校验器原机械拒绝事件缺origin，
补kernel来源，避免把worker仿造的blocked ID当核验拒绝。
直接210passed/2failed定位legacy partial continuation重扫，不是冻结Candidate：原fixture
目标partial456、baseline candidate123。恢复显式continuation身份检查，仍要求baseline
事件当前及Git ref不移动；测试Git seam返回既有partial456，正文准入未放宽。最终36项
直接边界通过（含同head新freeze、无关worker拒绝事件、重启三断点），待最终闭包/review。

AGG-RESUME最终：冻结版自动影响闭包11697 passed/10 skipped、serial4、sentinel53、
flow10。最后独立复核发现source证据在freeze A、fanout在同head新freeze B启动时可能
错用B；现沿原trigger.source_event_id精确核验更早kernel同Run证据的冻结绑定，不要求
latest test.passed。新增跨freeze/跨Run/worker来源/缺失/未来证据负例及合法后续pass复用。
最后199项直接调用、sentinel53、flow10通过；实OLD只读检查原evt-059b07206036绑定仍
有效，可消费原rebuild请求。全自动11697为此前冻结版，未宣称最终全量或无人E2E。

最终兼容复核：repair副本的action完成回执晚于副本事件，历史截断不能丢掉其授权链。
选择仍固定历史前缀，仅以完整账本验证选中的精确副本；现有调用默认不变。真实
controlled repair fixture覆盖两种副本及新freeze拒绝。首次250通过/2失败为fixture
缺manifest accessor，补齐后252直接调用/候选恢复mock、sentinel53、flow10通过。
独立静态复核无剩余确定阻断；准备部署。新Plan18:56 Planner passed、18:57 Synth
运行；资格协议更新并保留原版/原生修复证据，qualification_started=false。

19:03–19:05 UTC：27309e93 `fix: resume admitted aggregate rebuilds with exact publication recovery`
已提交并部署OLD/NEW隔离控制面，未merge dev或push。PID分别968795/974102，配置SHA
cd98304a…/fadd068e…保持，所有Worker pane保留。OLD原请求evt-7fc60ea3f3c2自动消费，
新aggregate evt-d271e7814187、Discovery evt-0c3009b2bda2、Goal evt-5640085ed8e4、
Judge operation wop-prd-lanes-final-judge-prd-trig-evt-5640085e-a075a6f53872；没有新增
操作员聚合请求。Judge新会话初始Luna，按用户授权tmux内切6.1-sol high，未重启。
真实证据位于OLD reports/eight-qualification-20261120T122929Z/aggregate-replay-real-proof.json；
Judge结论与最终交付仍待核验，原无人失败窗口保持。NEW Planner passed、Synth运行，
资格尚未开始；故障注入工具仅准备，未执行。

19:30 UTC OLD 最终 Judge 再次协议失败（evt-6bf0c7d99661）：14 个 Goal children
缺少受控补偿 ref，产品通过意见没有准入，不能计为 Judge 通过。19:25/19:28 原生
WRC continuation 已自动补齐读取，但不能解决此证明消费者不一致。问题记录：
goal-compensation-authority-mismatch.json；独立研究确认根因。新回归先 RED 1/9，
修复 Goal 专属已准入历史证据的只读补偿视图后，67 项直接测试通过；待影响闭包、
独立 review 和部署恢复。NEW 仍为 Plan 准备阶段，同一 Planner 已消费补充反馈，
正在修正 audit 命令归属、生命周期证明与数字专属错误恢复验收，尚未批准或计入资格。

Goal 补偿修复验证：自动影响闭包238 passed/7 skipped，sentinel53、flow mock10；
独立静态复核通过。随后补齐真实 Goal builder→Thin Judge 调用回归与已存在错误/空
restore ref 拒绝用例，最终36项相关回归通过。真实 OLD 原结果只读回放 authority_issues=[]，
仍保留原 blocked 语义意见，未替 Judge 提交结果。另发现 operation.failed 后没有对应
fanout.child.failed，恢复投影无合法 Reader checkpoint；记录并继续排查终态交接。

补充真实阻断：`result submit` 耗尽仅生成 operation.failed 与 task.attempt.failed，
未发布语义事件；fanout child 仍 dispatched，loss sweep 无法重派 failed operation，
child-rebuild 又缺少精确 child failure。新增 Reader 协议失败投影到现有恢复 sweep，
仅处理精确内核 output_repair_exhausted，核验 immutable request/envelope/current
transport binding，保留语义未知与原失败。独立研究确认该链路根因；初始10项失败
为测试误以为 EventLog.append 返回事件，修正fixture后10通过，待影响闭包及部署。

协议终态最终验证：第一版自动2377 passed/7 skipped、sentinel53、flow10；独立review
发现投影失败窗口，补已有精确child.failed的投影恢复及全局pause门禁，13项边界通过。
最终自动2380 passed/7 skipped、sentinel53、flow mock10，独立静态复核通过。
真实账本截至19:30 evt-6bf0c7d99661的纯reducer回放selected=true、绑定准确且无状态写入。
现场19:45 native idle timeout另产生evt-fb2f1e2c6418；补丁保留该历史失败，恢复使用正常
child-rebuild。NEW 19:50前后Planner passed并进入Synth；发现product_acceptance_spec
仍漏改文本工具数字恢复要求，已通过精确worker.reply反馈evt-a373749f4cab，仍不批准。


19:52–19:55 UTC：22e6e1f0及7becfc7b部署OLD/NEW隔离控制面，HEAD 7becfc7b、
PID1207168/1208223，配置及Worker panes保持。OLD受控child-rebuild生成
wop-prd-lanes-final-judge-prd-trig-evt-5640085e-retr-ebb5314aa35b，19:54正式派发；
按用户既有授权仅在tmux切Judge到6.1-sol high。20:06只读必读进度42/70，尚无
正式Judge结果；不将协议修复的确定性测试当业务或无人验收。未合dev、未push。

20:02 UTC 新Plan返修：19:55 Synth正式rejected，其中八项文本工具仍继承数字错误
恢复断言是真实遗漏；另一生命周期mandatory冲突追溯至监工18:26反馈。原始TODO
及SOURCE-PROMPT要求外部本session只读采证、交付后分四维评估，并明确不得要求
未来Judge/交付收据。监工曾额外指定RM为资格收据生产者、将完整窗口判定挂产品
验收，形成循环依赖；此工程假设无原文/实现依据，不能继续要求Agent照错返工。
受控intent evt-d88df0cd62f8明确取代evt-4102a74d1a4b的错误分配，trigger
 evt-5c65ca2533c5派发新Planner；16业务Task、19命令、全部独立资格标准和权限
保持。生命周期义务映射到交付后外部资格判定，不隐藏、降级或伪造通过。
本轮仍为准备期，资格窗口未开始；报告plan-qualification-boundary-recovery.json。


20:20–20:31 UTC：NEW Planner正式通过evt-7a0ae468f400，Synth operation
wop-prd-plan-synth-trig-evt-5c65ca25-d410f5b1e3fa独立审核中。操作员读完冻结Plan
正文并核对16独立Task、19命令、20产品Goal claim、3独立强制资格义务及来源映射，
尚未批准。产品acceptance_spec与real_e2e断言一致，文本工具不再继承数字恢复。
证据planner-package-operator-review.json；新窗口仍未开始、正式业务0/16。

QUAL-TOOLS：独立研究发现初版脚本会用事件入账时刻扩大区间、跨turn串接，注入前
operation可能已过期，worker恢复仅凭done，资格窗口/Reader阶段门禁与共享chunk检查
不全。现使用精确原生token_count时间和turn终止；未知间隙不计。注入前核对准确
审批/package/generation、当前Run/全局pause、dispatch/native turn/pidfd/出生时间。
controller要求只读因果审阅绑定原生恢复链，并验证当前boot/PID实际加载的声明源码。
Reader只删除固定完整正文可重建、当前活跃消费者独占的派生块，失败仍为未知。
实际Reader读取attempt取nested payload/dispatch_id而非TaskAttempt id。首次CLI
导入检查失败为新增import顺序错误，已纠正并补入口检查。25项通过，独立静态复核
无剩余确定阻断；真实旧MEDIAN Verify原生区间字段兼容通过，不冒充本轮容量成绩。
六份脚本及摘要保全于NEW reports的qualification-tools/；全部故障尚未注入。

OLD监工操作纠正：19:55界面所选6.1-sol不等于当前turn实际模型；原生记录仍Luna。
20:13为激活选定模型主动Esc，中断被runtime正确记为provider关闭且恢复耗尽，
operation终结evt-89f0c5deb1dd，不能原地续跑。20:18原生6.1-sol turn已无有效
operation，因此不计有效恢复。20:20受控child-rebuild intent evt-f001d39cc29e
重建operation wop-prd-lanes-final-judge-prd-trig-evt-5640085e-retr-bb655a9584d4、
retry-4。新pane %475初始仍Luna；仅重新选定未来turn设置，未再次中断，必须按实际
turn证据报告模型。保留16完成业务、同Candidate和旧失败；不扩预算或改资格结论。
详见OLD judge-model-interruption-incident.json。此人工操作失误不归因产品缺陷；
真实最终Judge/Goal/local delivery仍待正式结果，整批继续active。

2026-11-22 09:35 UTC：用户续额度并要求切换账号后继续。主登录已于09:19更新，
两次隔离原生最小请求 Luna/6.1-sol high 均READY，未重复logout、未打印凭据。
OLD retry-4仍保留failed；受控恢复intent evt-63af3b2f56ce、action evt-708fb466a6e2，
新的Judge operation 4925cdb33587 running。原生新turn 01a2029c-9842-77d1-9287-bf528419125f
确认6.1-sol/high。原配置Luna导致重试重建丢失tmux选型，现按既有授权仅持久化
OLD Judge/RM的Sol模型并重载控制面；16已完成任务、Candidate、权限预算保持。
真实资格窗口依然未开始，不把账号探针或人工恢复称为无人通过。

新增恢复阻断 SYNTH-STRUCTURE：NEW evt-c4872cc07975 passed 携带旧a2b7b3fd task map，
与当前5c65ca25矩阵不一致，evt-4e0ca745d0c7正确拒绝。直接修复纯Critic结构权限：
运行时冻结verdict-only，清除PRD纯审核结构模板，validate/submit/准入共同拒绝越权
结构输出，历史evidence引用保留。验收：实际dispatch冻结权限 -> verify: 调用方测试；
旧/当前/任意ref和inline结构越权 -> verify: 不准入且同attempt可修正；正确审核delta
-> verify: 正常提交，旧合同保持。71项直接测试通过；首次2项旧feedback兼容回归和
1项fixture缺created_by、1项fixture未加载request identity均已定位修正，自动闭包进行中。

SYNTH-STRUCTURE 验证完成：最终直接调用18 passed；自动影响闭包5200 passed/7 skipped，
1项旧Refactor mock仍让Critic复制Planner全部结构而失败，按真实verdict-only职责修正
fixture后该E2E与新边界合计20 passed。sentinel53、flow mock10通过。最初命令用了
系统Python而无xdist，已仅中断该测试进程，改项目venv六进程运行；不涉及现场Worker。
此为聚焦影响闭包，不声称全量或真实无人验收。恢复策略不扩大aggregate-rebuild权限：
该入口仅限Discovery。当前已settled Synth不能复活；最新合法Plan断点需用受控阶段
重触发，声明精确冻结Planner材料并复用，随后新Synth正常审批前审核。


2026-11-22 09:45–10:00 UTC 真实恢复后续：修复3d53ebaa已部署NEW隔离控制面，
boot evt-59f48dd92638、PID55430，配置与Worker panes保留；未合dev、未push。
通过stage-retrigger intent evt-7f56be1f5747 / trigger evt-042823a89a3a，声明12份
精确冻结Planner材料恢复最新合法断点；新Planner operation 0ee0dfc2a35c正在运行，
不是复活已settled Synth。10:00前只读核对34项必读尚缺6项，正式业务仍0/16，
Plan未批准，资格窗口未开始。

账号切换不会让所有驻留CLI自动刷新凭据：NEW旧RM reprompt明确返回账号已切换、
旧access token无法refresh。新原生进程最小调用正常。受控resident-only restart请求
evt-7ba161556142由内核执行evt-0bef12627c24；observe-only首轮产生
evt-2507b7cb2a07、后续建议evt-ec9436743072。无需重复logout，未改预算权限，
未重启业务Workflow。报告resident-auth-error-restart.json保留真实错误与恢复证据。

OLD最终Judge在09:49正式准入rejected（evt-4e07872fa3e7 / evt-ed0c42b8c8ad）：
19项产品claim已closed，但原始完整实验claim仍open；历史上精确16任务Plan批准
晚于实施、无人窗口有人工介入，无法追溯修复为通过。受控run-pause
evt-7d2764b66b12保全失败并停止继续派发重复规划；不等于local delivery/Goal通过，
不把16已完成业务任务当作8×8无监工资格。详见OLD rejected-qualification-preserved.json。

待处置历史watchdog候选b3e965a4：独立只读复核确认quiescent唤醒后旧projection
误报补丁仍适用于当前源码，但旧contract基线539bd829已漂移，且原continuation缺
checkpoint/safe_resume_action。旧验证通过不能授权当前基线apply；不填造恢复绑定，
不伪造merged/discarded。正在核查现有合法收尾路径，整批保持active。

当前历史收尾门的只读复核：该门并非全局阻塞；continuation选择器会跳过blocked apply选择其他动作，但严格pending仍阻止最终完成。现有dispose仅支持“失败且已有验证/应用替代”，不支持撤回已验证但基线漂移/缺continuation候选；这是入口缺口，不能伪造discarded。原案卷与后续修复义务保留。

SR-WATCHDOG-WAKE验证完成：当前HEAD先RED（1失败/5通过），修复与边界参数化后直接8通过；自动影响闭包256 passed/7 skipped、sentinel53、flow mock10通过。docs/instruction8项通过。属于确定性聚焦验证，未部署到运行控制面，未证明无监工通过。原生候选b3e965a4经独立只读review无确定源码阻断；本轮额外覆盖刷新窗口到期仍报警。

10:03 UTC cfa4c855部署NEW成功：boot evt-257a89c96e19、PID142323，source tree
d6ca11bb…，配置及全部Worker panes保持；资格协议已更新声明源码，未合dev/push。

10:07–10:09真实读取诊断：冻结34项443082去重bytes；约7分钟一次compaction
使--resume重读前缀。另发现Worker隐藏正文仅输出元数据，已有读取计数不能证明
模型消费。精确反馈evt-bebb746b5ce6、evt-a0225b62907b均已由Kernel sent；不改
固定输入/政策，不代读或补造收据。Planner方法增加工作记录、精确续读及隐藏/截断
正文恢复纪律，正在验证；此准备期人工介入不计无人成功。

操作员恢复脚本自身问题：首次RM reprompt漏safe_resume_action及expected downstream
字段，造成evt-277082ce9bf7验证失败；不是RM没有收到提示。10:09使用完整既有
动作合同真实重新投递，evt-207d978144d1核验resident.prompted通过，保留原失败。
这只证明投递效果，不替代Agent诊断或业务恢复成功。

读取方法变更：直接12项、自动docs/skills闭包41 passed/1 skipped；两种分发副本与canonical一致。独立研究确认源码级缺口：历史Provider delivery观察目前是非阻断诊断，仅宿主产出的页也可能满足ledger覆盖。需要新冻结合同可声明完整交付门；旧合同不能被现场静默改写。该准入修复待实施，真实Plan尚未批准。
