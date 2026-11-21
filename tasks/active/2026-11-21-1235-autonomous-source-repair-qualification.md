# 自动源码修复闭环与无监工资格验证

> 状态: active

Owner 已批准按下一步执行：现有真实 Run 继续独立验收，同时核验并补齐源码修复链；条件满足后再开始新的零人工干预窗口。基线 a93fc04f，沿用隔离工作区。当前 120 次宿主采证授权保持，不合 dev、不 push。

| ID | 工作与验收 | 当前状态 |
|---|---|---|
| SR1 | 核对 RM 诊断、修复合同、隔离工作区、发布与独立验证实际调用链 -> verify: 覆盖真实入口的确定性测试，未知原因、越权、失效绑定保持拒绝。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR2 | 故障注入应用与重启边界，修复已复现问题 -> verify: 缺少重启前提不改源码；失败不假报已恢复；正确版本重绑后才恢复原检查点。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR3 | 配置受限的自动修复实验并验证 -> verify: 路径范围、合同/验证证据、授权和检查点均有效；模拟通过后才部署真实实验；完整保留失败与人工介入记录。 | 原生候选已验证、应用、重启；恢复Plan需owner审批，已执行并进入新一轮Plan返工；五步无人闭环未通过 |
| LIVE | 既有 Run Reader → Judge → Goal → 本地交付 -> verify: 每阶段正式结果、同候选/目标/合同绑定，不将代码测试冒充真实交付。 | 16/16业务任务、8/8 Reader与discovery通过；最终Judge正式blocked（19 closed/1 blocked，准确16项Plan审批缺失），原无人窗口失败保留 |
| SR-WAKE | 源码修复步骤终态及时驱动下一步 -> verify: 真实tick入口在Supervisor周期前运行一次，普通bookkeeping不形成循环。 | 本地修复及独立review通过；236项直接闭包、6149项自动闭包、sentinel53、flow10通过；待部署 |
| SR-STRICT | 严格修复队列不能由普通进展假结算 -> verify: human/business/Run完成不能消除严格pending，legacy行为保持。 | 严格计数修复及独立review通过；显式disposition入口仍待实施 |
| SR-DISPOSITION | 明确关闭已被有效替代的失败候选 -> verify: 真实ControlledActionService/RM入口，旧失败保留、原案卷不变，陌生/过期/未应用替代与门禁拒绝。 | c7e403ac已提交，341直接闭包、7143自动闭包通过；独立review问题已修，待部署 |
| SR-POLICY | 投影不把路径风险建议冒充审批门 -> verify: medium未知审批需求，实际受控apply策略保持。 | c7e403ac已验证提交，实际gate未改，待部署 |
| SR-BOOT | 区分历史重启证明与当前版本可恢复性 -> verify: 后续升级不把已证明重启误判未知，失效/缺失/merge前证据不采信，当前不匹配仍不自动resume。 | c7e403ac已验证提交，当前不匹配仍拒绝resume，待部署 |
| QUAL | 新窗口验证 Worker 失败、控制器重启与 Reader 缺证恢复 -> verify: 同 Run 真实 8 Impl + 8 Task Verify 重叠，16 项独立验收、Candidate、Docker 浏览器、Judge、Goal、本地交付完整，零人工恢复。 | 前置条件未满足，不宣称通过 |

当前 Run 已有人工修复/部署，原无监工资格失败永久保留。新配置不允许 Agent 扩大授权，不将 `enabled` 或单条测试通过视为无监工完成。测试按影响闭包、sentinel、相关 mock E2E 执行；实现和实测记录提交，未满足验收保持 active。

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
