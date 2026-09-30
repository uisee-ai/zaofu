# 使用通用 Self-Issue 上报问题

Self-Issue 可用于任意本地项目。它把 `/issue` 后的自然语言整理为可编辑 Draft，并在用户
明确选择评估方式、检查最终预览后发布到宿主配置的 GitLab、GitHub 或其他 Forge；不会
自动修改代码、自动评估或自动发布。ZaoFu 只是一个可替换的 host adapter，不是 skill 的前置依赖。

宿主也可以对少量明确的强异常创建本地候选。候选不会自动成为 Draft、上传附件或发布；
用户必须审阅候选并显式确认后续动作。

## Web 完整流程

1. 在宿主的输入框输入 `/issue <自然语言描述>`；也可以输入空 `/issue`，随后直接编辑 Draft。
2. 系统立即生成可编辑 Draft：标题、Bug 描述、复现步骤、预期行为、附件描述、操作系统及版本、
   项目版本和空的 `additional_context`。不再弹出固定八问；八问只允许通过显式 legacy 命令使用。
3. 在 Draft 中编辑所有字段和目标仓库。浏览器/宿主检测到的 OS、版本和项目版本会标明来源，用户可以覆盖。
4. 选择下一步：**Continue with project assessment** 或 **Continue with limited report**。运行中的
   项目、heartbeat、worker 或已登录状态都不能替代该选择；limited 不调用 project-assessment agent。
5. 需要上传时可选择 PNG、JPEG、MP4、WebM、TXT、LOG 或 JSON。最多 5 个，每个不超过
   20 MB，总计不超过 100 MB。只要已经选择附件，提交前就必须勾选“附件遵循 GitLab
   项目可见性”；没有附件时该勾选不影响提交。若必填项和勾选都缺失，系统先定位第一个
   必填项，补齐后再定位附件确认。
6. 用户选择 project assessment 后，宿主才可在固定 revision 的只读 checkout/sandbox 中调用通用
   project-assessment agent；Web/CLI 不会因为 runtime 在线而自行调用。卡片分别显示执行载体、证据与评估状态。
7. 可用 **Interrupt** 中断。中断后可选 **Resume from checkpoint**，使用原 run、原证据
   快照和剩余复现预算继续；或选 **Restart with fresh evidence**，以新 run 重新采集当前
   现场并重置三次预算。中断保留本地 checkpoint，不会把部分输出当成结论。也可以不恢复
   而继续预览和发布；此时最终
   正文明确显示 `Not collected because the user interrupted evidence collection.`。非用户中断
   但采集失败时显示 `No incident evidence was collected.`，不会披露局部原始输出。
8. 在 **Draft** 页编辑报告并点击 **Save draft**。左栏是用户输入，右栏是 Agent 上报、
   orchestrator 分类、严重程度、组件、影响范围、置信度、建议和活动记录。
9. 在 Draft 的 **Publish destination** 选择 GitLab.com、GitHub 或两者。目标仓库由
   `zf.yaml` 集中锁定，普通用户不能改成其他仓库。若所选模式包含 GitLab 且有附件，
   点击 **Safe preview** 查看附件清单，再点击
   **Confirm & prepare GitLab attachments**，随后 **Prepare attachments on GitLab.com**。
   未授权时登录 GitLab；回调会继续这次已经确认的上传。
   完成的只读采集也会把有内容的脱敏日志/时序/失败事件摘要以及已有的可信 Playwright
   截图作为候选附件放进同一清单。它们不会绕过确认自动外发；确认并上传后，最终 Issue
   使用 GitLab URL，其他人能否打开取决于目标项目本身的可见性和访问权限。
   日志摘要保留独立的最后 4096 字节上下文，并列出 orchestrator 根据用户描述、事件、
   时序和源码语义确认的异常候选位置。Kernel 不以纯关键词匹配下结论，只接受自己签发且
   digest 未变化的候选 ID；没有语义相关项时明确显示未发现提示。
   清单中的文件名可在新标签页打开本地受控副本，并显示该副本在配置 state dir 下的
   绝对路径。浏览器不会提供用户最初选择文件的原始绝对路径，因此这里显示的是 ZaoFu
   校验 digest 后保存的受控副本，不会把个人路径写入最终 Issue。
   对从 Web 发起且请求了 Kanban 证据的报告，只有 Runtime 存活且 orchestrator 先判断为
   Web/UI 安全目标后，Kernel 才可用 Docker 中的 Playwright 自动截取一次无交互的本地
   干净视口。它不是用户当前浏览器标签页的原始现场，不带 Cookie，
   不点击、不输入，阻断外网并遮罩输入/聊天/身份区域；失败不会阻止继续上报。成功截图
   仍要经过本步骤的附件确认，才会在 GitLab 中可见。
   GitHub 不提供普通 REST Issue 附件上传 API。ZaoFu 使用 GitHub CLI 2.99+ 的受支持评论
   附件路径，把已确认且不超过 20 MB 的图片/视频发到带稳定 marker 的 Evidence comment；
   文本、Markdown、JSON、XML、YAML 和 CSV 经脱敏与有界截断后写入同一评论。其他二进制
   只记录文件名、类型和 digest，不把本地路径或原始字节写进 GitHub。
10. 附件准备完成后再次点击 **Safe preview**，也可以直接点击 **Preview** 标签生成快照。
   **Preview** 不使用 Draft 双栏，而是按每个 Provider 的最终 Markdown payload 排版；
   GitLab 与 GitHub 各自显示的内容就是各自最终发送内容。
11. 点击唯一的 **Publish** 按钮即确认当前不可变预览。首次未授权时按钮替换为 **Connect** 并显示授权码；
    授权回来后原页面进入 **Publishing…** 并只续发这一份冻结批次。成功后仅显示 **View**；如果 Issue
    已创建但 label/附件等后置步骤失败，保留 **View** 链接并显示 **Retry label reconciliation**，
    不得重新创建 Issue。发布后 Draft 进入只读，但 **Draft** 与 **Preview** 标签仍可无缝来回切换；Preview
    直接恢复实际发布的不可变 snapshot，不会再次 prepare。

浏览器刷新会恢复未发布 Draft、卡片大小和正在运行的安全活动投影。Draft 编辑需点击
**Save draft**；右上角叉号会永久删除当前未发布 Draft 及其本地受控附件。旧版 Intake
仅在显式 legacy 入口中出现，且位于宿主工作区而不是聊天对话框内。

独立 **Triage** 页面默认打开 GitHub Issue 镜像。Repository 与 star 数来自同一次 GitHub
同步；手动 **Refresh** 会绕过短时节流并立即重新读取仓库元数据。列表可按多个 label、
多个 contributor、state 和来源组合过滤；label 使用 GitHub 原始颜色，排序支持创建时间
或名称的升/降序。选择某一 label 或 contributor 时只显示命中的 Issue。Issue 状态与分组
提供悬停说明，作者头像/名称可悬停查看 GitHub 主页及该作者在当前镜像中的 open/closed
Issue 数。详情同时镜像 GitHub comments；评论中的 GitHub 图片直接预览，附件链接在新标签
打开或下载。GitHub 更新后点击 **Refresh** 会同时更新正文、评论、状态、label、头像与仓库
star 元数据。

如果 `/issue` 时宿主执行载体未运行，页面会说明仍可保存 Draft、检查 committed
source，但新鲜事件、日志、Trace、失败截图和实时复现证据可能不可用，并提示执行：

```bash
cd /path_to_project && zf start
```

此时状态为 `evidence: waiting_for_runtime`、`assessment: waiting_for_runtime`，不会启动
评估 agent。可点击 **Check runtime again**；也可明确选择 **Continue with limited report**。
有限报告允许预览和发布，即使载体后来在线也不会自动评估；正文会写明动态证据未采集、评估未执行，
并固定为低置信度，不会伪装成完整评估。

## CLI

自由文本（直接生成 Draft）：

```bash
uv run zf issue report "简短标题"
```

显式 legacy 模式只创建并返回 Intake JSON（公共 `/issue` 不走此路径）：

```bash
uv run zf issue report "简短标题" --legacy-intake --non-interactive
```

用 question-id keyed JSON 提交：

```bash
uv run zf issue answer <intake_id> --answers-file answers.json
```

证据收集完成后，可从 CLI 使用同一个 Kernel publication batch：

```bash
uv run zf issue preview <draft_id> --provider gitlab   # 或 github / both
uv run zf issue confirm <batch_id> --payload-digest <digest>
uv run zf issue publish <batch_id> --confirmation-id <confirmation_id>
```

CLI 发布若返回 `authorization_required`，先在 Web 完成对应 Provider 授权，或沿用同一
workspace 中尚未过期的既有凭据，再以新的 preview/confirmation 继续。Token 不会打印。

Web 与 CLI 使用相同 `self-issue-capture`、Draft schema、SelfIssueService、Draft Store
和 evidence action，不存在第二套业务状态机。

## 旧版八问兼容入口（仅显式调用）

公共 `/issue` 不会打开以下固定问卷。需要兼容旧客户端时，必须显式使用 `--legacy-intake` 或对应 legacy API；
新实现应优先使用上面的自然语言 Draft 流程。

1. Add a title（必填）
2. Describe the bug（必填）
3. To reproduce（必填）
4. Expected behavior（可选）
5. Screenshots, videos, and logs（可选）
6. Operating system and version（可选）
7. Current ZaoFu version（必填，默认填入检测版本）
8. Additional context（可选）

## 证据由谁收集

- 用户发现：用户回答事实，Kernel 收集只读机械快照，orchestrator 评估。
- worker 发现：该 worker 生成 local-only sidecar 并上报 immutable refs；Kernel 验证后
  合并机械快照，orchestrator 评估。
- Kernel/Web/provider 模块发现：Kernel 记录机械现场，orchestrator 在隔离工作区检查
  committed source 并评估。
- 无法判断责任域：Kernel 先生成通用安全证据并记录
  `reporter_fallback: orchestrator`；orchestrator 可判断责任域，但不得冒充 worker 上报。

## 自动触发边界

- 自动候选只来自固定强信号 allowlist，或 Worker 发出的 `worker.self_issue.detected`。
- Worker 必须提供自己创建、带来源事件且 digest 不变的 local-only sidecar；Kernel 不接受
  只有模型判断、没有证据 ref 的自动触发。
- 普通业务/项目测试失败、短暂且已恢复的超时不会自动创建 Self-Issue。
- 同一诊断 fingerprint 24 小时内更新同一个候选 Draft；通知冷却为 6 小时，严重度升级除外。
- 同时最多 10 个活动自动候选；取消某候选后，相同 fingerprint 24 小时内不再弹出。
- 自动触发永远停在本地审阅边界；确认附件、OAuth、查看 Preview 和 Publish 仍由
  用户逐步完成。

可在 `zf.yaml` 中控制该行为：

```yaml
self_issue:
  enabled: true
  automatic_detection_enabled: true
  browser_capture_enabled: true
  # Kernel 信号需要截图但没有 Web 请求 origin 时才需要配置：
  browser_capture_base_url: http://127.0.0.1:8002
```

启用 Workspace 锁定的 Self-Issue policy 后，新建项目时会一并物化外部 Issue
Triage 的运行合同：`external-issue-triage/v1` Workflow、只读的
`external-issue-triage` 与 `external-issue-triage-verifier` 角色，以及对应的
stages/tasks。这样项目既能接收 GitHub Issue，也能在人工批准后转入 Fix Run。
`zf project init` 会生成这些声明；普通 `zf init` 只初始化已有项目的运行态，不会
重写已有 `zf.yaml`。

评估器不能修改代码、访问网络、读取 Secret Store 或主动发布 Issue。它最多通过提供的
runner 执行 3 个现有 focused tests；活动区按 `Reproduction 1/3 started`、结果的形式
显示安全测试目标。次数来自按 draft/run 隔离的 Kernel 持久 ledger，Interrupt/Resume
不会重置；第 4 次会在执行前被拒绝。三次后仍无法确认，或 Provider 返回的
JSON/Schema 不合法时，系统只记录不含原始回复的安全失败类别，并生成
`unknown/unverified/low` 的保守评估，用户仍可继续预览和发布。
公开 evidence Markdown 会丢弃仅含 `[]`、`{}`、`null` 的空日志行，不把无信号日志
扩散到 GitLab 附件。

## GitLab、GitHub 授权与安全

目标项目由 `zf.yaml` 的 `self_issue.target_project` 集中配置并锁定。GitLab 创建 Issue
需要 `api` OAuth scope；它比“仅创建 Issue”更宽，界面会明确提示。token 只保存在
Kernel Secret Provider，不进入 Web、Draft、事件、日志、Trace 或 artifact。

登录不是发布授权。只有绑定当前 snapshot、短 TTL、一次性的明确确认才能触发具体
上传或发布。Draft 内容、附件、目标、凭据主体、权限或脱敏摘要变化都会使旧确认失效。
最终 Preview 会保留所有用户字段；可选字段留空时显示
`(User did not provide this information.)`，并包含影响范围和评估置信度。原始 local-only
sidecar 路径不会进入 Issue；只有已确认并上传的附件以可点击 GitLab 链接出现。
这些最终链接使用完整的 Provider HTTPS URL，因此在本地 Preview 与 GitLab 页面中都指向
同一已上传字节；不会使用本地页面无法正确解析的相对 `/uploads/...` 地址。

GitHub 附件发布使用 ZaoFu GitHub OAuth App 的 Device Flow，不需要把 client secret 分发给
用户。管理员需在 OAuth App 设置中启用 Device Flow，并把公开 client id 配置为
`ZF_GITHUB_OAUTH_CLIENT_ID`；公开仓库使用 `oauth_scope: public_repo`，私有仓库必须显式使用
`repo`。对应 target 示例：

```yaml
github:
  authorization_domain: github.com
  project: owner/repository
  oauth_client_id: ${ZF_GITHUB_OAUTH_CLIENT_ID}
  auth_mode: oauth_device_flow
  oauth_scope: public_repo
```

Device code 保存在 0600 Kernel 事务文件，access/refresh token 仍只保存在 Secret Provider。
发布出来的 Issue 由完成授权的真实 GitHub 用户身份创建。凭据按 user、workspace、provider
和 authorization domain 保存，同一 workspace 的兼容项目只需授权一次。旧
`device_flow`/`ghu_` 凭据可以继续支持旧 GitHub App 路径，但不能上传媒体；切换 target 后
首次发布会要求重新 Connect。授权和 repository 只读 preflight 均通过后才创建 Issue。
GitHub 公共用户创建的 Issue 不能直接附带仓库 label。仓库内的
`.github/workflows/self-issue-labels.yml` 会在 Issue 创建后读取稳定 marker，只从白名单中
补上分类与严重程度 label。目标 GitHub 仓库必须预先创建下列 label（名称必须完全一致）：

- 分类：`runtime`、`kernel/state`、`provider/integration`、`web/ui`、`configuration`、
  `security`、`performance`、`test/regression`、`unknown`
- 严重程度：`p0`、`p1`、`p2`、`p3`

建议颜色依次使用：`5319E7`、`1D76DB`、`0052CC`、`0E8A16`、`C5DEF5`、`D73A4A`、
`FBCA04`、`BFDADC`、`D4C5F9`；P0–P3 使用 `B60205`、`D93F0B`、`FBCA04`、`0E8A16`。
发布完成后，当前 Draft、Save draft、Safe preview 和 Restart 进入只读，并显示不可变提示；
**Published & View** 仍可打开远端 Issue。Preview 从已发布 PublicationIntent 恢复，刷新后
仍显示实际发布的同一 Markdown。仅保留历史 `published_issue_ref`、但当前状态不是
`published` 的新一轮 Draft 不会被该 UI 锁误伤。

## 未知结果

若 Issue 响应丢失，PublicationIntent 进入 `outcome_unknown`，禁止普通重试；先按稳定
marker 查询对应的 GitLab 或 GitHub Provider。唯一匹配则恢复为 published，否则保持锁定。附件上传响应丢失也
会锁定，但 GitLab upload 没有等价 marker，因此只能由 Owner 提供 evidence refs 并通过
Controlled Action 裁决，避免重复上传或错误引用。
