# 145 · Verified Candidate 人工审核与 Forge 交付

> 状态：accepted
>
> 决策日期：2026-08-31

## 目标与已定决策

本设计补齐 design 144 中止于本地 `verified_candidate` 的后续交付链。首版面向
GitHub，但身份、sidecar、事件和状态合同保持 provider-neutral，后续 GitLab adapter
不得改变 Kernel 合同。

已确定：

- Issue 来源和代码交付目标当前都为 `rzwu-uisee/zaofu-self-issue`，但配置中保持两个
  独立身份，禁止从 Issue URL 推断代码交付仓库；
- 代码交付目标为 `https://github.com/rzwu-uisee/zaofu-self-issue.git` 的 `dev` 分支，
  不直接交付到远端 `main`；
- Agent 的 Review/Judge 与 Owner 人工审核是两个独立 gate；开发阶段允许同一人依次
  充当 ZaoFu Owner Reviewer 和 GitHub Reviewer，但必须留下两份独立 receipt；
- ZaoFu 在人工批准后创建本地、不可变的 review branch；人工负责 push、创建 PR、
  GitHub 审核和 merge；ZaoFu、Provider Agent 和 integration 都不得执行远端引用更新；
- 默认 merge strategy 为 squash；候选的原始 task/commit lineage 继续保存在 handoff；
- 目标 `dev` 的 pinned base 发生变化时 fail-closed，候选必须在新 base 上重建并重新
  验证，旧人工批准自动失效；
- 首版由人工登记 PR URL，ZaoFu 通过只读 GitHub API Refresh 同步状态；不增加 GitHub
  写权限；
- Web 与 Feishu Issue-only 操作的 Review、Prepare、Record PR 和 Refresh PR 先完成本地
  handoff/event，再把 GitHub 生命周期标签作为后台投影同步；其中 Prepare 仍同步校验
  远端 base，Record/Refresh 仍同步校验 PR 的仓库、分支和 SHA。PR 主体与 reviews 两个
  只读请求并行发起，避免串行网络等待叠加。Feishu 的 Start/Cancel 也跳过请求路径中的
  GitHub 刷新、附件下载和生命周期标签写入，使用已有镜像与事件账本快速返回。
- merge 后不自动关闭 Issue、不自动评论，由人工确认发布结果后处理。

## 生命周期

```text
verified_candidate
  -> owner_review_pending
  -> owner_changes_requested -> 新 Fix Run / 新 Candidate
  -> owner_rejected
  -> approved_for_pr
  -> publication_prepared
  -> 人工 push + 创建 PR
  -> pr_open
  -> pr_changes_requested -> 新 Fix Run / 新 Candidate
  -> pr_closed_without_merge
  -> pr_approved
  -> 人工 merge
  -> merged
```

`verified_candidate` 只证明 exact candidate 通过 ZaoFu 的机械与语义 gate；它不表示
Owner 已接受、代码已发布、PR 已创建或远端已合并。`merged` 只在只读同步证明目标仓库、
base branch、PR head identity 和 merge 事实一致后成立。

## 权威对象

### Candidate Owner Review

Owner review receipt 必须绑定：

- External Issue stable key 与 source revision；
- Task、Run、PDD/Candidate identity；
- candidate base SHA、head SHA 与 diff ref；
- candidate verification/judge event id 与 evidence digest；
- reviewer、verdict、reason 和时间。

建议事件族：

- `candidate.owner_review.approved`
- `candidate.owner_review.changes_requested`
- `candidate.owner_review.declined`

这些事件只能由 token-gated 确定性 Web action path 追加。Agent 的 `review.approved` 或
`judge.passed` 不能冒充 Owner receipt。

### Candidate Publication Handoff

`candidate-publication-handoff.v1` 是原子写入的 required sidecar，至少包含：

- source provider/repository/Issue identity 与 source revision；
- delivery provider、repository、HTTPS URL、base branch 和 branch prefix；
- candidate ref/base/head/diff、changed paths、task/commit lineage；
- verification commands、gates、evidence refs 和 unresolved risks；
- Owner approval event/ref；
- 本地 review branch、建议 PR title/body 和人工命令；
- lifecycle、PR identity 与最后一次只读同步结果。

事件只保存身份、digest、ref 与状态，不内联完整 PR body 或大证据。

### Forge Pull Request Receipt

人工创建 PR 后登记 URL。确定性服务必须校验 host、repository、PR number、base branch、
head branch 与 approved candidate SHA；校验成功才写 `forge.pull_request.recorded`。只读
Refresh 可追加 `forge.pull_request.synced`，并把 GitHub 状态映射为 provider-neutral
`pr_open`、`pr_changes_requested`、`pr_approved`、`pr_closed_without_merge` 或 `merged`。

## 本地 review branch

默认命名：

```text
review/github-issue-<number>-<candidate-sha前8位>
```

Prepare 操作在 repo-scoped git lock 下执行，并且：

- 只允许 approved exact candidate；
- 通过 `git update-ref` 创建/验证本地 ref，不 checkout、不 merge、不改当前分支；
- 已存在同名同 SHA 时幂等成功；同名不同 SHA 时 fail-closed；
- Prepare 通过只读 `git ls-remote --heads` 将目标仓库当前 `dev` SHA 与 receipt 的 base
  SHA 精确比较；不 fetch、不更新本地 remote-tracking ref，base 漂移时 fail-closed；
- 当前工作树的用户改动不被清理、stash、reset 或提交；
- 不配置 remote、不 fetch、不 push、不调用 GitHub 写 API。

人工交付包给出显式命令，但不执行：

```bash
git push https://github.com/rzwu-uisee/zaofu-self-issue.git \
  refs/heads/review/github-issue-<number>-<sha8>:refs/heads/review/github-issue-<number>-<sha8>
gh pr create --repo rzwu-uisee/zaofu-self-issue --base dev \
  --head review/github-issue-<number>-<sha8> --body-file <generated-pr-body>
```

## Triage 操作面

选中 `verified_candidate` Issue 后，详情页显示 Candidate Review：

- Issue/source revision、candidate ref/base/head/diff；
- changed paths、task/commit lineage、verification/gate 结果与风险；
- `Approve for PR`、`Request changes`、`Reject candidate`；
- 批准后显示 `Prepare Review Branch`；
- 准备后显示本地 branch、复制命令和 `Record PR`；
- PR 登记后显示只读 `Refresh PR`、GitHub 链接、base/head/merge 状态。

所有 mutation 都使用现有 Web action token；确定性服务按 exact source/candidate identity
提供幂等与 fail-closed 合同。Triage Web 只提交受控动作请求，不直接写 event、sidecar
或 git ref。

## Run 控制补充

Issue Triage 同时补齐此前保留的 Run 控制：

- `triage_queued`：`Cancel queued Triage`；
- `triaging` / `fixing` / `verifying`：`Manage Run`；
- active Run 可 `Pause after current dispatch`；paused Run 可 `Resume`；
- `Cancel permanently` 是终态，不可 Resume，且不会回滚已经写入本地 worktree 的文件；
- Fix Run 取消投影为 `fix_cancelled`，不得统一显示成 `triage_cancelled`；
- admission/cancel 竞争由现有 run-admission lock 串行化；已完成的 Run 返回冲突，不伪造取消。

风险提示必须使用独立红色 danger panel，并要求操作者勾选“理解不可恢复且不回滚本地文件”
后才能永久取消。Pause 使用 warning panel，明确只阻止后续 dispatch，已派发工作可以结束。

## 安全与失效

- code delivery repository/base 只能来自 `zf.yaml` 白名单；
- PR URL、GitHub response、Issue 内容与评论都是不可信输入；
- approval、handoff、PR receipt 全部绑定 exact source revision + candidate SHA；
- Candidate、Issue revision、base SHA、目标仓库或配置 digest 任一变化，旧批准和 handoff stale；
- GitHub 只读同步不得写 canonical state，必须发布 intent/event，由受控服务更新 sanctioned
  sidecar；
- 不记录 token，不把凭据放入命令、event、handoff 或 Web projection；
- `ship-candidate` 保留给原有本地 delivery flow，但 Issue Triage 不暴露或调用它。

## 实施切片与验证

1. 配置与 schema：增加 provider-neutral code delivery policy，并配置 GitHub repository/dev；
   verify：loader/schema/default/inheritance tests。
2. Owner review：实现 exact-candidate review service、事件与 sidecar；verify：approve/change/reject、
   stale revision/SHA、idempotency 和 auth tests。
3. Publication prepare：创建不可变本地 review branch 与 handoff；verify：同 SHA 幂等、ref
   冲突、base drift、dirty worktree preservation、无 remote mutation。
4. PR receipt/sync：登记 allowlisted PR URL并只读同步；verify：wrong host/repo/base/head、
   open/changes-requested/approved/closed/merged fixtures。
5. Web：Candidate Review、copy handoff、Record/Refresh PR 与生命周期；verify：API contract、
   frontend unit/typecheck/build 和浏览器交互。
6. Run controls 与 Triage labels：完成保留控制和本批次交互；verify：projection、action、筛选、
   tooltip、折叠、反选及浏览器回归。

## 明确排除

首版不实现 Agent/服务端 push、自动创建 PR、自动 GitHub review/merge、自动关闭 Issue、自动
评论、部署、重启、GitLab adapter，以及绕过 branch protection 或 GitHub 官方审核。
