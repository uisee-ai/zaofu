# 185 续批：有界工件执行、规划绑定与恢复隔离

> 状态：部分完成；真实 Codex 有界交付、同 Provider 自动恢复、标准 Issue、General 完整交付通过。PRD 两轮候选功能通过但未交付：上一轮触发 Run 12M 上限；2026-10-24 轮第二个 Verify 触发 Operation 2M 上限。不表示 185 全部签收。

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

## Reader 会话边界（2026-10-23 续批）

上一轮 PRD 重规划使用原 Planner 会话，最终触发 operation token 上限。日志同时存在
多次完整工件读取和大块写入，不能把超额单独归因于会话复用。默认仍按 workflow 复用，
新增显式 `roles[].lifecycle.reader_context_scope: dispatch` 供新运行选择：

```yaml
roles:
  - name: planner
    role_kind: reader
    # 其余已注册角色字段省略；手写角色会完整覆盖同名 controller 角色。
    lifecycle:
      mode: on_demand
      preserve_session: true
      preserve_workdir: true
      reader_context_scope: dispatch
```

示例是已注册 reader 的配置片段，不是独立可运行配置。仅 on-demand reader 可选择
`dispatch`；其他生命周期或 writer 配置被拒绝。生成 lane 的 reader 可通过
`roleDefaults.lifecycleByStage.verify` 配置，不能在含 writer 的全局 lifecycle 默认值中
开启。`workflow` 是缺省值；`preserve_session` 仍适用于当前边界内的休眠恢复。

真实 fanout dispatchability 入口使用稳定 `run_id` 作为 dispatch 身份；没有 Task ID 的
synth 仍按 trace 绑定，缺失 dispatch 身份或 Task/trace 范围时拒绝派发。同一 dispatch
的协议修正和发送重试保留上下文，新 dispatch 等待旧 provider turn、child、task 全部
结算，再终止旧进程并复查停止结果，之后才能清理/轮换会话。旧进程仍存活或状态未知
时，保留旧绑定，不启动替代。旧记录缺少 dispatch 绑定时保守轮换一次。会话切换保留
工作目录，必需完整工件仍由已有输入合同约束；不以 shadow delta 替代权威输入。

这项配置进入有效执行策略摘要，改变后旧启动预览不能继续批准。需在新 Run 前选择，
或走已有配置修订及重新批准；禁止改运行中的冻结配置。它不等于源码 single-agent
接替、跨 Provider 恢复完成，也不保证预算内通过。真实收益要由新运行测量。

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

## PRD 第二次候选通过、独立评审前置证据缺失

`TASK-8E5850` / `workflow-dd76c5051933c94f` 保持相同需求与预算，两名writer
完成候选`5b516ae0`。外部65项产品检查、20项真实unittest及源码人工审阅通过；
Owner计划批准发生在两次writer之前。Verify发现G-01要求的独立PRD评审在Owner确认前
没有持久化记录，随后21:06:46 UTC达到单operation 2,047,702 / 2M tokens，
21:06:48 Kernel blocked。此次是operation上限，不能误记为12M Run上限。
Verify的语义阻断报告尚未admitted；最终机械阻断原因仍为预算。Docker实际看板确认
根Task blocked、两子Task testing。外部需求映射G-01已更正为unproven，不能以产品
65项通过替代流程证据，也不追补或伪造历史评审。完整证据在`current-prd/prd/`。

根因包含真实测试驱动漏步：系统已有`channel-prd-evaluate`受控入口，使用独立Codex
只读评估并绑定canonical版本；驱动此前直接从PRD生成进入Owner确认。新增显式
`evaluate-prd`阶段，必须在隔离项目`zf start`运行后执行，等待独立评审PASS、验证报告
摘要及当前PRD版本，仍停在Owner人工审阅之前；失败/不确定/过期/超时不可继续移交。
使用方式：`python tests/e2e/intake_live_validation.py evaluate-prd --root <isolated-root>
--harness <184-worktree> --evidence <evidence-root> --confirm-real`。
该层是opt-in测试驱动，不将所有业务PRD强制套用新的语义Kernel门禁。22项驱动/实际
reactor/恢复定向回归通过。相同seed与预算的`reviewed-prd`隔离轮将先完成独立评审，
其后仍须独立Owner确认、启动和Planner/Critic批准；未提前宣称该轮交付成功。

复核同轮scope guard拒绝：Verify使用Python heredoc写workdir外的合法scratch，
动态shell写入未获得原生Edit/Write/apply_patch的精确路径例外。保留安全门禁，
仅补验证技能明确使用原生工具更新已授权文件；canonical技能与两处分发副本同步。

新轮评审在调用模型前真实复现`original requirement source is unavailable`：文件PRD
入口没有discussion requirement_message_id，独立评审原先只支持讨论消息来源。现在沿
canonical版本的synthesis request取受控快照，校验channel/thread、kind和digest后读取
完整原文；不信任作者在PRD中自行声明的source_ref，不将目标草案混入盲评来源。
新增实际reactor调用者回归，RED为1失败/5通过，覆盖正常文件入口及缺失、篡改、
跨thread/scope、错误request。失败评审保持原记录，修复后经显式retry创建新评审。

该文件入口修复的最终自动闭包为15个测试文件，182 passed / 1 skipped（可选技能源缺失）；
另有共享来源/sidecar/output-evaluator/mock E2E 36项、sentinel 51项及技能30 passed /
1 skipped。各组有重叠，不累加为唯一总数。未执行full pytest或coverage。
真实重试`07ed74c0-138b-425a-99b8-014bd9ffed0d`于21:24:10 UTC产生
`output.evaluation.completed`（`evt-57c8f0b270ff`），34项来源标准全部PASS；
独立报告明确产品验证未执行，完整报告已审阅。该证据早于Owner确认和后续Workflow启动，
没有追补旧Run历史。来源、报告与canonical摘要记录在`reviewed-prd/prd/independent-review-audit.json`。

前置流程取证方法另行澄清：PRD独立评审和Owner确认可能早于Task创建，事件task_id为空；
按Task精确筛选后的空结果不能证明它们不存在。验证技能要求按绑定ID/ref或类型定位，
核对channel/thread、PRD版本、计划代际和时间顺序，保留不确定结论而不借用其他版本。
该技能在新轮Planner阶段补充，须记录Verify实际派发版本，不声称整轮冻结于c40c2239。

## 计划批准前的真实取证命令返修

新轮Planner完成、Critic建议approve，但Owner复核实测V-07的Task全量事件查询返回
673,209 bytes / 158条事件，缺少建单前的独立评审与Owner签字原事件。外部测量记录
`reviewed-prd/prd/plan-evidence-query-audit.json`；没有把正文中提到事件ID当作读取原始事件。
批准前通过现有`zf plan reject`要求仅将AC-09证据命令改成精确ID/有界类型查询，
同步矩阵并保留所有产品条款与预算，尚未派发writer。拒绝和返修不是模型输入重复发送。
Planner技能补充同类取证方法及原生文件写入说明，28 passed / 1 skipped的技能/注入
检查通过；不增加语义Kernel门禁。后续交付仍需返修计划、独立Critic和单独批准。

## 上一轮状态：PRD 重规划未交付（21:51 UTC）

最新`TASK-5CB69E` / `workflow-304fe8c8e9e23357`于21:51:48 UTC记录重规划
operation预算超限，21:51:50 blocked。384秒、10个新增usage samples共2,096,853
tokens / 2M；计量正确扣除会话基线1,476,431，不应把累计3,573,284误作本次新增量。
费用样本unpriced，不能将0USD解释为免费或已证明美元限额有效。重规划复用了原Provider
会话，尚不足以证明复用策略本身有缺陷；不能用忽略缓存tokens或放宽门禁制造通过。

独立PRD评审34项PASS、Owner确认、启动重放幂等、扫描、首版Planner/Critic及Owner
拒绝/重规划入口均真实发生；返修Planner确实读取了精确评审/签字事件和报告并核对摘要。
然而返修结果未admitted，Plan未批准、writer未派发、无candidate，不能运行或声称
本轮产品oracle通过。前两轮候选65项功能检查不替代这次未完成的流程验收。

最新失败证据：`reviewed-prd/prd/failure-audit.json`；重规划前实测取证缺口、Owner拒绝、
独立文档评审报告分别留存在同目录。`current-prd`和`shape-rerun/prd`的原失败记录保留。

| 真实场景 | 已证实结果 | 未涵盖的结论 |
| --- | --- | --- |
| 有界报告正常执行 | Task done，完整报告独立核对通过 | 非源码交付 |
| 验证者pane真实退出 | watchdog自动接续，同Provider独立验收后done，执行者未重做 | 非v4新attempt、非跨Provider |
| 标准Issue | 全链路至Kernel ship，根/子Task done，候选/交付后探针与Docker看板通过 | 非其他Flow的通过证明 |
| 文件PRD独立评审 | 真实Codex逐项34/34 PASS，版本和来源摘要绑定 | 仅文档评审，非产品通过 |
| PRD完整交付 | 保留三轮后续失败：Run token、Verify operation token、重规划operation token | 未签收，不归档交付任务 |
| Kimi缺环境 | 实际API明确拒绝，未建Task/invoke | 无正向Kimi/native/cross-provider证明 |

下一轮必须先解决有界重规划/验证的上下文与预算可行性：使用真实用量证据评估
State Packet、来源读取和受控会话接续，验证完整AC与原始来源不丢失，再做产品交付。
当前没有实现“所有复杂度在固定2M operation内必然完成”的保证，也未证明自动配置变更
能解决这一约束；相关18502/05/07/08继续active。18506的正向Kimi/Claude认证环境仍缺。
本轮不将这些项目写成done，不以继续重复相同实验替代明确的改进条件。

最新轮Docker真实浏览器在21:55:38 UTC确认仅根Task blocked、无page error、无mock。
原交付探针等待实施子Task超时exit1，失败保留；单独状态投影检查通过，显式
`product_delivery_passed=false`，两者没有混为成功。`simulation.done`已入账，
watcher/Web/tmux及项目cwd进程均停止；543个脱敏归档成员逐项校验摘要后清理临时根。
证据：`reviewed-prd/prd/browser-live/{probe-verdicts,live-board-result}.json`、
`terminal-evidence.json`、`archive-manifest.json`与`cleanup.json`。


## 2026-10-23 Reader 边界续批验证

证据目录：`/home/min/workspace/public/zaofu-evidence/185-context-20261023T225800Z/`。
停机确认缺口、新策略未实现、共享默认值别名和无Task synth范围分别保留RED日志。
修复后18文件闭包720 passed；最后无Task分支改动追加5文件调用者/Generic mock E2E
208 passed；最终sentinel51 passed、三流mock E2E 10 passed。测试组有交集，不累计为唯一总数。选择器因共享
配置模型保守扩展到597文件；显式领域闭包加自动sentinel/flow smoke，未运行full pytest。

新真实PRD由原四个seed文件重新初始化，不恢复旧失败Run。脱敏归档中的token_budget
字段不能作为可运行配置，数值从原freeze收据恢复并逐项比较；仍为Run 12M、operation
2M。有效角色配置逐字段diff只有Planner/Verify的reader_context_scope变化，模型、
角色合同、权限、预算、完整需求和五文件范围均保留。最终结果见下方新 PRD 记录，不能仅凭会话隔离签收预算收益。


## dispatch 上下文真实 General 验证

`e4548e95` 基线下，隔离 `TASK-8B42B7` / `workflow-1da42a7db86bc23a`
通过真实 headless 主 Agent → Task → 启动 Plan → 精确批准，重复批准仅一个 invoke。
使用已注册 `evidence-synthesis-v1`，不是临时拼接任意 YAML。scope 与 synthesize
复用 `context-reader`，两位 collector 与独立 verifier 分开，native delegation 禁用。
冻结 Run 2400秒/4M tokens、operation 600秒/1M tokens，运行中未改配置或源码。

五阶段均有 admitted result，2026-10-23 23:51:24 UTC Kernel 记录
`evt-a16e2af0ab52 run.goal.completed`，TaskStore 为 done。完整报告和独立验证已逐项读取：
A/B/合并的原序列、排序、数量、总和、均值、中位数正确，保留重复值、负数与零；
空序列均值/中位数未定义，来源引用及计算过程完整。独立 oracle 在运行前冻结，
未交给执行角色。Docker 真浏览器看板为 done，无 page error、无 mock route。

同 Task/Run 的 scope→synthesize 发出 `reader_dispatch_context_changed`，
旧 session `01a16b23-0238-7f62-931a-7bfa7df485e2` 与新 session
`01a16b28-d585-7161-bdd7-11dc20767912` 不同；新 Provider SessionStart 在回收记录之后。
已核对操作绑定、报告摘要、单次 invoke、配置及需求原始 hash。
外部 pane 快照采集于回收过程中，**未独立捕获旧进程 PID**，因此不将其描述为
OS 层旧进程证明；停止确认的拒绝路径另由确定性测试覆盖。
实际新会话仍误报 `is_resume=true`，该可观测性缺陷另有三项 RED 回归；运行结束后修正无缓存 UUID 分支及实际生命周期调用者，原始运行日志保留。

证据：`/home/min/workspace/public/zaofu-evidence/185-context-20261023T225800Z/general/`，
`acceptance.json`、`report.md`、`verification.md`、`browser-live/live-board-result.json`。
已通过 EventWriter 记录 simulation.done，停止自有 tmux/Web/watcher，382 个归档成员
逐项 hash 核对后删除隔离项目。归档脱敏且排除认证、缓存、git 和 provider home，不能用于续跑。
这项真实通过不覆盖 native 助手、跨 Provider、源码 single 接替或任意图生成能力。


## 尚未闭合能力的解决路径

以下是已批准185范围内的具体后续方案，不表示代码已实现或验收已通过。

| 缺口 | 实施边界与方案 | 必需验收 |
| --- | --- | --- |
| Task Map 后实际改变配置 | Agent 读取完整Task Map、有效策略、Provider能力及实际计量，输出保留/修改建议；需要修改时复用配置修订/CAS/consumer，生成新摘要并重新批准，禁止直接改运行中的zf.yaml | 真实触发一次配置差异；旧批准拒绝；新批准绑定新代际；执行者使用新配置，原AC和已完成产物保留 |
| 源码single执行与单阶段接替 | 在现有Task/Operation/Attempt中增加受限源码交付profile，复用writer worktree、候选快照、独立Verify与Kernel交付；Run Manager先确认旧进程停止，再交付State Packet和仅未完成阶段，不把整段Workflow改成无验收串行执行 | 真实写代码任务中断；已完成任务不重跑；旧结果拒绝；新attempt独立验证；候选与最终交付一致；取消和未知外部副作用不得自动重试 |
| 原生助手协作 | 先接Provider的root/child lineage、子树停止确认、强制权限、并发/深度/预算限制和唯一根结果，再开放native=true；当前不满足能力合同仍拒绝 | root退出、child残留、取消、超时、越权、重复结果及成本累加逐项故障注入；普通多角色Workflow不能替代这些证明 |
| Kimi/跨Provider | 先补可用CLI、模型与认证的canary，再核对只读工具强制、结构化输出与取消；跨Provider使用规范化恢复包和fresh session，不导入原生历史充当恢复 | Kimi正向真实调用、无工具/越权拒绝、进程组回收；Codex→另一Provider的实际接替与独立验收。本机缺Kimi CLI且Claude未认证，环境缺口与实现缺口分别记录 |
| 其余真实Flow | Refactor、light、v4与Research native分别冻结需求、预算、oracle及源码版本；每种保留自身审批、重工和交付条件 | 对应Flow真实终态、独立产品探针及投影检查；不能从Issue/PRD/General或mock结果推导全部通过 |

下列真实缺陷均在冻结运行结束后修复；未在运行中热改源码/技能，也未把旧失败改写为通过。

## 2026-10-24 新 PRD：候选功能通过，流程仍 blocked

冻结源码 `e4548e95`；`TASK-754624` / `workflow-37cd1d6725abd32d` 从原 seed
`e768783b` 启动，保留完整13项AC、五文件范围、Run 12M / operation 2M tokens。
独立真实 Codex 文档评审31/31通过，6项Owner决定确认后，才建单及批准单次启动。
Planner、独立Critic、精确Plan批准均发生；三次writer启动均晚于
`evt-3777fe72752b`（23:46:21 UTC），没有跳过前置批准。

三项实现完成，冻结候选 `4cc787c6df7bab74450d39b60022ce83eb0ca8f1`。
运行前冻结的外部 oracle（SHA `5dedd578b578caf7b37352aa3d1252513efb02eac9174cca7234a0fe76071018`）
65项通过，产品21项测试、编译、真实CLI探针通过；逐文件核对stdlib、整数分、UTF-8、
输入不变、错误路径和范围。**这些证明候选功能，不证明Workflow交付完成。**

- API Verify 的独立功能探针通过，但产品报告 evidence_refs 使用事件字符串，随后又以
  全部任务行passed搭配blocked verdict；两次正式提交耗尽协议修复额度，未admitted。
- CLI Verify 功能探针通过，最终以产品旅程未完整覆盖提交，Kernel准入的是 **blocked**。
  原 briefing 同时要求“只读自己切片”和完整产品验收，构成实际矛盾。
- PRODUCT Verify 完成21项测试及额外检查，仍在取证审批顺序时耗尽Run预算。
  00:26:10记录超限，00:26:13 `evt-3c36e582b488 run.goal.blocked`。
  用量12,113,967 / 12,000,000 tokens（input720139、output53284、cache11340544），
  123个费用样本unpriced，不能声称美元预算已验证。

未进入完整discovery/Judge/ship，主分支仍为seed。真实Docker交付探针exit1，独立状态
投影看到根Task blocked，`product_delivery_passed=false`。受控恢复dry-run没有可用的
失败Verify接续checkpoint，没有强制重置预算、伪造结果或跳过门禁。

证据：上述 evidence 根下 `live/prd/complete-oracle/result.json`、`failure-audit.json`、
`terminal-evidence.json`、`usage-totals.json`、`browser-live/`及`recovery-inspect.json`。
完整事件审计使用EventLog读取跨UTC午夜滚动文件。已记录simulation.done，停止自有
watcher/tmux/Web，796个脱敏归档成员校验后清理临时根；归档不包含可续跑凭证。

## 真实缺陷的修复与再次验收条件

| 缺陷 | 已实施修复 | 验证边界 |
| --- | --- | --- |
| fresh Codex误记resume | 无缓存UUID时明确fresh；警告不再错误声称UUID从未观察过 | 覆盖从未缓存、主动清缓存、真实生命周期caller |
| 同一reader串行兄弟预注册反复重启 | 批次内每个role成功ready一次；所有operation仍先注册，实际发送前仍检查context及旧回合结算 | 实际生命周期回归证明一次预启动、忙碌拒绝及下一任务正常切换 |
| 切片范围与产品验收矛盾 | 只有绑定产品报告的Candidate Verify获得必需journey的跨切片只读范围；任务所有权及写权限不变 | 实际briefing caller覆盖带/不带产品规格两支 |
| schema修正消耗正式提交额度 | task-verify/candidate-verify接入现有 `zf result validate`；复用adapter、typed contract和产品报告校验，预校验不持久化sidecar、不写事件、不消耗授权 | 服务及真实CLI解析调用覆盖坏证据、缺旅程、错误target、缺报告和修正后正式提交；最终提交继续所有准入门禁 |
| Critic动态shell写scratch被拒绝 | 实际提示与canonical技能要求native Edit/Write/apply_patch原位更新 | caller回归；不放宽scope guard |
| Planner编造 `impl.child.completed` 取证类型 | Planner/Critic方法要求核对当前producer，比较批准与实际writer启动，空查询不能当证据 | 本次Owner检查也漏验了事件producer，记录责任；新技能的真实Planner/Critic行为仍待重跑 |

新增预校验只检查机械合同；不替代Verify独立判断，也不承诺预算必然足够。
上述改动发生在真实运行结束之后，尚未证明新代码下完整PRD交付通过。
下一次真实验收须固定新提交、沿用原完整需求和预算，从新隔离Run开始，检查Verify全部
准入、discovery/Judge、Kernel交付及最终产品oracle；若再次超限，按实际阶段token和重复
读取证据定位，不通过放宽预算或删除AC掩盖问题。02/05/06/07/08及184交付任务保持active。


### 本批修复的验证结果

- `followup-impact-closure.log`：21文件414 passed，覆盖result submit/typed admission/
  Product Acceptance、实际fanout/briefing/lifecycle、Provider停止与Generic/bounded mock E2E。
- `followup-final-preflight-skill.log`：22 passed / 1 skipped（可选skill fixture），包含
  最终CLI预校验及sidecar不落盘断言；与上一组有交集，不累计为唯一数量。
- `followup-automatic.log`：manual docs currentness通过、sentinel51 passed、三流mock10 passed。
- 三项canonical技能元数据及三份分发副本一致，`git diff --check`通过。
- `dev-verify plan --base dev`覆盖整个历史分支，选747文件；本批显式路径仍扩展225文件。
  `dev-verify run`自动执行contract/mock_e2e，显式跳过deterministic/serial，使用上述
  21文件影响闭包替代；没有宣称full pytest、coverage或修复后全流程真实PRD通过。

收据：`followup-scope.json`、`followup-verification-plan.json`、
`followup-automatic-receipt.json`、`followup-skill-parity.json`。历史RED/中间失败日志保留。


## 2026-10-24 Codex 优先批次（执行中）

用户批准按1—9方案继续，第7项由Kimi改为Codex实测。Kimi接入暂停；真实运行仅用Codex，
跨Provider状态合同继续验证但不能用同Provider跨会话宣称真实跨Provider通过。
[批准清单](../../tasks/active/2026-10-24-0137-185-codex-completion.md)记录逐项验收。
本批开始时 PRD/Issue/Refactor 自动配置候选生成有缺口；`4907ab45` 已补受限 lanes
候选及实际消费者验证，边界如下。Task Map 后重新配置运行中 Run 仍未签收。


## Codex 优先批次：受限配置候选与真实计量（2026-10-24）

已批准的 Synthesis `lanes` 建议进入 `build_flow_submit_preview` 时，Issue/Prd/Refactor
现在可从已有 Flow 生成受限 YAML 候选。只改变 lanes，保留其余文档的语义；正常 loader、
preflight、提案摘要、`workflow-config-apply` CAS 和 submit 门保持有效。原配置在批准前
不变；没有合并候选质量门时扩容仍被拒绝。light 不能通过 lanes 改成 fanout。
显式 Task Pipeline 池不能只改 lanes：必须另提完整池配置修订；本批不自动猜测池成员。
这不证明 Task Map 后重新配置运行中 Run 已实现，后者仍需新代际批准与恢复边界验证。

`plan-execution-review.usage_observations` 和 `zf plan inspect-execution` 接入
CostTracker 已准入的新增计量及事件归属，不重复累计 Provider 的累计快照。
按明确的 Run/stage/operation/attempt/role 归属分组，缺失身份不猜测；项目历史与当前
Run 区分。输入、输出和缓存分别展示；estimated/provider-reported/billed 缺证为 null。
该只读报告不改变预算执行，也不推导剩余任务的价格。Critic 通过既有正式输入引用收到
这些事实，语义建议按 [计量复核方法](../../skills/zf-plan-execution-readiness/references/usage-budget.md)
形成评审证据，不硬编码“难度 -> 模型/并发”。

当前真实 PRD 使用冻结提交 `7e45fe91`，与上述开发中修改隔离。
`TASK-B2E0D4` / `workflow-e848dbc3d8cb2824`：Channel 原需求全文保留，作者归纳为
10 个 AC；独立 Codex 评审逐条检查原始 31 项要求均 PASS，Owner 已审阅确认，
主 Agent 精确提案批准后 Kernel 接收 Run。该结果只证明文档与入口链，不证明产品交付。
本轮无 Kimi 安装或调用；原预算与五文件产品范围不变。
证据目录：`/home/min/workspace/public/zaofu-evidence/185-codex-20261024T013700Z`。

### 恢复投影的 Run 与阶段边界

State Packet 对具有 canonical `execution_binding.workflow_run_id` 的 Task，只读取该 Run
的事件，并以当前 `active_dispatch_id` 的明确 stage 或已请求 Operation 的 parent stage
确定阶段。缺证为 `unknown`，不按自定义角色名称猜作 implement，也不生成错误的旧版
完成命令。旧 Run 的 judge/evidence 不再污染当前恢复包；显式 Run 绑定优先于调用方的
legacy run 提示。无绑定的旧任务保留兼容行为；本改动不从 request_id 猜测缺失的 Run。

回归先复现旧 Run 污染与缺少当前阶段两项失败，再覆盖真实 `zf handoff --format
state-packet` caller、恢复 briefing、运行域隔离及 bounded mock。115 项影响闭包和
51 项 sentinel 通过。这是恢复投影修复，不代表全部跨 Provider 接替或源码 single
新入口已完成；真实 Codex 运行仍使用先前冻结源码，不能作为本修复的真实验证。

## 2026-10-24 目录必需参数与恢复提示

真实 Refactor 启动暴露缺失 `source_root`：路由目录现在从既有交互合同投影
`required_parameters`（objective 保持顶层），CLI/Agent 可在批准前补齐；修改参与目录摘要。
规划 skill 同步说明 source_ref 不能替代 source_root，Refactor 的源与目标必须不重叠，
原地项目需使用经确认的冻结来源副本。运行中的冻结源码未热改。

恢复提示不再把空 next_event 解释为可交付。仅已知 ship 阶段提示等待 Kernel delivery；
unknown 或未解析的阶段要求查阅当前 operation 结果合同，并明确禁止自行 done/ship。
State Packet 是运行事实投影，不是新的 canonical authority。

恢复显示闭包132项、dev-verify自动层303项通过；目录/启动/规划/skill闭包87 passed、
1 skipped，sentinel51项、serial10项通过（有重叠，不相加）。目录共享消费者保守扩展
89文件而升级全仓，本批跳过 broad deterministic，以明确影响闭包验证；不是 full pytest。

## 2026-10-24 真实失败驱动的结果草稿修复

固定 Research 的 Codex risk_critic 正确发现兼容性风险，但编辑生成的不可变 completion
sidecar 时被 scope guard 拦截；随后失败结果导致 synth 拒绝整个研究，未伪造成功。
非 semantic-submit reader 现在生成 completion-drafts 下两份可编辑模板；权限从最新
角色 dispatch、当前 fanout/attempt 与 EventLog 重建状态推导，只允许已存在的精确草稿
原位更新。旧派发、终态、其他角色、任意 artifact、符号链接与新增/删除均不放行。
草稿不是 canonical 结果，提交仍使用原受控 emit 和结果准入；semantic-submit 的签名
scratch 路径与权限不变。重发 briefing 不覆盖已填写草稿。

PRD 本轮 `workflow-e848dbc3d8cb2824` 的产品外部65项、产品19测试通过，首个 Verify
准入；第二个 Verify 在16个 usage samples、2,065,001 token 时触发 Operation 2M
预算，`evt-44ae4e8aace4` 阻断，未进入 Judge/交付。保留原预算，没有把候选 oracle
当成交付验收。该 Verify 有重复大工件读取和多轮大范围 hook 收据查询；验证 skill
补充完整必读资料的分批读取、避免重复输出与按事件身份建立证据索引的方法。
不能据此推断新一轮一定通过；须冻结提交后真实重验。

结果草稿 RED 7项复现；主影响闭包234、额外 hook/指令50、sentinel51通过。
失败运行与清理证据存于 `185-codex-20261024T013700Z`；重验另建目录保留旧失败。

本批 dev-verify 选出20个测试文件，按不重跑已绿检查的原则分组覆盖该闭包；
额外调用者结果见 `research-draft-additional-callers.log`，不是 full pytest。
自动 run 显式跳过已分组执行的 deterministic 层，实际 sentinel51、flow smoke10通过；
另两项 policy/Generic mock E2E 文件9项通过。各组重叠，不累计为唯一测试总数。

## 2026-10-24 Refactor 引用投影与结果恢复顺序

真实 Refactor v4 首次 Planner/Critic 已准入，但 Planner 只提供 `task_map_ref`，
下游投影只读取根级内嵌 Task Map，误报 `plan missing task_map`。现在 Plan Synth
候选合同的 source bindings 同时绑定 ref 与摘要。下游从 Critic 精确审阅的合同、
子报告和 Task Map 摘要链读取不可变工件；支持 canonical task_map port，保留内嵌
旧格式。缺失摘要、身份不匹配、多个生产者或互相冲突的双份内容均拒绝。
不得重新读取可变 worker 目录来补造旧批准；没有 ref 的旧合同需要重新规划和评审。
实际旧运行由原有 replan 修复内容后继续，不能用它证明新投影代码已被真实调用。

Codex Research 重验 `wf-research-fanout-0e3dc7b58300` 的四路报告及 synth 成功，
真实 React/Docker 浏览器任务卡为 done、零页面错误。本地 A/B/C 引用、严格整数与
bool 反例、事实/提案/未知边界人工审阅通过；结构化交接字段仍为空，不能宣称完整
strict 交接验收。运行中 technical_analyst 已写结果却被误判失联而重试，原因是
恢复扫描先处理 Provider 关闭，再重放子结果。恢复顺序改为先走既有结果准入/投影，
然后判失联；没有放宽原始 worker 结果的可信度，也没有增加重试预算。
两项 RED 复现及188项恢复闭包通过；真实重验运行冻结在修复前，仍保留重复重试证据。

State Packet 补齐根 Task 只有 Request 绑定的情形：从 Task 的精确 Request 修订对应
的 `workflow.invoke.accepted` 解析唯一 Run。旧修订、尚未接受、多个 Run 或缺证均
保持 unknown，不继承旧 judge/ship，也不采信调用方猜测的 Run。显式 canonical Run
绑定继续优先；规范化完整恢复包和真实跨 Provider 接替仍未签收。

证据：`185-codex-20261024T013700Z/refactor-ref-*` 与
`185-codex-recheck-20261024T030000Z/{research,state-packet-request-*}`。
Refactor 工件影响闭包216项、State Packet/CLI/Run scope/recovery context50项通过。
Research 已停止并清理，254个归档成员逐一核验摘要；原始失败证据单独保留。

扩展 Refactor mock E2E 还发现一个旧缺陷（冻结421eacf9同样复现）：可选模式下，已通过
secure submit 的结果被再次作为 legacy 结果适配，重复 reported 且 verdict 改变。
现在只要完整 semantic-submit 凭据与已准入事件匹配，就复用原 envelope，不要求该
模式同时为 blocking。伪造准入事件仍走拒绝路径；2项专门幂等测试和74项
Refactor完整mock、call-result runtime/admission、result submit测试通过。
新修复没有热注入正在运行的PRD/Refactor。

## 2026-10-24 交付、故障恢复与启动重试验收

- Refactor v4 `workflow-25cd5e204e64345c` 在冻结 `4907ab45` 下完成
  Task Verify、集成、Candidate Verify、bridge、一致性扫描、Judge；Kernel
  `evt-0f141551fbb5` 确认交付。交付 HEAD `0902a76a` 与候选 `c11e31d0`
  产品树一致，交付后外部38项检查、7个产品测试和真实浏览器通过。入口经过
  操作者 Request 修订，首次 ref-only 计划经过 replan；不把该样本写成全自动入口
  或后来 `281f9f13` 引用投影修复的真实验证。968个归档成员摘要验证并清理。
- 冻结 `281f9f13` 的 Issue light `workflow-60c3c1394fcb7f1d` 在真实 writer
  PID 1180314 被终止后，由受控 worker-respawn 交还同一阶段/快照；Verify、Judge、
  Kernel `evt-b003e661834e` 完成。交付 `8dc25e70` 与候选 `37dc0289` 产品树一致，
  外部6项、产品4测试、真实浏览器通过，Run计量985,530 token。中断发生在首个源码
  编辑之前；不证明 dirty diff 恢复、纯自动恢复、通用源码 single 新入口或跨Provider。
  351个归档成员验证并清理。
- 同一冻结版本 Research `wf-research-fanout-4019d25b7981` 四子任务分别只派发一次，
  无 dispatch_lost 重试；`evt-b67ef3a5ac13` 完成报告，真实浏览器通过。终态后同一
  synth 结果重放被拒绝，未重复派发。A/B/C引用及事实/提案/未知区分人工审阅通过，
  结构化 Acceptance/Test/Task Map 仍为空，不能签收 strict 下游机器交接。226成员
  摘要归档并清理。

### PRD 第三轮未交付及追加修复

冻结 `421eacf9` 的 `workflow-f4e934ac6b9e8331` 保留原需求摘要、五文件范围、
12M Run / 2M Operation 预算。独立PRD评审37项、候选外部65项、产品21测试通过，
但第一份 Verify **准入的是失败报告**：本会话缺少持久测试工具输出收据；第二份才是
通过报告。`test.failed evt-d78914d987d4` 后自动返工把诊断文本
`verification evidence capture` 当作无主源码路径，生成新 dev 任务，随后 assembly
校验拒绝。不能把协议 admitted 当作业务 passed，更不能把候选检查代替交付。
本轮Run计量8,875,933 token，未加预算。无安全 resume checkpoint 后，通过
`zf runs cancel` 取消（`evt-905305c1c163`），simulation.done 记录 cancelled。
未进入 discovery/Judge/Kernel交付；失败证据保留后再冻结新版本重验。

追加修复遵守原批准边界：

1. WorkflowStartService 可重新预览同一 Task 尚未启动的 Request。必须匹配精确修订、
   不可变 Requirement 的 Task ID/合同摘要、原来源；终态/活动派发/Run准入历史拒绝。
   preview 不修改 canonical binding，仍需新精确提案批准，submit 才绑定。其他Task、
   旧修订、篡改工件、来源变更均拒绝；不是任意采用一个未绑定Request。
2. reader target pin/checkout/dirty reset 的清理保留精确 `.codex/hooks.json`。
   原实现会删除 Provider 已安装的运行时 hooks，形成发现时序竞争。三个真实git回归
   先RED，修复后证明 hooks 内容不变，其他未跟踪文件和 `.codex` 杂项仍被清理。
3. finding 路径无任何 Task Map owner 且无明确 gap plan 时，交回重新规划；Kernel
   不再凭诊断文本生成默认 dev 所有权和源码 scope。既有有主替代任务与显式 gap plan
   保持原校验，assembly门未放宽。

验证：Request/Run影响闭包81项、自动14文件255项；hooks/workdirs/spawn/reader
闭包92项；返工/TaskMap闭包134项，恢复caller67项。各组有重叠，不累计为唯一总数。
证据分别位于 `185-codex-recheck-20261024T030000Z/live/prd` 与
`185-writer-recovery-20261024T033900Z`。后续新源码真实运行和最终自动gate结果另记。

追加验证：Run Manager直接caller159项通过；hooks直接caller与相关mock25项通过，1项host层排除。另10项历史Codex hook静态hash基线失败，在冻结281f9f13也全部复现；未修改hash基线，未把该组写成全绿。该失败与本次保留hooks路径无代码交集；真实Codex版本0.163.0，旧hash验证基线0.144仍待独立重新资格验证。

最终检查：新增Task已占用Run的拒绝分支纳入9项Request回归，全部通过；文档指令8项通过。hooks/返工dev-verify选择276文件并升级全仓，本批显式跳过deterministic/serial全仓层，以前述直接调用者和共享合同闭包替代；自动sentinel51、三流mock10全部通过。不是full pytest；历史hash基线10项失败继续保留。

## 2026-10-24 Run Manager 恢复事实隔离

`build_task_recovery_context` 与 State Packet 共用 canonical Task→Run 解析：显式
Run 优先，否则仅采用精确 Request 修订对应的唯一 accepted Run。未接受或歧义
绑定保留 unresolved；旧 Run 的失败、活动及恢复动作不进入当前恢复上下文。
请求中的失败 ID 若无法在当前边界核验，单独列入 unresolved，不伪装成已取证失败。
复用 role instance 已服务其他 Task 或缺少 Task 绑定时，不再报告其 worker 可用。
新增 execution_boundary 仅表达事实，不授予继续执行权；完整规范化恢复包仍待完成。

两个回归先 RED；修复后 State Packet、handoff CLI、Run scope 和恢复 briefing
70项、Run Manager/语义恢复/Task pipeline 调用者96项、sentinel51项通过。各组
重叠，不累计为唯一总数。证据位于 `185-prd-hooks-20261024T042500Z/recovery-*`。
第四轮真实 PRD 冻结在 `4b81a645`，未包含本次恢复上下文修改，不能作为它的实测证明。

恢复批次自动 dev-verify 覆盖40个文件：897 passed、1 skipped，包含相关确定性mock E2E；未跑host/real_provider/full pytest。

### Research 与恢复交接的实际消费者

固定 Research 的 synth briefing 原模板只提供 summary/findings，未暴露现有研究报告
投影支持的 architecture、acceptance_matrix、test_matrix、task_map 和下游 prompt
字段。现在在固定路由的真实 synth 入口预填这些字段，并提供来源、事实/提案/未知、
验收与测试映射的填写方法。任务建议仅为研究建议，不能生成 canonical Tasks 或跳过
下游计划批准；不适用或缺证时允许为空并说明原因，不增加 Kernel 语义质量判定。
端到端 mock 证明字段经过 synth→aggregate→持久报告保留，相关192项通过。

同样的跨 Run 隔离补至实际恢复 briefing 使用的 resume packet，以及 Web/人工交接
使用的 handoff summary。否则即使 State Packet 已修复，旧 judge.passed 仍会进入
“不要重复已完成步骤”或把当前摘要显示为 ship。显式当前 Run 与待接受 Request 两项
先RED，修复后恢复/交接57项通过。legacy 未绑定任务继续兼容；不改 canonical 状态。
证据：`185-prd-hooks-20261024T042500Z/{research-handoff-*,resume-scope-*}`。

第四轮 PRD `TASK-2511E6` / `workflow-0704bed45f851959` 冻结4b81a645，独立文档
评审31项通过（evt-8629457f5462），Owner确认和精确启动批准后进入扫描。仍未交付；
本段新增交接修改没有热注入该真实运行。

恢复补充调用者29项通过（含旧合同handoff mock）。最终自动检查因共享Orchestrator
选择全仓层，本批明确跳过deterministic/serial宽层，采用上述192/57/29项影响闭包；
sentinel51项通过，flow smoke结果见同目录handoff-verify-receipt.json。

用量报告补齐精确阶段关联：usage receipt缺少stage时，只读取同一Run、同一Operation
的唯一parent_stage_id，不根据角色名或时间猜测。跨Run、冲突和缺失关联继续unknown；
原始usage计量、Attempt和费用语义不变，EventLog读完关闭。1项RED与43项计量/规划
调用者测试通过。既有真实样本可重新投影阶段事实，不能将新投影写成旧运行使用过的新代码。

最终三流mock10项通过；本批仍为focused验证，不是full pytest。

## 第四轮 PRD 预算阻断与 Planner 输入改进

`workflow-0704bed45f851959` 的Planner在2,027,576 token触发原2M Operation上限，
`workflow.budget.exceeded evt-28bf3380f732`、`run.goal.blocked evt-f29e0b52793e`。
本轮尚未提交Plan或产生candidate，不能证明产品交付或PRD Verify hooks修复。
Run合计2,553,986 token；保持原始失败和预算，停止后413个归档成员摘要核验并清理。

诊断记录33次Planner工具调用，包含重复读取skills、已取证报告以及宽泛CLI/help、
workflow render和事件探索。规划briefing新增由现有execution_policy投影的已编译
配置事实，包括policy摘要、阶段、writer配置、Plan批准要求与task pipeline；
缺配置明确unknown，不凭默认值认证配置。信息只供规划，不授予执行权。
规划skill补“当前证据/未来门禁”区分、缺失部分补读和有界工具探索方法，三份分发同步；
完整必读来源、原AC、独立Critic和Plan批准均保留。1项RED后190项调用者/skill测试通过；
初次修复的字段名与无config测试上下文错误已纠正，失败日志保留。
这些方法尚待新冻结真实Run验证，不能把测试通过等同预算问题已解决。

## 结构化 Research 新样本

冻结 `18c625a2` 的 `wf-research-fanout-e939975dd2ee` 于 `evt-233e5e9819be`
完成。新需求明确增加结构化交接要求，不能重新解释旧report-only样本。四子报告各派发
一次，产出architecture、6项AC、6项测试、5项未批准任务提案及PRD/refactor输入；
ID和依赖引用、来源/事实/提案/未知、bool兼容反例、风险异议与批准边界人工审阅通过。
没有产品源码改动，也没有执行建议中的测试或生成实际实施Task。
证据：`185-handoff-20261024T045000Z/research/{structured-report,owner-report-review}.json`。

Research浏览器任务卡done且零页面错误，235个归档成员摘要核验并清理。Planner改进
最终sentinel51、三流mock10通过；因共享模块闭包保守升级全仓，自动宽deterministic/
serial层显式跳过，以190项直接影响闭包替代；不是full pytest或真实预算成功证明。

未提交修改恢复：`workflow-fdd2aa505f4d3544` 的writer修改test_counter.py后被终止，
确认旧Codex及工具进程退出后受控worker-respawn，`evt-a5c5ed68b94f`接续。
中断测试文件逐字节保留；Verify报告 `evt-5ca8292ccf49` passed，15条本会话
post-tool hooks存在；Judge `evt-adea3548b18d`、Kernel `evt-f079f3d60e11`交付。
候选9be4fa23与交付61b6c21f产品树一致，交付后外部6项及4产品测试通过，Run937,801
token。该样本补充了dirty edit与hooks实际验证，仍不等于自动恢复、跨Provider或通用
源码single新入口。真实浏览器和最终清理另记。
