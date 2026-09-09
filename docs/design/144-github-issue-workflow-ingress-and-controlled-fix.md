# 144 · GitHub Issue 触发、自动分诊与受控修复

> 状态：accepted
>
> 决策日期：2026-08-31

## 目标与已定决策

ZaoFu 首先打通 GitHub External Issue 到 Issue Workflow 的完整本地交付链路：新 Issue
自动进入只读分诊，操作者通过 GitHub `zaofu:ready-to-fix` 标签或 ZaoFu Web 的独立批准
授权修复，最终只生成本地 `Verified Candidate`，由人工受控合并。

已确定：

- Self-Issue 默认发布目标改为 GitHub；官方目标仓库保持由 `zf.yaml` 锁定；
- 首版只实现 GitHub ingress 与 GitHub Issue Triage，不实现 GitLab Issue Triage 或 GitLab
  webhook；现有 GitLab publication 能力不因此成为本链路的入口；
- ingress、source identity、sidecar 与事件 payload 保持 provider-neutral，后续 GitLab adapter
  不得改变 Kernel 合同；
- 新 Issue 自动触发只读 Triage，不自动获得源码写权限；
- Fix Run 由当前 exact proposal 的独立授权点火；
- 首版采用 `zf web` 进程拥有的单例 GitHub API 定时轮询，每 300 秒增量对账；Agent
  runtime 可离线，请求必须可排队并在 `zf start` 后恢复；
- 交付止于本地、已验证候选，不自动 push、创建 PR、merge、部署或重启。

首版假定 `rzwu-uisee/zaofu-self-issue` 映射到当前 ZaoFu Project 的代码根
`targetRoot: .`。该映射必须显式配置和仓库白名单校验，不能从 Issue 内容推断目标目录。

## 为什么不是“opened 后直接修复”

GitHub Issue 的标题、正文、评论和附件都是不可信外部输入，而且通常不包含可靠的
`repro_command`、`allowed_paths` 或完成标准。它不能直接冒充 `issue-candidate.v1`，也不能
绕过 TaskContract、Workflow proposal、approval 或 Completion Gate。

Warp 当前公开的 `oz-for-oss` 同样把 webhook、路由、Agent Run 和结果应用分层，并让新
Issue 先进入 triage；具备生命周期标签、Agent assignment 或严格自动准入资格后才进入实现：

- <https://github.com/warpdotdev/oz-for-oss/blob/main/docs/architecture.md>
- <https://github.com/warpdotdev/oz-for-oss/blob/main/core/routing.py>

ZaoFu 沿用“事件入口 + 显式准入 + 人工交付”原则，但所有 canonical 状态和副作用仍由
ZaoFu 的 EventWriter、Store 与 Controlled Action 合同拥有。

## 领域对象与权威边界

### External Issue Source

`external-issue-source.v1` 是 provider-neutral、不可变 revision sidecar。至少包含：

- `provider`：首版只允许 `github`，合同预留 `gitlab`；
- provider host、稳定 repository/project identity、Issue number/IID 与公开 URL；
- title、state、author、labels、assignees、body/comments/attachments 的受控引用与 digest；
- provider `updated_at`、source revision、采集时间和来源 delivery/ref；
- 项目映射、目标代码根与来源可信度；
- Self-Issue marker（存在时）及其 publication identity。

Issue 正文、评论和大附件保存在 sanctioned sidecar 中；`events.jsonl` 只保存稳定身份、
revision、refs、digests、因果关系和裁决，不内联大正文。

### Issue Mirror

Issue Mirror 继续是可重建的只读 Web 投影，不是 Workflow truth。后台 Poller 和手动 Refresh
可以更新 Mirror，但必须通过同一 ingress service 发布 workflow intent；任何 integration route
都不得直接写 `kanban.json` 或其他 canonical business state。

### Issue Intake Task

首次接纳一个 External Issue 时，由确定性 consumer 通过 `TaskStore` 创建唯一的
Issue Intake Task，并用 `EventWriter` 追加 `task.created`。它的 contract 只承诺完成只读
分诊并生成绑定当前 source revision 的修复候选合同，不宣称已经知道代码写入范围或复现命令。

Triage Run 输出并由 Kernel 校验：

- classification、severity、duplicate/needs-info 判断；
- 可复现步骤与受控 `repro_command`；
- `allowed_paths`、风险边界和预期改动规模；
- acceptance、verification、证据 refs；
- 建议的 active Issue route、tier 与 exact Workflow proposal。

只有形成合格、current 的修复 TaskContract 后，Fix Admission 才可用。Triage 不得虚构缺失
事实；信息不足时状态进入 `needs_info`，不能产生可批准的 Fix proposal。

## 两段式 Workflow

产品上是一条 Issue Workflow；Kernel 中是两个独立 admission 的 Run：

```text
GitHub API incremental reconciliation
  -> 固定仓库身份校验、ETag/since 对账、revision 去重
  -> 原子保存 external-issue-source.v1
  -> external_issue.received
  -> 唯一 Issue Intake Task
  -> 自动只读 Triage Run
  -> needs_info | current Fix proposal
  -> awaiting_fix_approval
  -> GitHub label intent 或 ZaoFu Web approval
  -> ControlledActionService 校验 exact proposal/current revision
  -> workflow.invoke.requested
  -> Fix Run -> Verify/Judge/Completion Gate
  -> verified_candidate | failed | blocked
  -> 人工检查并受控合并
```

自动 Triage route 必须是注册、只读、无 Forge 写权限的 route。它不能复用一个会继续落入
writer lane 的整段 IssueFlow 后再临时暂停；自动 Run 与可写 Fix Run 必须在 admission 边界上
分离。Fix Run 复用现有 `issue.requested` 入口和 active IssueFlow 能力。

## GitHub 对账路由

首版处理以下事件：

| GitHub revision 变化 | Kernel 意图 | 不允许的隐式行为 |
|---|---|---|
| 新 Issue | 接纳 source revision、创建/恢复 Intake Task、排队 Triage | 直接启动 Fix |
| title/body 更新 | 创建新 source revision，使旧 proposal stale，重新分诊 | 静默沿用旧批准 |
| 评论/附件更新 | 更新当前上下文；按策略重新分诊 | 把评论命令当系统指令 |
| 出现 `zaofu:ready-to-fix` | 对当前 revision 生成 approval intent | 标签本身绕过 exact proposal |
| 移除批准标签 | 在 Fix 尚未点火时撤销待处理 intent | 杀死已运行 Run |
| Issue 关闭 | 关闭未启动 intake/proposal；运行中交由受控取消策略 | 直接终止 provider 进程 |
| 手动 Refresh | 对账并补发缺失 ingress revision | 每次刷新重复建 Task/Run |
| 手动 Start Triage | 单独拉取并准入指定 Issue 的 current revision | 批量准入历史 Issue |

轮询固定使用 `self_issue.targets.github.project`，首次成功同步记录 activation 时间；默认只有
activation 之后创建的新 Issue 自动分诊，已有 open Issue 继续作为 Mirror 展示并允许人工处理。
首版不要求公网域名、反向代理或固定 HTTPS 隧道。现有 webhook route 仅保留为可关闭的镜像
兼容入口，不是 Workflow ingress；未来启用 webhook adapter 时必须复用同一 source/revision 合同。

Triage 顶部工具栏提供 token-gated `Start Triage`。操作者可选择当前 Mirror Issue，或输入配置
仓库内的 GitHub Issue URL/编号。后端固定校验 `github.com`、仓库 identity 和 Issue 编号，只拉取
该 Issue 及其评论，然后以 `admission_mode=manual` 接纳 current revision。人工准入可以绕过
activation 时间边界，但不能绕过仓库白名单、source revision 去重、只读 Triage route 或后续
Fix proposal 审批；相同 revision 重复点击返回 `already_queued`，不创建第二个 Task/Run。

## 批准合同

批准标签固定为 `zaofu:ready-to-fix`。产品合同允许两种批准入口：

1. GitHub 中具备仓库写权限的人工操作者添加标签；
2. ZaoFu Web 展示 current proposal 后，操作者点击独立 Approve/Start。

标签只是 approval intent，不直接等于授权。当前无凭据的公共 API 轮询只能观察标签当前值，
不能可靠证明“谁添加了标签、当时是否具备仓库写权限”，因此首个轮询实现会记录 intent 并将
current exact proposal 展示到 ZaoFu Web，仍由 Web 独立批准点火。后续若配置了 GitHub
authenticated actor audit 与明确 actor allowlist，才允许把标签 intent 升格为等价授权。
Controlled Action 必须在 apply 前确认：

- label event 来自允许的仓库和具备权限的人工 actor；自动化 actor 默认拒绝；
- proposal 绑定当前 External Issue source revision、TaskContract digest 和 active route；
- Issue 未关闭、proposal 未过期且不存在同 Issue 的 active Fix Run；
- approval ref、proposal event id/digest 与 action token 满足现有 exact proposal 合同。

GitHub 标签不携带 proposal digest，因此 Kernel 只能在“唯一 current proposal 且 Issue 自分诊后
未变化”时将 label event 绑定为 authorization ref。不存在 proposal、有多个候选或 source
revision 已变化时，状态回到 `triage_queued` / `awaiting_fix_approval`，不得猜测批准对象。
Provider Agent 永远不能读取 action token。

## 幂等、重放与并发

External Issue 稳定键：

```text
github:<repository_id>:<issue_number>
gitlab:<project_id>:<issue_iid>        # 仅合同预留，首版无 adapter
```

必须同时满足：

- source revision 去重负责 API 重叠窗口和重复 Refresh；External Issue 稳定键负责跨 Poll、Refresh 和
  `self_issue.published` 的业务去重；
- 同一 External Issue 只有一个 Intake Task；更新产生 revision，不产生第二个 Task；
- 同一 source revision 只有一个 current Triage Run 和一个 current Fix proposal；
- 同一 Issue 同时最多一个 active Fix Run；重复标签、重复批准与 runtime 重启可安全重放；
- Self-Issue publication 与随后到达的 GitHub webhook 通过远端 repository id/number 和稳定
  marker 收敛为同一 External Issue；先到者 claim identity，后到者只补证据；
- 定时轮询中断时，手动 Refresh 使用同一 claim/ingress service 补齐，不创建平行状态机。

## Runtime 离线与恢复

`zf web` 的后台 Poller 只负责安全持久化 sidecar、Intake Task 与事件，它不等待 Agent Provider。
若 `zf start` watcher 未运行，Web 显示 `queued_runtime_offline`，事件保持 pending。Watcher 启动
后从 canonical ledger 消费；用户不需要重新加标签、重新发布 Issue 或再次批准 current
proposal。

浏览器不拥有 GitHub 轮询：Triage 页面每 10 秒只读取本地 Mirror，手动 Refresh 才显式请求
立即对账。后台同步与 Refresh 共享文件锁；GitHub 网络等待在线程中执行，不占用 FastAPI event
loop，因此不会阻塞 Triage 页面。若整个 `zf web` 进程也停止，停机期间的新 Issue 会在下次
启动后的首次增量对账中补齐。

恢复测试必须覆盖 Poll 已接纳但 runtime 未启动、Triage 中途重启、批准后点火前重启、
以及 `workflow.invoke.requested` 已写入但尚未消费四种窗口。

## 用户可见状态

Triage 页面统一投影以下生命周期：

```text
mirrored
  -> triage_queued
  -> triage_cancelled
  -> triaging
  -> needs_info | awaiting_fix_approval
  -> fix_queued
  -> fixing
  -> verifying
  -> verified_candidate | blocked | failed
```

`closed` 是 External Issue provider state，不替代 Workflow lifecycle。页面应同时显示 provider
state、Workflow state、Task id、current revision、proposal/run id 和最近 diagnostics。
当选中 Issue 的状态为 `triage_queued` 时，顶部操作切换为 token-gated `Cancel Triage`，复用
Kernel `run-cancel` ControlledAction。取消可在 runtime 离线时完成并阻止未来 admission；它只
终止该 revision 的 Run，不删除 Mirror、Intake Task、source sidecar 或 append-only 审计事件。

第一版不由 ZaoFu 自动添加 GitHub 状态标签、评论或创建 PR；GitHub 只提供 Issue source 和
人工批准标签。状态回写可以在后续作为独立、可关闭的 controlled side effect 增加。

## Verified Candidate 合同

Fix Run 只能在隔离 worktree 和 TaskContract scope 内修改。`verified_candidate` 至少绑定：

- External Issue stable key、source revision 与 Task id；
- base ref/SHA、candidate ref/SHA 或等价不可变本地引用；
- changed paths、verification commands、gate 结果和证据 refs/digests；
- unresolved risks、人工检查提示和明确的 merge target。

达到该状态不表示代码已合并、已推送、已创建 PR、已部署或 Issue 已关闭。候选由人工通过
现有受控 git/merge 流程处理；Provider Agent 和 webhook integration 都没有远端 push/merge
权限。

## 配置边界

首个实施批次需要：

- 将 Self-Issue `provider`、兼容顶层 `authorization_domain`、`target_project`、OAuth 默认值
  与 GitHub target 对齐；`default_publication_mode` 保持 `github`，保留 provider-neutral
  `targets` 结构；
- 增加 External Issue ingress policy：enabled、provider、仓库 identity allowlist、project/
  targetRoot mapping、approval label、authorized actor policy、并发与重试上限；
- 在当前 Project 的 active route catalog 中注册 read-only Triage route 和 Issue Fix route，
  不替换现有 PRD route，也不创建第二份 `zf.yaml`/state dir；
- GitLab 使用同一配置 schema，但 ingress `enabled: false` 且无 adapter；未知 provider
  fail-closed。

integration 只能请求 `EventWriter`/controlled action；不能直接写 TaskStore、Workflow Run、
candidate 或 completion 状态。确定性 consumer 才能使用 canonical Store。

## 安全要求

- Issue/评论/附件一律作为数据，不作为 prompt 或权限指令；
- prompt 构造必须明确隔离可信系统目标与不可信 Issue 内容；
- Web Forge 凭据存放在当前 `ZF_WORKSPACE_HOME` 隔离的 workspace registry 下，同一 Web
  用户在该 workspace 内切换项目时复用授权；凭据键仍按用户、provider 与
  `authorization_domain` 分隔。未配置 workspace registry 时保留项目级凭据存储；Issue
  草稿、OAuth 事务、Triage/Fix 状态和其他 canonical state 始终保持项目级隔离；
- webhook secret、OAuth token、action token 和 provider credential 不进入 sidecar、事件 payload、
  Agent prompt 或 Web 投影；
- 仓库/project identity 和 targetRoot 必须来自配置白名单，禁止由 Issue 链接或正文覆盖；
- fork、未知 repository、未知 provider、自动化 actor、stale proposal、篡改 ref/digest、超出预算
  或 scope 的请求全部 fail-closed；
- Triage 并发、Fix 并发、单 Issue 重试和全局队列必须有机械上限。

## 实施切片与验证

1. **默认目标与 schema**：对齐 GitHub 顶层默认值，增加 provider-neutral ingress policy；
   verify：config loader/schema、Project init/inheritance 与默认发布 Web 测试。
2. **Ingress contract**：增加 source sidecar、stable identity/claim store 与事件合同；
   verify：原子写入、digest/currentness、GitHub/GitLab identity fixture 和 known-event 测试。
3. **GitHub producer**：以 `zf web` 单例 Poller 复用增量镜像逻辑，并让 Refresh 进入同一
   ingress service；verify：opened/edited/comment/labeled/closed、重叠窗口、错仓库、限流和
   reconciliation 测试。
4. **Intake 与自动 Triage**：创建唯一 Intake Task，注册只读 route，runtime 离线可排队恢复；
   verify：Task/Event 因果链、无写权限、needs-info、重启/重放与 active route 测试。
5. **Fix Admission**：生成 current proposal，把 Web 与 GitHub label intent 接到同一
   Controlled Action；verify：actor 权限、stale revision/digest、撤销、重复批准和单 active Run。
6. **Fix 与候选交付**：点火现有 IssueFlow，输出本地 Verified Candidate；
   verify：repro red→green、scope、direct callers/shared contracts、candidate evidence 和无 remote
   push/merge 副作用。
7. **Web 投影与操作文档**：显示双状态、队列、proposal、Run 与 diagnostics；
   verify：frontend build/typecheck、affected component/browser tests，以及 Web/API 边界测试。

跨 EventLog、TaskStore、config、workflow admission、orchestrator 与 recovery 的批次必须执行
impact-closure tests、`scripts/dev-premerge-gate.sh` 和相关 deterministic mock E2E。GitHub
真实 provider 测试属于显式外部 tier，不能用普通单元测试结果冒充。

## 明确排除

首版不实现：GitLab Issue Triage/webhook、任何 Issue 全自动修复、GitHub 自动评论/状态标签、
自动分支/PR/push/merge、自动关闭 Issue、部署、重启、飞书通知，以及从未知仓库映射本地代码。
