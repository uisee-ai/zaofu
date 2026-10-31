# V5 验证上下文与运行时收尾

关联：[实施任务](../../tasks/done/2026-10-30-0903-v5-verification-efficiency-and-runtime-closeout.md)、
[受控装配](registered-controller-assembly.md)、[前次真实运行](../records/2026-10-30-hello-prd-v5-controlled-assembly.md)、
[无期限执行与活性复测](../records/2026-10-30-v5-unlimited-execution-liveness.md)。

## 真实运行残余问题

Discovery 是已接纳代际的全局扫描，读取完整 task-scoped Plan port；Impl/Task Verify
仍只消费本任务分区。该范围选择位于实际 canonical handoff 入口，仍校验 Package、Run、
代际与 digest，不为父任务伪造分区。

Kanban Create task 在写入 TaskStore 前，将既有用户消息 causation 或显式消息引用绑定为
`originating_message_event_ids`，核对项目、会话和 thread。critical context 从已绑定事件
恢复原文；非聊天入口不凭空生成来源。历史缺失的来源仍明确标记 unavailable。

Codex 消费见证识别 `functions.exec` 中的标准命令结果 JSON 包装，解码完整 `output`，
同时保存原始包装、session/call 身份与输出摘要。错误来源、截断、旧时间戳和仅出现在
arguments 中的正文不能证明交付。见证仅证明 transcript 可观察，不证明模型理解。

Workflow Graph 一次读取固定事件前缀，`head_seq` 描述该快照边界。结构 generation
可供随后 execution/node/full 读取使用同一边界；Provider 新事件追加不使正在读取的快照
失效，下次正常刷新获取新 generation。配置、TaskStore、实际引用的侧文件变化及事件前缀
篡改/截断仍拒绝旧 generation。只在取得事件前缀时短暂使用 ledger 锁，构图不持写锁；
快照保存在可丢弃的 Web projection cache 中，不新增业务状态来源。
较新结构缓存发布不撤销已开始的合法读取：在途请求继续验证自己绑定的侧文件/前缀。
执行摘要串行读取，新的结构 generation 合并为一次后续读取；已完成快照保持各自的事件边界，
项目、Run、revision 或历史视图切换仍取消旧请求。
前端更新图数据时保留 React Flow 已测量的节点尺寸，避免 DOM 尺寸不变时节点一直隐藏；
后续尺寸变化仍由 ResizeObserver 更新，实时刷新保留拖动位置与视口。

同一基础构图读取只序列化一次展开配置（绑定边界另行校验），事件追加不再引发三次完整构建后返回 503。
Planner 方法要求检查每角色的输入放大与完整语义覆盖；Verify 可在工具输出预算允许时
一次回合读取少量连续 CLI 页，逐页保留完整正文和收据。没有降低 lane 数、删门禁，
也不能据此保证任意 Provider 的 token 数或消除宿主资源压力。

中文/Markdown 内联数值预算（例如 `token_budget=0、timeout_seconds=0`）保留可读，
后续正文继续执行秘密扫描，非数值可疑赋值及真实凭据仍脱敏。
Flow YAML 的 `contextWindowTokens` 纯数字同样保留，与展开后的
`context_window_tokens` 一致；不把上下文窗口大小当成访问凭据隐藏。

## 必读输入与验证提交

`zf artifact read --attempt <id> --required` 统一返回 `required-artifact-page.v2`，
包括小结果。通过 `items` 读取正文，保留 attempt、bundle digest 和每次读取唯一的
`delivery_id`；将 `next_cursor` 原样传给同一命令的
`--cursor`，直到游标为 null。`--page-bytes` 控制整个返回（含元数据和换行），范围 1024–8192 UTF-8 字节。
每个来源独立鉴权并校验原始摘要；内容相同、切片相同的来源只交付一次正文，各自记录收据。
游标绑定 attempt manifest、必读声明和内容；只记录当前实际交付范围，漏页不能通过完整消费门禁。

Task Verify 先读阶段卡和受控输入，任务投影用于缺失上下文或身份诊断，不再要求无条件重读
task/source/progress 三份投影。evaluator skill 明确先查 hook 索引，再按 ID 读取原始证据，
保留已消费的正文与 ref，不反复读取技能或近期完整事件。方法由 skill 拥有，Kernel 不判断产品语义。

`zf result validate ... --submit` 失败时返回结构化诊断且不消费 submit capability；成功时在
同一次 CLI 调用进入已有 `SemanticResultSubmitService.submit`。Task/Candidate Verify 阶段卡
使用该命令，避免成功预校验后再消耗一个模型回合。正式接纳仍校验当前 operation、身份、证据
和结果合同；预校验不能冒充通过、不能复活被预算阻塞的 operation。

## 时间与用量

V5 的 `bounded-direct-v1.limits.timeout_seconds` 与 `workflow.run_limits.timeout_seconds`
显式为 0：执行不按累计时长终止。已声明 stage 的 `timeout_seconds: 0` 同样关闭执行期限，
不再被运行时替换成 7200 秒；无法解析定义的旧 stage 仍保留兼容性 fallback。
显式配置的正数期限、启动等待和锁等待继续生效。

PRD 阶段角色 `stuck_threshold_seconds`、on-demand 空闲阈值和 Task Pipeline 的
`workerLifecycle.idleSeconds` 保持 600 秒。检查分别判断：

- 有当前任务/operation 且最近有 Provider 工具、文本、用量或 worker 心跳：继续 busy，
  累计执行时长不触发终止。用量事件即使由 Kernel 采集也属于真实 Provider 活动。
- 有执行义务但持续无活动：进入已有静默探测、有限重试与失败/恢复路径；
  内核状态镜像和 probe 事件不能续活。reader 与 synth 在关闭执行期限时仍检查无活动窗口。
  Codex/Claude reader 保留 `attempt_lease_grace_s`（默认 900 秒）保护静默思考；
  心跳检测也保留现有 active-turn grace，因此 600 秒不是一到即杀的期限。
- 无活动任务/operation：按 idle 策略休眠；保留 session，在后续派发时恢复。
  常驻 OA 仍为 resident。心跳/工具活动仅证明活性，不替代测试、审查或结果接纳证据。

v5 显式设置 operation 与 Run 的 `token_budget: 0`，关闭原 150 万单操作与 1,000 万
Run 累计 token 上限。仍保留 60 次 usage sample 上限及 operation/Run
金额上限（8/40 美元）。用量继续完整记账，缓存输入仍属于累计处理量，不虚构模型价格。

Task Pipeline 释放旧 stage session，以及 reader 跨任务/派发切换时，先确认 Provider 停止，
再用旧 session binding 补采尾部用量，通过 EventWriter 与幂等 CostTracker 入账后才轮换身份。
只补采正在退出的角色；失败保留旧身份供重试，append 成功而 cost 写入失败也可重放补账。
该对账修复不会重写已终止的 Run 预算 verdict；周期门禁与最终尾部统计仍是不同观测时点。

Workflow operation 详情展示 Kernel 预算事件绑定的累计处理量、非缓存输入、缓存读写、输出、
期限与金额。未定价或仅部分定价时总金额为未知；最近上下文占用单列，不与多回合累计 tokens 混用。

## 运行时可靠性

- Workflow Synthesis 消费者只有争锁超时按本轮未消费返回；持锁期间真实异常仍传播。
- 默认 Owner 通知保持 Web/Inbox 可见，仅在已有飞书路由配置下自动选择飞书；显式要求飞书
  而缺少配置的请求继续记录投递失败。默认无渠道声明的请求不因飞书缺配置制造失败。
- Raw YAML 的已知数值 token 字段不按凭据遮盖；JWT 识别验证 JOSE header 或明确的 JWT/Bearer
  标记，普通点分测试名称可读。真实密钥、环境口令和敏感字符串仍脱敏。
- Web Request 的 Task 关联优先来自 digest 核验后的项目内输入 manifest，并核对本项目 TaskStore；
  不从 URL、request 名称或另一项目的文件推断。已有直接 Task 绑定的旧 Request 保持兼容。
- Autoresearch resident 在子调用返回、超时或异常时先按该实验 `zf.yaml` 停止其 runtime（包含
  Run Manager），再归档/清理 worktree。配置、session 所属项目不匹配或停止失败时保留 worktree；
  文件保留和进程寿命分别处理。停止子进程环境移除父项目定位变量，禁止误停真实 dev。

## 验证边界

定向测试覆盖分页完整性、重复来源鉴权、预校验失败后修正提交、重复提交拒绝、无执行期限与空闲阈值配置展开、
争锁退避、无飞书通知、脱敏、Web Task 绑定、预算展示和实验异常退出。真实浏览器/Provider
复测结果另行记录；mock 通过不能替代最终交付证据。

## Workflow 审批证据进入 Goal closure

Judge 的 `closure-fact` 除 Plan 审批/派发顺序外，也投影同 Run 实际调用所绑定的
`workflow.request.approved`、`workflow.submit.requested` 和 `workflow.invoke.requested`。
Request ID、revision、Requirement digest 必须与 invocation 一致，审批必须发生在调用前；
冲突 Run、旧 revision、晚到审批和缺失审批不能补成成功。保留原事件 ID、主体、顺序、
Proposal/config 摘要，语义验收仍由 Judge 决定。受控 stage-retrigger 可以在补齐代码后
生成新 closure identity 并重新裁决，旧拒绝与旧快照保留。[真实验证](../records/2026-10-31-kanban-judge-fidelity-e2e.md)。
