# 185 续批：有界工件执行、规划绑定与恢复隔离

> 状态：实施与确定性验证中；尚未开始本批真实 Provider E2E，不表示 185 全部签收。

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
