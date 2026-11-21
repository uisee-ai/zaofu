# 自动源码修复闭环与无监工资格验证

> 状态: active

Owner 已批准按下一步执行：现有真实 Run 继续独立验收，同时核验并补齐源码修复链；条件满足后再开始新的零人工干预窗口。基线 a93fc04f，沿用隔离工作区。当前 120 次宿主采证授权保持，不合 dev、不 push。

| ID | 工作与验收 | 当前状态 |
|---|---|---|
| SR1 | 核对 RM 诊断、修复合同、隔离工作区、发布与独立验证实际调用链 -> verify: 覆盖真实入口的确定性测试，未知原因、越权、失效绑定保持拒绝。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR2 | 故障注入应用与重启边界，修复已复现问题 -> verify: 缺少重启前提不改源码；失败不假报已恢复；正确版本重绑后才恢复原检查点。 | 代码已提交部署，确定性验证通过；真实故障实验待完成 |
| SR3 | 配置受限的自动修复实验并验证 -> verify: 路径范围、合同/验证证据、授权和检查点均有效；模拟通过后才部署真实实验；完整保留失败与人工介入记录。 | 待 SR1/SR2 |
| LIVE | 既有 Run Reader → Judge → Goal → 本地交付 -> verify: 每阶段正式结果、同候选/目标/合同绑定，不将代码测试冒充真实交付。 | 本轮6/8通过，2项执行失败；19/19原采证可用，未增加宿主执行 |
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
