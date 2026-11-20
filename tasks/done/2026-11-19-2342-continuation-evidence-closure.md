> 状态: done — 9f4e7ee4 `fix: preserve recovery after pre-send rejection and parent progress`

# 冻结候选续跑：证据接力与恢复闭环

用户已批准上一轮复盘的修复及真实断点恢复。基线 e122a6b9，隔离分支
`wip/codex-20261120-continuation-evidence`。现场为 K7 Discovery 弃权，
16 Task 和 8 Candidate Reader 已通过；不重新执行已完成 Plan/Impl。

## 本批验收

- C01 历史证据接力 → verify: 实际 Reader 输入解析器及 Goal 消费者都取得授权
  continuation、原 Task Verify/Impl 原始结果与引用的工具事实；保留原 Run/目标，
  错摘要、无授权、错候选拒绝；来源缺失显式保留，不能由计数生成通过结论。
- C02 恢复语义 → verify: 证据不可用与执行错误保持区别；诊断知道当前可用能力，
  不把关闭的 reflect/repair 建议成可执行动作；既有暂停、人工批准及结果门禁保留。
- C03 端到端接力 → verify: mock 覆盖 Candidate 之后的 Discovery 输入及 Goal
  历史来源；真实从原 Discovery 断点继续，观察 Judge/Goal/本地交付或记录并修复
  新的具体阻碍。产品交付与无人资格分别评估，原人工恢复历史不得抹掉。

本 session 负责监工、独立核对和缺口记录；真实 provider 已获用户授权，先 mock。
本批不 push、不测试 Go、不直接改写事件/Task/Run 真值。代码变更记录沿
`docs/impl/unattended-progress-recovery.md`，运行证据放 reports。

## 实施与验证进度

C01/C02 已实施。实际 Reader 派发输入缺失与诊断能力表缺失均 RED→GREEN；
新增损坏证据不能吞掉诊断请求、Goal 消费同源证明、受控范围说明的反例。
真实账本预检找到16 Verify/8 Impl以及原派发/hook，新增来源约276KB；仅物化
不可变证据侧车，尚未改变产品状态或派发真实 provider。

dev-verify --base dev 选中775个测试文件，确定性层9208 passed/10 skipped/1 failed。
唯一失败在基线e122a6b9也复现：prompt-response测试使用默认关闭的resident却期望
observing。为该正例显式启用resident，保留关闭优先规则；相关调用方113 passed。
随后按同一验证计划补跑其余tiers，保留首跑失败记录，不把它重写成一次全绿。
记录位于主工作区 reports/continuation-evidence-20261120。

C03 断点预检确认只触发flow.discovery.requested，Run/候选/代际不变；真实
加载及Judge/Goal/交付仍待验证，因此本任务保持active。

3021c89e 已提交并实际加载；同一候选的 Discovery 已恢复。Reader 两轮只读
部分材料后提前退出，Run Manager 两次自行接回，未新增人工派发；新增提示补丁
明确同轮读取到完成或具体阻塞，实际派发回归先 RED，再修复。该补丁的 provider
效果和最终 Judge/Goal 仍待观察。历史人工恢复计数 17，不声明无人资格通过。

提示增量：基于已验证3021c89e重新规划18个影响测试文件，无缺失映射；420 passed。
另有针对性37 passed、premerge 53 passed、mock flow 10 passed（计数重叠不相加）。
未改变Worker的当前输入或结果门禁；仅后续自然续读派发采用新提示。

现场进一步发现本批27条工具记录均被错误筛为unavailable：PostToolUse本来没有
kernel origin；且19条TDD记录属于前序已准入但blocked的attempt。复用既有工具
事实捕获器，沿明确引用及原派发因果链接回前序观察，保留blocked语义和原身份；
补充原始输出侧车校验、错Run/角色/目录/attempt、未准入及损坏输出反例。
00:51:51控制器进入本session维护，Reader继续完成原102项输入；原manifest不改写。
此维护计为第19批人工介入。新源码和后续实际阶段仍待加载验证。

现场复核最终为21条原生工具观察、5条受控宿主验证事实可读；另1条rework控制
事件明确不是执行输出，不虚构缺失的测试证据。针对性39 passed；最终影响闭包
1453 passed，premerge53及mock flow10 passed。原failed预检和中间8/27、21/27
记录均保留在reports。Discovery已读完102/102，提交因缺product_acceptance_report
进入结果repair_pending（operation投影仍running），该后续问题继续处理，任务不归档。

C11 实际加载 2b6f4c05，102/102 读取已完成；发现纠错错走首次派发，引发
TaskAttemptDeliveryClaimedError。C12 将纠错绑定到当前 sent attempt 并记录
发送 claim，未知发送不盲目重试；保留真实发送入口的预算、身份、存活与租约门禁。
Discovery 增加必需产品报告模板、同源只读 preflight 和报告 ref/digest 指引。
广覆盖首跑 4063 passed/4 failed，其中旧事件派发 ID 兼容缺口已修复，另三项是
测试 PATH 缺 python；修正入口后的失败项及新增反例 28 passed，最终相关完整模块
233 passed。首轮失败保留。实际纠错送达、产品准入及后续阶段仍待观察。

2c5dee61 已实际加载。01:28:22 Discovery 正式准入，01:28:40 自动进入
Judge，原 Run/候选/16 Task 保留。边界复核发现预算发送前拒绝误留 unknown
导致恢复后不再发送，新增回归 RED→GREEN，明确未发送与未知发送保持区别。
现场又复现父 Task 历史 discriminator.failed 将新 Judge 误标 idle 并跳过
卡顿监测；活跃 fanout child 改按自身派发判断，普通 Task 完成识别保留。
相关63 passed；最终影响闭包3567 passed/7 skipped、premerge53、mock flow10
均通过。Judge 正在独立审阅90项输入，最终产品/Goal/交付仍未宣称通过。

## 最终验收（2026-11-20 02:24 UTC）

上述进度段保留发生时状态；本节记录最终结果。实现提交依次为：

- 3021c89e `fix: carry continuation evidence through downstream review`
- 4aa7f310 `fix: keep reader continuations working until bounded completion`
- 2b6f4c05 `fix: retain native and predecessor execution observations`
- 2c5dee61 `fix: resume result corrections on the existing reader attempt`
- 9f4e7ee4 `fix: preserve recovery after pre-send rejection and parent progress`

C01/C02 的确定性影响闭包、实际入口与反例验证已完成，见上文各批结果；
C03 的真实 provider 断点续跑也已完成：同一 Run、候选、代际保留16个已完成
业务Task及8个候选Reader。Discovery正式通过后自动进入Judge；Judge读完90/90
项输入，02:20:26正式准入evt-b085a8f519aa，随后自动完成本地交付
evt-e8a597475892与Goal终态evt-5620a1580edd（02:20:53）。没有重启Plan/Impl，
本session没有代写Judge结论、产品通过或Goal终态。

独立核对：19/19冻结产品声明closed；21个支持结果均已准入且摘要一致；
产品报告17条旅程、41项断言的引用可校验；原Docker回执绑定候选a84d24b4、
退出码0与稳定源码，PNG/trace/WebM及环境记录均通过摘要核对。本次没有重新
执行浏览器测试，没有将证据复核表述为新测试。测试产品本地dev交付08e22e54
的文件树与候选完全一致；这不是ZaoFu源码dev合并。活动fanout和未闭反馈均为0。

最终回执在主checkout的reports/continuation-evidence-20261120/：
review.md、goal-completion-receipt.json、final-evidence-audit.json、
final-browser-evidence-audit.json。终态之后通过既有simulation生命周期助手写入
evt-42eb04c5103b，再关闭本次临时控制器及其两个tmux session；运行证据和产品
保留供复盘，未删除worktree、未push、未merge ZaoFu dev。

本批完成范围是证据接力、恢复修复和获准的增量产品闭环。原轮严格8×8/无人
资格仍not_passed，21批历史人工维护保留；原始独立行为RED-before-GREEN与
同时并发资格不能由此次产品通过追认。另记录Judge材料约1 MB、至少167页的
审阅延迟，作为后续输入分层优化依据，不在已运行attempt内删减必读契约。
