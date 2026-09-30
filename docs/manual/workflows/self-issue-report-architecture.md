# 143 · Self-Issue Report-only

## 目标

当前 Web `/issue` 使用文末“Portable Issue workspace”流程：同一个评估 Agent 先生成
报告，用户可再次分析或生成可编辑 Draft。以下旧的 Draft-first/Intake 描述保留供
兼容入口使用，不代表新 Web 工作区的交互顺序。

`/issue` 与 `zf issue report` 进入同一条 host-controlled 状态路径，把一段自然语言
直接整理为可编辑 Draft；用户随后明确选择是否调用通用 project-assessment agent，最后
通过单一 Publish/Connect/View 控件发布到宿主配置的 Forge。P0 不修改代码、不自动修复、
不自动评估、不自动发布。ZaoFu 只实现一个 host adapter，不是通用 skill 的前置依赖。

## 权限与状态边界

```text
Web /issue ─┐
            ├─ self-issue-capture → Draft（自然语言理解）
CLI report ─┘                         │ 用户编辑/选择评估方式
强 Kernel 信号 ─ allowlist ───────────┤ system_detected / awaiting_user_review
Worker 发现 ─ actor-owned sidecar ────┘
                                     ▼
                      limited_report 或 project_assessment decision
                                     │（仅后一项允许调用评估 agent）
                         固定 revision 的只读 checkout/sandbox
                                     ▼
                         Issue-understanding / assessment artifact
                                     │ Kernel 校验 digest 和 capability
                                     ▼
                       Publish → Connect/Publishing → View
                                     │
                              Forge Issue + labels
```

- Agent/skill 只生成本地 sidecar、证据引用和 controlled action intent。
- Kernel/host adapter 独占 Draft、EventWriter、Secret Provider、OAuth、publication
  snapshot、幂等裁决及 Forge 写入；agent 只能返回结构化 artifact。
- `orchestrator` 是配置的语义角色；它不替代确定性 Python
  `Orchestrator`，也不是新的诊断 Agent 或第二状态机。

## Draft 与兼容 Intake

公共 `/issue <自然语言>` 不再经过固定问卷。理解 agent 生成 `issue-draft.v2`，包括标题、
Bug 描述、复现步骤、预期行为、附件描述、OS/版本、通用 `project_version` 和空的
`additional_context`；浏览器/宿主检测结果标明来源并可由用户覆盖。空 `/issue` 也直接生成
可编辑 Draft。旧 `self-issue-intake.v1` 八问仅通过显式 `--legacy-intake` 或 legacy API 使用。

Draft revision、原始文本 digest、附件 refs 和目标绑定原子持久化。刷新恢复同一 Draft；
取消会物理删除未发布的本地 Draft 与受控附件。每次编辑都会失效旧评估/发布快照。

自动触发不绕过 Draft：只有宿主定义的强信号 allowlist（例如 Kernel housekeeping
失败、Orchestrator tick 失败、Watcher 严重积压、briefing hydration 契约失败），或携带
actor-owned immutable refs 的 `worker.self_issue.detected`，才能创建 local-only
`system_detected` 候选。普通项目测试失败、已恢复的暂态错误和 Self-Issue 自身事件不触发。
同一 fingerprint 24 小时更新候选，通知冷却 6 小时且严重度升级可突破；最多保留 10 个
活动自动候选。用户取消后，Event ledger 中的 fingerprint tombstone 在 24 小时内阻止重新
弹出。自动路径不会确认附件、启动 OAuth 或发布。

## 发现者证据与评估授权

worker 发现异常时由该 worker 生成受控 local-only sidecar，并在 action intent 中提交
`reporter_evidence_refs`；Kernel 校验路径位于配置 state dir 且 digest 未变化。没有
worker 的 Kernel/Web/provider 异常由 Kernel 收集机械快照，宿主注入的 project-assessment
agent 在隔离 committed-source workspace 中补语义证据与评估。

执行载体、证据与评估是三个独立状态域。载体未存活时可以保留静态证据；载体在线、heartbeat、
worker 可调度或已有登录都不能构成用户授权。只有当前 Draft revision 的
`assessment_decision=project_assessment` 事件允许调度通用评估 agent；`limited_report` 明确
跳过该 agent 并记录原因。评估 agent 只能在固定 revision 的只读 checkout/sandbox 中运行，
不能调用 ZaoFu kernel/orchestrator、写源码或发布 Issue。

如果同一 evidence run 已经发出 `self_issue.assessment.requested`，再次选择 limited 必须
fail-closed，避免 scheduler 继续执行旧授权；用户需要启动新的 run/revision 再作选择。

Worker 自动入口固定为 `worker.self_issue.detected`：sidecar 的 `created_by` 必须等于事件
actor，必须包含 `source_event_id`，Kernel 再校验路径和 digest。Kernel/Web 的自动候选只
保存可验证的检测 seed；用户完成同一 Draft 后，仍由宿主注入的 project-assessment agent
完成技术上报与最终评估。两种来源都不能直接写 Draft 或 Event ledger。

评估输出固定为 `self-issue-assessment.v1`，包括 classification、severity、
reproduction_status、component、impact_scope、confidence、observations、hypotheses、
counter_evidence、unknowns、code_locations、duplicate_assessment 和
recommended_next_action。结果只在 run id 与 Draft revision 同时匹配时应用。
Kernel 为每个 draft/run 在 state dir 中维护 owner-only reproduction ledger，并把它作为
次数、目标与结果的唯一权威来源；`run-reproduction` 只允许 3 次有效调用，第 4 次在运行
测试前拒绝。activity 只是该 ledger 的安全投影，分别记录 `Reproduction n/3 started` 与结果，
并只显示经过白名单验证的 workspace-relative 测试目标。Provider 输出限制为 64 KiB，
JSON/字段/枚举/未知字段失败只记录安全类别，不保留原始回复或字段值；无法形成合法输出
时应用 `unknown/unverified/low` 的保守 assessment，使用户仍可审阅证据，而不是把整次
采集标记为失败。
机械日志摘要会省略空行及仅含 `[]`、`{}`、`null` 的无信号 heartbeat，避免把空结构
扩散到可公开 Markdown；有意义的脱敏行仍保留。
日志证据同时保留每个受控文件最后 4096 字节的脱敏上下文，并在总计 64 MiB、单文件
20 MiB 的界限内提取最多 100 个异常候选。Kernel 只负责 ERROR/Exception/timeout/5xx/
slow request 等机械候选及稳定 ID、digest、相对路径和行号，不用关键词重合宣称相关性。
project-assessment agent 结合用户描述、事件、时序与源码做语义判断，最多返回 20 个候选 ID；
Kernel 验证 ID/digest 后才生成公开位置摘要。未知/篡改 ID fail-closed；没有中高置信度
语义关联时明确写入未发现提示，并继续提供独立的日志尾部上下文。

Interrupt 写入 checkpoint、把未完成 attempt 记为 `outcome_unknown` 并终止对应 provider
进程。中断后 Web 同时提供 **Resume from checkpoint** 和 **Restart with fresh evidence**：
Resume 保留原 run、证据快照和剩余预算；Restart 创建新 run、重新采集机械现场并获得新的
三次预算。Web 每 1.2 秒读取安全 activity 投影，实时显示发现者、Kernel
collector 和 orchestrator 的阶段，完成后无需刷新。

committed-source snapshot 使用单个 `git cat-file --batch` 读取受限文件集，避免逐文件
启动 Git 进程。Codex 只有在宿主能强制 read-only sandbox 时参与评估；若宿主不支持，
Kernel 自动切换到 Claude 的只读工具白名单。两个 Provider 都不能强制只读时 fail-closed，
不得用宿主级 `danger-full-access` 绕过。

`pending/running` 仍阻止 publication preview。`interrupted/failed/conflict` 是可由用户
明确选择继续发布的终态：Kernel 不采用或披露局部评估输出，而是在不可变 Markdown
snapshot 的 Incident evidence 段分别写入“用户中断未采集”或“未采集到现场证据”。
Draft 编辑页按用户输入与 agent 评估双栏组织；Preview 始终渲染最终
provider payload，不继承编辑页布局。
当前 `publication_state=published` 时，Draft 字段和 Save/Preview/Restart 操作只读；
Kernel 同样拒绝内容更新或证据重启，并从已发布 PublicationIntent 恢复原不可变 Preview。
仅存在历史 `published_issue_ref` 不构成写锁，锁定只由当前 canonical publication state 决定。
Runtime 不可用时用户可选择 limited report：证据状态终止为 completed、评估为 skipped、
置信度为 low，最终 Markdown 必须明确说明静态证据边界和未执行语义评估。已发布 Draft
的 Draft/Preview 标签仍可切换，Preview 只读取已发布 Intent 的不可变 payload。

## 安全披露与附件

原始日志、Trace、配置、事件账本、身份、未提交源码与凭据只留本地。文本/JSON
附件要求 UTF-8 并脱敏；PNG/JPEG 校验结构并剥离元数据；视频必须单独确认公开披露。
来源或敏感性不明确时拒绝外发。

附件与 Issue 是两个独立但不可变的披露边界：

1. 用户预览并确认附件 manifest 后，Kernel 才使用 Secret Provider 中的 token 调用
   GitLab upload API；
2. 上传返回的 Markdown/URL 写入新 Draft revision；用户再查看最终 Issue payload，随后
   通过唯一的 **Publish** action 冻结并创建 Issue。首次未授权时该 action 进入
   `connect_required`/Connect，授权后只恢复同一 snapshot。

Issue 已创建但 label/附件等后置步骤失败时，状态为 `partially_published`：保留远端 Issue
URL 和失败原因，只允许重试后置步骤；不得重复创建 Issue。全部后置步骤成功后才进入
`published`/`view`。

只要 Draft 含附件，最终发布就必须确认附件遵循 GitLab 项目可见性；无附件时不要求。
必填答案校验优先于附件确认校验。采集完成后，Kernel 可生成有内容的脱敏日志/时序/失败
事件 Markdown 摘要，并复制已存在且来源标记为 Playwright 的截图、剥离元数据。两者都
只是 local-only disclosure candidates，必须进入同一 manifest 确认和 GitLab upload 路径。
最终正文只引用上传后的 GitLab Markdown/URL，不披露 canonical sidecar ref。
本地 Web 的附件确认清单可显示 state dir 中受控副本的绝对路径，并通过 Draft id + digest
校验的只读路由在新标签页打开；该路径是瞬态投影，不写入 Draft、事件或发布 payload。
最终 Markdown 始终使用 Provider 返回的绝对 HTTPS URL，不复用只能在 GitLab 页面内
正确解析的相对 `/uploads/...` Markdown。

自动 Playwright 仅接受枚举目标 `kanban_board` 与显式 loopback HTTP URL。Web `/issue`
可携带当前 Web origin；Kernel 强信号在配置了 `browser_capture_base_url` 时也可请求。请求
先保持 deferred，只有 Runtime 存活且 orchestrator 将问题归类为 `web/ui` 或给出安全的
Web/Kanban component 后，Kernel 才执行截图。
Kernel 使用 `mcp/playwright:latest` 执行一次 1440×900 的 clean-context 被动截图：无用户
Cookie、无点击/输入/提交、阻断非本机请求、遮罩输入框/聊天/身份区域，并在可见文本出现
凭据形态时丢弃结果。它标记为 `playwright_clean_reproduction`，不能冒充用户原浏览器
现场。截图失败为非致命状态；成功图片仍需附件 manifest 的独立预览、确认与上传。

附件响应丢失进入独立 `outcome_unknown` 锁；只能由带 evidence refs 的 Controlled
Action 裁决。Issue 发布仍使用稳定 marker 查询恢复，两个幂等路径互不混用。

## OAuth

GitLab.com 使用 Authorization Code + PKCE S256、一次性 state、精确 redirect URI，
并绑定 session、Draft、凭据主体和已确认的具体操作。若用户是在已确认的附件上传或
Issue 发布步骤进入登录，OAuth 回调会只恢复该操作；普通连接成功不发布任何内容。
token 只在 Kernel → Provider 边界短暂解封。Web 在独立授权标签页中完成该回调，通过
同源消息或 localStorage 通知原页面后关闭授权页；原页面保持原工作上下文并等待 Kernel
返回最终发布结果。

GitHub.com 使用公开 GitHub App 的 Device Flow。App 固定安装到官方上报仓库，权限为
Metadata read 与 Issues read/write；Client ID 可公开配置，不分发 client secret。Kernel
保存一次性、会话绑定的 device transaction，Web 在用户打开 GitHub 授权页后轮询；成功
只恢复当前已冻结 `PublicationBatch`。用户可选 `gitlab`、`github` 或 `both`，目标项目
均由 `zf.yaml` 锁定。Batch 只负责一次用户 Publish；每个 Provider 仍有独立 Intent、marker、
远端 ref 和 `outcome_unknown` 恢复，不能互相代替结果。

GitHub 没有本功能可依赖的公开 Issue 二进制附件上传 API。第一版 GitHub-only 只发布完整
Markdown 并明确提示二进制未上传；`both` 模式仅向 GitLab 上传经确认的二进制，GitHub
发布文本。GitLab-only 保持原附件流程。

公共 GitHub 用户不能在创建 Issue 时直接设置仓库 label。官方仓库使用
`.github/workflows/self-issue-labels.yml` 在 `issues.opened` 后，以仓库 `GITHUB_TOKEN`
从带稳定 marker 的正文读取 allowlisted classification/severity，并应用预先创建的同名
label；核心 Forge payload 仍保持 provider-neutral。

## 文档边界与排除范围

本文件只描述用户侧 Self-Issue 和发布边界；开发者侧 Triage → Fix → Verify → PR → Review → Merge
的实现合同以 `New_function/Self_issue/self_issue_revision.md` §6 及 `steps.md` Phase 5–9 为准。

不实现旧 diagnosis/clarification schema、独立诊断 Agent、自动修复、auto-heal、
自托管 GitLab、GitHub 二进制自动上传、merge、部署、重启、回滚或当前实例自我修改。
## Portable Issue workspace（2026-09）

`/issue` 的评估会话现在由一个可恢复的工作区承载，而不是在确认后立即跳转
到发布页：报告轮次、每轮活动、证据、Draft 版本和发布状态分别保存。用户可以
选择任意报告字段并附加 comment 再次分析；再次分析只生成新的轮次，旧报告和旧
证据仍可查看，只有用户点击“生成 Draft”才选择一个轮次生成新的 Draft 版本。

工作区的四个主要动作是“再次分析 / 生成 Draft / 查看预览 / 发布”。预览是本地
Markdown 渲染；Draft 编辑每 10 秒以受控 action 自动保存，并在生成或保存时写入
版本 sidecar。页面刷新后从 Session 与 Draft projection 恢复，不依赖浏览器内存。

公开正文以 Issue #59 留存正文作为回归基线：Environment 固定为 OS、OS version、
Architecture、Python version、Project version、Project source commit 六项，事实由评估
Agent 收集；不以渲染宿主代填，不混用 Issue 工作区、源码和 wheel 构建提交。Triage
将 Severity status/reason 紧随 Severity，Impact scope 紧随 Component，Assessment
confidence 紧邻 Assessment 之前。Hypotheses 统一为 Hypothesis、Confidence、Status、
Support，兼容正文别名；Support 和 Locations 使用原编号样式并将首项独立换行。
“其他分析”显示为 Other analysis。证据编号仅用于内部关联，正文引用实际文件/位置及
支撑结论，不显示 E1/E2 编号；完整证据和环境来源仍保留在结构化报告中。

工具调用只在活动摘要中展示，完整结果写入带 digest 的 evidence sidecar，展开时
按 activity id 读取；若上游工具本身标记截断，界面保留该标记。暂停会撤销 provider
进程并提交最近安全检查点，迟到的 Agent 结果经过 revision/status CAS 校验后丢弃；
恢复从检查点继续。关闭会话只关闭本地工作区并写入 dismiss cutoff，不会重新显示旧卡片。

用户上传附件先通过受控 sidecar ingress 校验路径、大小和 SHA-256。生成 Draft 时复制
到 Draft-owned sidecar，并在 Issue comment 中只发布下载路径、来源（user/agent）、说明、
用途和 digest；不把附件正文或缩略图嵌入 Issue body。
