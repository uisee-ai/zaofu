# V5 验证上下文与运行时收尾

关联：[实施任务](../../tasks/done/2026-10-30-0903-v5-verification-efficiency-and-runtime-closeout.md)、
[受控装配](registered-controller-assembly.md)、[前次真实运行](../records/2026-10-30-hello-prd-v5-controlled-assembly.md)。

## 必读输入与验证提交

`zf artifact read --attempt <id> --required` 保留小结果的逐行 JSON 形式；超过有界输出时，
返回 `required-artifact-page.v2`、`items` 和 `next_cursor`。将游标原样传给同一命令的
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

V5 PRD 阶段角色的无进展阈值、on-demand 空闲阈值、`bounded-direct-v1` operation 期限均为
600 秒。Task Pipeline 的 `workerLifecycle.idleSeconds` 单独设置为 600，防止覆盖 reader 默认值。
常驻 OA 保持 resident；协作 Research 路线、心跳、启动等待、锁等待及全 Run 期限保留各自策略。
150 万单操作 token 上限不变，不将缓存输入排除出累计处理量，不虚构模型价格。

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

定向测试覆盖分页完整性、重复来源鉴权、预校验失败后修正提交、重复提交拒绝、600 秒配置展开、
争锁退避、无飞书通知、脱敏、Web Task 绑定、预算展示和实验异常退出。真实浏览器/Provider
复测结果另行记录；mock 通过不能替代最终交付证据。
