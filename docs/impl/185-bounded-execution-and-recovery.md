# 185 续批：有界工件执行、规划绑定与恢复隔离

> 状态：部分完成；真实 Codex 有界交付及同 Provider 自动恢复通过，标准 Issue 完整交付通过，PRD 仍在验收，不表示 185 全部签收。

承接 [185 设计](../design/185-agent-routing-delegation-and-workflow-recovery.md) 与
[上一批逐分支记录](185-routing-admission-validation.md)。工作目录为 `184`，基线
`6682ff90`。用户要求先完成未完成项，再做真实 E2E；模拟进程测试与真实模型测试分别记录。

## 有界单执行者

`bounded-agent-v1` 是注册的工件交付模板。编译后仍由现有 Generic Workflow Kernel
维护 operation、attempt、依赖、预算和结果接纳；没有第二套 Task 状态机。

```text
Kanban Task / Channel 当前 PRD + Task
  -> Agent 从有效目录提出 agent:single:agent-execute
  -> 精确启动批准（Task、来源、配置摘要）
  -> Workflow Request / Run / blocking result protocol
  -> agent-execute：一个有界执行者产生报告
  -> 必需产物 + 当前 attempt 的授权提交
  -> agent-verify：不同角色独立验证
  -> 已接纳结果 + Goal completion gate
  -> Task 完成；失败/取消不产生完成态
```

可用示例：[bounded-agent-v1.yaml](../../examples/prod/controller/bounded-agent-v1.yaml)。
示例存在不代表当前项目启用；通过配置候选校验、批准和受控 apply 后，重新预览启动。
Workflow Synthesis 支持 `intent=bounded_execution`，参数为 `executor_role`、
`verifier_role`、可选 `deadline_seconds`（1..3600，默认600）。

两个阶段各只有一个角色，不能附加 fanout 或 synth 聚合角色；每个允许的执行 profile
必须是 `direct` / `single`，具有时间、token 和 usage sample 上限。所有角色必须显式
`provider_session.native_delegation: false`。Codex 启动禁用 `multi_agent` 和
`multi_agent_v2`；Claude 的 tmux 与 stream-json 均禁用 `Agent`、`Task` 工具。

这是一条报告/工件执行路线，不能声明源码交付，也不把一个 lane 当作单 Agent。
Provider 原生工具禁用不等于操作系统沙箱；源码写入仍受既有 scope/结果合同约束。
现阶段不开放未验证的 `native_delegation: true`；运行前 preflight 明确拒绝。

## Task Map 后复核

现有 `plan-execution-review` 增加 Task Map 内容摘要、有效执行策略摘要与请求/计划代际，
并报告配置 writer 实例、依赖层宽度、共享写入范围检查。仍通过不可变 sidecar 进入
Planner/Critic 的正式输入，不产生难度分数或自动选路规则。

Agent 在已有评审工件中引用这些绑定并解释是否沿用配置。需要改变权限、预算、拓扑
或验收时，提交候选差异并走现有受控修订；不得修改运行中 YAML 静默换图。依赖层宽度
不代表可并行性或实时空闲容量；配置不足的语义判断由 Agent 负责。

Planner briefing 同时列出当前 profile 所要求的 Plan 产物，避免只在提交末尾发现缺口。
保留现有完整工件和 `result validate` 门禁，不因节约输入而省略必需来源。

## 恢复修复与真实失败回归

- 旧进程终止异常、仍存活或存活状态未知时，禁止启动替代进程。必须终止调用成功且
  liveness 为 false 才允许恢复；失败回合的健康状态不能替代停止确认。Claude resume
  失败后改开新会话，也必须先终止前一个启动实例。
- 冷却、熔断或其他恢复持有锁不视为恢复完成，不向这些状态注入续跑指令。
- 受控执行路线切换要求新 Provider 会话，继续使用当前 State Packet/产物恢复上下文；
  不把跨 Provider 会话历史当成恢复依据。
- 从上一批真实 Issue 日志复现 `test_path_prefix: ./` 误拒绝根目录测试文件的问题，
  修正路径规范化。没有扩大预算或减少原验收条款。

## Kimi root canary

增加显式后端 `kimi-headless-canary`，接入现有 Web headless caller。可使用
`ZF_KIMI_CANARY_CMD` 指向本机 CLI；默认命令 `kimi`。仅接受 `read_only` 新文本回合，
通过自定义 profile 的 `tools: []` / `subagents: []` 禁用工具和子 Agent。

仅根回复、零退出码不足以证明产品完成：遇到工具活动、结构化错误、缺少回复或
后台未收敛诊断时拒绝。限制总时间，超时/取消后清理自有进程组。写权限、resume、图片
和思考等级覆盖暂不准入。usage 未提供时明确标记不可用，不计作零成本实测。

CLI 契约来自本机 `~/workspace/kimi-code` 的 `commands.ts`、`run-v2-print.ts`、
`prompt-render.ts` 与自定义 agent profile loader。无工具能力的真实版本签收仍需
可用 CLI/模型/认证；不因此升级 ZaoFu 的 native delegation 能力声明。该 canary
不是交付 Worker，也不直接改变 Task 状态。

## 验证与残留

本批证据目录：`/home/min/workspace/public/zaofu-evidence/185-continuation-20261023T164153Z/`。
恢复错误有6项 RED复现；根目录测试前缀有3项RED复现。后续 GREEN、影响闭包、mock E2E
与真实层的最终结果在收尾时登记。185-02/03/05/06/07/08 在各自验收完成前保持 active。

## 真实运行前的验证冻结

- `tests/test_generic_workflow.py`、config/schema/loader、start/catalog/policy/consumer、Channel批准、instruction/skill 及两个 Generic/Policy mock E2E：696 passed、8 skipped。跳过项保持 pytest 原标记，不记作通过。
- synthesis、Plan review/preflight、reader fanout 与执行路线：235 passed。
- 抽出 SDK options 与共享 catalog contract 后的 transport/synthesis/recovery/route caller：83 passed。
- 恢复与 Provider 影响闭包：196 passed；恢复、预算与 resume 补充：61 passed。
- 根目录测试前缀回归及 Plan caller：99 passed。各组重叠，不相加作为唯一测试数。
- `dev-verify.py plan --base dev` 对整条历史分支选出732个测试文件并升级全量。此次采用上述领域闭包，`run --base dev --tests tests/test_bounded_agent.py --tests tests/test_kimi_headless.py --tests tests/test_recovery_quiescence.py --skip-tier deterministic --skip-tier serial` 执行其余自动层。没有修改选择器或声称全量通过。
- 初次 sentinel 发现两个旧模块越过1000行上限，已按职责抽出 `stream_json_options.py` 和共享目录契约；重新 sentinel 51 passed。保留第一次失败日志。

有界路线当前沿用受信任本机 Worker 的权限基线；`native_delegation: false` 禁用 Provider 原生助手工具，但不是任意 shell 的操作系统沙箱。Kimi 的无工具 root canary 与此执行路线不同。

最终入口回归125 passed；停止确认调用闭包65 passed。Web typecheck/unit、51项sentinel、10项flow smoke通过。上述均非真实 Provider 验收。

## 第一轮真实失败与最小修复

基线 `4bee9d93`，Codex CLI 0.163.0、gpt-6-astra/high；隔离目录
`/tmp/zf-185-continuation-20261023T170000Z`。

- Issue `TASK-1BCCC0`：建单、精确启动批准和幂等重放通过。按需角色终端收到文本，但没有
  user_prompt_submit/session 证据；600秒 operation 上限阻断。未人工补Enter、未重复发送。
- 有界报告 `TASK-B0417F`：真实语义选择 `agent:single:agent-execute` 并经批准派发。执行者
  读取来源后，在188秒、6个usage samples、124640累计tokens时超过100000上限，被Kernel停止。
  未形成通过的独立验收。缓存输入tokens仍计入既定预算，不改预算算法。
- Channel PRD生成与Owner审阅通过，完整保留业务、CLI、错误行为、五文件范围与验证命令；
  非目标仅网页/HTTP/外部服务/第三方依赖。Task `TASK-AE15EE` 独立启动批准通过，执行待下一版本。

两项失败驱动以下改进：

1. Codex任务输入采用唯一命名tmux buffer、bracketed paste（`-p -r`）和一次Enter；不增加
   提交重试或延时。实际tmux/PTY探针验证Unicode、换行与单次提交字节完整；这不是模型验收。
2. `zf artifact read --attempt <id> --required` 在一次CLI调用中读取Kernel声明的全部必需片段，
   每项仍经角色凭证、路径权限和摘要检查，分别记录read ledger，不删除必读正文或放宽完成门禁。
   禁止同时传入source/artifact/截断覆盖。briefing推荐批读，保留逐项命令用于失败或输出截断时补读。

改进的CLI/读取安全、tmux、reader实际caller和bounded mock E2E闭包：237 passed；sentinel51 passed。
第一轮真实失败及浏览器环境失败均保留原始日志，不能用后续成功覆盖。第二轮仍使用原任务预算和验收。

补查发现bounded执行者原先只提供来源manifest、没有必读策略；现已在operation注册前绑定全部批准来源，完整报告缺少读取证据同样拒绝。批读同时支持事件中的任务派发策略和reader operation的不可变policy sidecar。补充实际caller/门禁回归56 passed。

## 第二轮入口契约回归

冻结代码 `efd235c5` 后，Issue 与有界报告的真实 Kanban 提案均使用
`contract.objective`，在建单前被 `contract.behavior` 必填检查拒绝，未产生执行副作用。
主 Agent 提示已有规范字段；现于既有提案归一化边界兼容非空文本 objective，
仅在 behavior 为空时填入，已有 behavior 优先、冲突内容保留 notes，非文本仍拒绝。
转换发生在提案持久化／摘要／批准之前，不添加第二种 Task schema，不批准失败提案。

浏览器使用 Docker Playwright、隔离 API 状态与本轮生产构建，4 项实际浏览器案例通过
（`browser-r9.log`）；覆盖启动回执和提案页面，不代替 Provider 交付验收。

字段兼容回归：26 项定向、包含该组的 Web/headless 闭包 90 项通过；sentinel 51 项通过。

第二轮真实执行仍失败：Issue `TASK-E9AFD8` 在 230.663 秒、15 个 usage samples 达到
1016193/1000000 tokens；bounded `TASK-796E86` 在 122.755 秒、4 个 samples 达到
101544/100000 tokens。两者均由预算保护阻断，未交付；计划中的 worker 故障注入因进程
先退出而没有执行，不算恢复验证。两轮失败不可被新预算下的结果覆盖。

已将基于实测的预算可行性复核补到选路 skill 和主 Agent 实际提示，要求考虑缓存输入、
工具回合、结果提交及独立 Verify。当前 bounded 实测每回合约2.5万tokens，原10万上限
不足以支持完整工件提交。另开隔离配置修订案例验证有界预算变更、批准、消费者加载与新启动；
不修改旧 Run、计量公式、原验收或完成门禁。

Provider 环境检查：Claude `loggedIn=false`；Kimi CLI 不存在。跨 Provider 与 Kimi 真实验证
缺少运行环境，证据 `provider-environment.json`；保留未签收状态。

预算方法与 bounded 合同回归18项通过，主 Agent headless caller回归64项通过，skill三份一致。
PRD双扫描被接纳；Planner在601.79秒、929782 tokens时达到600秒上限，Run阻断，未进入写入。

## 配置适配后的独立复测

新隔离根目录 `/tmp/zf-185-adaptive-20261023T180000Z`；执行源代码 `89272260`。
这不是原预算下的回归通过，旧失败及 oracle 非零退出码保存在原证据目录。
所有候选经真实 intake/confirm、不可变配置提案、preflight、HTTP `workflow-config-apply`
和 CAS 后，由新消费者加载，再经真实 Kanban/Channel 入口及精确启动批准。
重复 apply 返回同一事件/回执，启动批准重放只产生一次 invoke。

- bounded：原每 operation 100000 tokens 调为500000，Run250000调为1000000；
  600秒、30samples及1500秒Run上限不变。依据是每回合约25000 tokens及提交/独立验证成本。
- Issue：light候选被 `productAcceptanceMode: blocking` 拒绝，保留fanout/standard与全部门禁。
  每 operation1000000调为2000000，Run3000000调为6000000；时间、采样与费用上限不变。
- PRD：保留标准路线、五文件范围与全部验收；新PRD明确11项强制验收，原需求完整保留。
  reasoning effort high调为medium，operation600秒/1000000 tokens调为1200秒/2000000；
  Run1800秒/3000000调为7200秒/12000000；费用上限不变。

PRD旧轮次写入拒绝的顺序审计：混合读写脚本在running时因涉及共享根目录拒绝；
后续本地规划工件的apply_patch发生在预算终止之后。operation已正确绑定规划工件路径；
终止后拒绝写入正确，因此没有放宽scope guard。证据 `prd-guard-order-audit.json`。

Issue新轮次于18:11:47 UTC真实关闭triage pane，旧PID505647消失。监控记录dead_observed，
但角色stuck阈值900秒大于operation600秒上限。受控CLI重启与自动恢复分开记录；
不把人工触发的接续标成自动恢复通过。


## 消失 pane 的恢复修复

真实故障注入后的受控 `zf restart issue-triage` 失败：pane 已消失，pane-grid 的归属检查
拒绝终止，导致无法进入接续。新实现仅在成功列举该会话所有pane、确认旧pane、角色身份及
已知工作目录均不存在且共享角色窗口不存在无身份pane时，将终止视为幂等成功。
查询错误、空/畸形结果、旧绑定指向另一角色或工作目录身份不明仍拒绝，不杀其他角色。
这证明tmux管理的slot消失，不声称提供任意脱离会话的OS子进程沙箱。

配置适配轮仍未完成交付：bounded执行者工件被真实接纳，独立验证者11回合502223/500000 tokens
阻断；Issue退出后自动等待阈值过长、手动重启又遇到上述pane缺陷，最终wall-clock阻断。
后续恢复案例将验证者配置为单独1000000 tokens profile、Run1500000，executor维持500000，
使用medium及60秒活性阈值；原600秒/30samples/独立验收保持，不修改失败Run。

本修复定向91项通过；扩展CLI调用者暴露dry-run终止后仍报告存活及未启动slot终止问题，
已在模拟transport/layout状态中修复，不绕过真实停止确认。最终相关CLI/transport/skill回归65项通过，
真实tmux 3项通过（33项非host未选），sentinel51项通过；此前6项及1项失败日志保留。

## 有界工件真实交付通过，恢复单独验收

执行代码 `36832cdc`，真实 Kanban Task `TASK-6E4E2C` / Run
`workflow-58f22117f8de105d` 于18:36:14 UTC通过Kernel完成门禁，规范TaskStore状态为done。
真实Codex执行者提交报告，另一独立Codex验证者读取全部必需证据后签收；外部算术复核与人工
逐项审阅确认原序列、排序、数量4、总和6、均值/中位数1.5、计算步骤和空序列说明符合需求。
重复启动批准没有重复invoke，执行者没有自评完成。证据位于续批目录
`recovery/bounded/{terminal-evidence,external-report-check}.json`。

本轮原计划关闭验证者pane，但测试脚本错误地按独立worktree路径定位共用项目目录的reader，
在注入前抛出StopIteration；没有真实注入故障，**只算正常交付通过，不算恢复通过**。
修正测试脚本为按隔离会话内的角色标识和项目路径共同定位，再以相同配置/预算/需求重跑。
原报告、失败注入日志及Run均保留。最终有界执行/恢复确定性E2E为8 passed
（`recovery-mock-final.log`）；不冒充真实恢复。

Kimi实际HTTP缺环境案例返回`kimi-headless-canary command is unavailable`，没有创建Task、
启动Workflow或静默fallback；证据`kimi-missing/result.json`。这仅证明缺CLI负分支，
不代替Kimi模型正向、native子树或跨Provider接续验证。

## PRD 产品验收规格的完整字段提示

适配后的PRD `TASK-34EFCB` 双扫描通过，Planner生成保留11项AC的三任务串行计划，但仍在
740.602秒、24个usage samples、2073256/2000000 tokens处阻断，未进入Critic/实施。
其中1962880为缓存读取tokens，仍按既定计量累计。实际预检先报告旅程缺少title，修复后又
报告断言缺少assertion_id/observation_kind；原提示只列概念，未完整列出嵌套机器字段。
证据`adaptive/prd/validation-errors.json`保留两次实际诊断，不把已写计划当作交付。

已在实际Plan派发/重试提示中补一个完整形状示例，明示必须换成当前需求语义、不得作为证据；
canonical `zf-prd-plan-synth` skill及两份分发同步列出旅程/断言的全部必填字段和枚举。
没有改变schema、必读、预算、独立Critic或验收规则。实际briefing的示例经Kernel身份绑定后
交由现有产品验收validator验证：先RED复现，30项定向及31项Plan/triage/PRD/refactor
调用者闭包通过。预算失败保留；后续真实运行才能证明本改进的交付效果。

## 真实 Codex 自动接续通过

隔离重跑`TASK-4FFC3A` / `workflow-38a9bec9915ed6ec`，配置、需求和预算与正常完成案例相同。
18:46:51 UTC实际关闭bounded-verifier pane `%102`，旧PID553666消失；监控18:47:02记录
dead_observed，watchdog于18:48:00自动respawn，新pane `%103`/PID559661接续。
未运行手动restart。执行者只派发/接纳一次；验证者派发两次，operation身份保持同一个。
18:53:45验证结果admitted，18:53:50 Kernel完成门禁通过，规范TaskStore为done；没有预算阻断。
外部算术复核及完整报告人工审阅通过。证据`recovery-r2/bounded/recovery-verdict.json`
与`terminal-evidence.json`，包括故障、旧PID消失、因果顺序、唯一执行者和最终状态断言。

这是同Provider会话/operation接续，不是跨Provider或v4新attempt redrive的实测。
运行进程启动于36832cdc；期间094f39e3仅修Plan authoring提示及测试/文档，bounded执行、
结果接纳和恢复实现未变，最终CLI source HEAD与此版本边界已记入证据。

## 真实 Issue 候选与 PRD 审批证据缺口

相同预算/需求的新运行中，Issue `TASK-6B6CE8` 完成 triage、Critic、实现，并进入独立 Verify。
候选 `7fb54223a8503a656ead79aa28e3cbb00c849609` 的外部五项行为断言通过；但19:25:39 UTC
整条Run触发1800秒wall-clock限制，Task仍blocked，不能视为完整交付。Docker真实浏览器
19:32:38确认根Task blocked、子任务testing，无页面错误；仅证明真实投影，不替代验收。

PRD `TASK-BC14CC` 的双扫描和Planner通过，完整嵌套产品验收字段缺失没有再次出现；两任务
串行覆盖10项AC和五个产品文件，CLI任务明确承担最终装配。独立Critic虽然读取了全部来源，
其提交仍保留 `Short synthesis summary.`，模板预填passed/approve。操作员没有批准实现，
保留该证据并准备受控驳回、重审。旧的plan digest按根目录多owner/缺assembly分类报警，
实际计划为单lane串行、文件所有权无重叠，不以压掉提示为理由修改任务语义。

由真实失败驱动的修复：

- 实际Synth briefing给出已校验的绝对scratch路径，保留agent已有编辑和绑定字段。
- 签名Synth模板不预填成功结论；validate及submit都要求report的status/recommendation/summary
  为非空字符串，直接submit也不能绕过。`abstain`不再归一化为passed。语义质量仍由独立评审负责。
- 高频 `hook-recv/artifact/result/guard` CLI仅加载对应命令注册，根help/其他命令保留完整解析器。
  同机各一次冷启动help实测从6.49–6.51秒降至1.21–1.41秒；这是局部启动测量，不是交付SLA。

证据：续批目录 `shape-rerun/issue/{terminal-evidence,candidate-oracle}.json`、
`shape-rerun/prd/plan-review-before-reject.json`、`cli-hot-benchmark.json`。
PRD在待审批、无writer运行时以SIGINT停止watcher，后续从原状态受控启动；新旧代码边界需单独记录，
不能把整轮称为冻结在094f39e3上的无干预恢复。

本轮修复验证：`critic-cli-focused.log` 62 passed、`critic-cli-closure.log` 94 passed、
`critic-runtime-closure.log` 173 passed；预检职责移至已有Plan验证模块后
`critic-preflight-final.log` 59 passed、`critic-cli-sentinel-final.log` 51 passed。
`critic-cli-mock-e2e.log` 14 passed覆盖计划审批、策略准入及有界执行；这些集合有重叠，不相加
当作唯一用例数。完整pytest/coverage未运行。首次sentinel因result_submit超过1000行失败，
已按职责拆分，未增加豁免或修改结构阈值。

独立Verify在19:24:17试图新增`.verification-evidence/counter-review.json`，被本任务仅允许
`counter.py/test_counter.py`的scope守卫拒绝；不因此扩大源码写权限。验证角色skill补充现有
`zf events`查询工具结果、核对actor/Run/派发因果并引用真实event或raw-output sidecar的方法，
报告仍写入Kernel授权的结果scratch。缺证据必须报告缺失，不能虚构引用。canonical及两份
分发一致；75项技能/实际注入回归通过，1项因可选外部技能夹具缺失跳过。该方法在新Verify
派发前提交，是否改善实际验收仍以新运行记录为准。

## Task Map 后真实复核与产品验收边界

PRD返修Critic `evt-af4d646ec77d` 明确评审需求覆盖、依赖、CLI装配、验证阶段和残余风险，
引用Task Map/有效策略摘要；两任务串行、writer数量1且无写冲突，语义结论为沿用配置。
测试操作者核对新旧任务、goal claims、requirement coverage完全一致后，通过
`zf plan approve evt-f0f4fb212ae9`批准实施；此前占位裁决已经正式驳回。
这是B18的沿用配置分支，不是Task Map后变更冻结配置的真实证明。

实际`zf plan inspect-execution --plan-package ...`原输出漏带包内Run/plan revision/generation；
已RED复现并将已校验包身份传给既有review函数。不编造包内没有的字段，仍明确
`config_scope=current_file_not_frozen_run`，不能把当前磁盘配置冒充Run快照。
真实包复测输出保留`workflow-1afb5d0f75ff7297`与generation
`a1cf20529986aadd86e8c42485efc56602ff721c120477a687dbb176c4886b26`。
包/Task Map不匹配仍拒绝。36项CLI/包/产品验收/技能闭包通过；证据为
`post-plan-cli-{red,green}.log`及`shape-rerun/prd/post-plan-inspection{,-bound}.json`。

复核前轮v3 Verify模板中的task_verify身份后，确认既有产品验收绑定按operation profile
判定candidate义务，任务级owner不绕过完整产品报告门禁；未改成另一套候选authority。
新增回归证明两个owner下都拒绝缺报告/旧target，补充技能解释任务行not_applicable
不能豁免mandatory产品旅程。12项定向验证通过（与36项有重叠）。
新Issue Verify已实际物化含工具结果引用及candidate义务的最新技能，摘要见
`critic-rerun/issue/verify-method-materialized.json`；运行时实现保持58db64a2，技能内容在
Verify派发前已更新并记录hash，不称整轮冻结于单一代码/技能提交。

本次 CLI 身份修复及 legacy candidate-verify 边界的自动闭包：`dev-verify.py run --base dev --paths <本次六个源码/测试/技能路径> --tests tests/test_plan_artifact_package.py --workers 2`，8 个测试文件，81 passed / 1 skipped（外部可选技能源缺失）。此前 RED 1 项、定向 GREEN 36 项、候选 owner 12 项均保留在 `post-plan-*` / `candidate-owner-boundary.log`；没有执行 full pytest 或覆盖率验收。

## 验证取证查询收敛

真实Issue Verify读取工具历史时遇到约97k tokens的嵌套查询输出。已有EventLog支持actor/task
筛选，但CLI未暴露，Agent只能先输出大量历史再自行过滤。现在`zf events`增加`--actor`、
`--task`和`--id`，均精确匹配且先筛选再取`--last`；原输出envelope及完整事件正文不变，
不存在或与其他条件冲突的ID返回空集合，不伪造证据。方法skill建议优先定位原始工具事件，
避免递归读回查询自身。CLI RED复现后，55 passed / 1 skipped的查询/JSON/技能闭包通过。
这不改变权限、Run状态或验收结论，也不保证单条被查询事件本身没有大payload。

当前独立候选检查：Issue `ae671604` 6项通过；PRD `8691ff47` 65项通过，涵盖API/真实CLI、
错误分支、输入不变和原范围。已审阅所有产品源码、测试与README。检查脚本摘要在候选完成前
登记，外部报告不进入运行时裁决；Issue Verify已admitted，后置discovery和PRD验收仍在运行。
证据：续批`{critic-rerun/issue,shape-rerun/prd}/complete-oracle`、`manual-source-review.json`。

事件查询补充：24项CLI主入口/trace/EventLog读取回归通过；真实CLI对`evt-44ab90920ccb`
按actor+ID精确查询返回1738 bytes完整原事件。自动planner因main注册依赖选出103文件，
建议broad deterministic及无关锁serial；按仓库禁止仅按数量触发full suite规则显式跳过，
采用上述55+24项定向闭包（并非唯一总数），`event-query-verify-receipt.json`记录跳过层。

## 标准 Issue 真实完整交付通过

`TASK-5577E6` / `workflow-9f80eb3ff1e0d2ac` 经真实Kanban Agent、HTTP Preview/Approve，
进入triage → 独立Critic → writer → Verify → discovery → Judge。20:10:30 UTC
Kernel记录`run.goal.completed`；20:11:13终态消费后根Task和子Task都为done，期间约44秒
事件消费延迟如实保留。Judge闭合10项mandatory声明，Product Acceptance四项断言通过。

运行保持初始1800秒/6M tokens/$30及operation600秒/2M tokens/60 samples限制，无人工Enter、
重复发送、跳过门禁或执行中加预算。重复批准仅一个`workflow.invoke.requested`；五个发布
native prompt hook的角色各接收一次（discovery没有该hook，不编造收据）。
运行时58db64a2；验证skill及只读CLI后续变更已按实际派发/调用记录，不冒充整轮单提交冻结。

候选`ae671604`经Kernel ship交付到测试项目main `52bff247`。候选与交付后独立需求探针均
6项通过；Docker真实浏览器20:11:39确认两个Task done、无page error、无mock路由。
首次Docker命令误用了镜像默认entrypoint，属于测试驱动错误；改用`--entrypoint node`后
通过，失败日志保留。

`simulation.done`已通过EventWriter记录；watcher/Web/tmux与项目cwd进程均停止。549个
脱敏归档成员逐一校验摘要后清理自有临时根。证据为续批`critic-rerun/issue/`下的
`terminal-evidence.json`、`native-input-audit.json`、`{complete,shipped}-oracle/result.json`、
`browser-live/live-board-result.json`、`archive-manifest.json`、`cleanup.json`。
这证明标准Issue链路；不代替PRD、light、Refactor或v4真实交付。

## PRD 第一次候选完整、Run 预算阻断

`TASK-BC14CC`的第一项Verify于20:11:53 admitted；第二项仍在整理结果时，20:18:03
整条Run达到12,155,516 / 12,000,000 tokens，20:18:05记录blocked。累计4727秒、121条
usage samples，含11,387,520 cache-read tokens；不能去掉缓存用量绕过冻结预算。费用记录
均unpriced，显示0不表示真实免费。两个writer及候选装配通过、外部65项检查通过，不替代
未完成的第二Verify/discovery/Judge。20:20:10 Docker真实看板确认根Task blocked、两子任务testing。

该轮保留旧版本占位裁决、受控驳回和重规划的完整证据。现另建隔离项目，以bb986afb版本
重跑相同原始需求，维持Run7200秒/12M tokens及operation1200秒/2M tokens/60 samples，
不改旧Run、不降低AC、不跳过后置阶段。新轮`current-prd/prd/freeze.json`记录四个seed文件
和配置摘要；当前只是启动验证，不是交付通过。旧轮详细结果与归档在`shape-rerun/prd/`。
