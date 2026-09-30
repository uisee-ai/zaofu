# 148 · Self-Issue 与 External Issue 待实现/修复总清单

> 状态: active
> 汇总日期: 2026-09-03
> 适用源码根: `/home/uisee/zaofu_v7`

本文是当前讨论中仍未完成事项的唯一汇总入口。它把已经确定的边界、尚未实现的
功能、需要修复的源码缺口、验收条件和仍需人工确认的决策放在一起。本文只描述
ZaoFu v7 的实现范围；产品项目的源码、Web、runtime 和后台服务不属于 Self-Issue
或 External Issue 修复链路的依赖。

截至本轮实现，已落地：`unverified_candidate` 结论及其 admitted-result 传播、禁止把
“没有动态 Playwright/用户产品环境”单独判为 `needs_info`、Issue 图片链接的 digest
绑定 sidecar、同一 revision 取消后复用并重新激活 Task、Issue 详情 ID 跳转、状态分组
图标/颜色与单状态筛选文案、后台自动显示 `/issue` 补充卡片，以及 OAuth 授权完成后的
自动发布。其余条目仍按下文验收条件保留，未宣称完成。

## 一、已经确定的总边界

### 1. 源码与运行状态边界

- 所有 `/issue`、External Issue Triage 和后续 Fix 都针对
  `/home/uisee/zaofu_v7` 源码。
- 新建项目时默认继承 ZaoFu v7 的 External Issue workflow、角色、stages/tasks
  和 Self-Issue target-root 规则。
- 产品项目的目录可以保存独立的 runtime state，但不能成为源码目标，也不能成为
  官方修复的必要环境。
- 官方不要求、也不尝试启动或访问用户的产品 Web、产品 runtime 或产品后台。

### 2. Web 与 runtime 边界

ZaoFu Web 和 ZaoFu runtime 是两种独立能力：

| 能力 | Web | ZaoFu runtime |
|---|---:|---:|
| 查看已有 Overview、Loop、Tasks/Board 投影 | 必须 | 不需要 |
| 静态 Playwright 页面检查 | 必须 | 不需要 |
| 消费事件、Start Triage、worker/orchestrator 调度 | 可选 | 必须 |
| Resume、实时刷新、热启动、动态证据 | 必须 | 必须 |
| `/home/uisee/zaofu_v7` 源码检查和 focused tests | 不需要 | 不需要 |
| 用户产品项目服务 | 不参与 | 不参与 |

`zf web` 只提供 Web/API 和读投影；`zf start` 才启动 watcher、Orchestrator 和事件
调度。报告不能把 runtime 停止写成“所有页面无法验证”，也不能把用户产品环境
不可访问写成 `needs_info`。

### 3. 交付边界

Issue workflow 的默认终点仍然是：

```text
GitHub Issue
  -> 只读 Triage
  -> Fix proposal
  -> 人工批准
  -> Fix Run
  -> verified_candidate
  -> 本地 review branch
  -> 人工执行 push / 创建 PR / review / merge
```

不自动 push、merge、部署或关闭 GitHub Issue。公网 webhook 仍不纳入本批次；
Feishu 已作为单独的投影/意图入口接入，但不获得绕过受控动作和
canonical Store 的独立写权。

## 二、已完成或已有设计、无需重新发明的事项

以下能力已经有实现或已有被接受的设计，本清单只要求在最终回归中确认，不作为新
需求重复开发：

- GitHub polling ingress；不采用公网 HTTPS webhook 作为当前方案。
- GitHub Issue 的手动 Start Triage、历史 Issue 接纳和 runtime 离线排队。
- Triage/Task ID 历史显示，以及按 Task ID、Triage ID 搜索。
- 失败投影与活动 Run 的区分：活动 Run 显示 Manage Run，真实终态才允许重新 Start。
- GitHub 默认发布与 Verified Candidate 人工交付设计。
- 新项目初始化时物化 `external-issue-triage/v1`、
  `external-issue-triage`、`external-issue-triage-verifier` 和对应 stages/tasks 的设计。
- Self-Issue 源码目标根固定为 ZaoFu v7，runtime state dir 仍由项目配置管理。

当前工作树中与上述 target-root 规则相关的源码变更仍是未提交工作，不应与本清单
中的新修复混淆；提交前必须按 AGENTS.md 的显式 pathspec 规则检查 staged 文件。

## 三、待实现/修复工作包

### A. Triage Run 生命周期与 Manage Run

**目标**：解决 queued、活动、失败、完成、取消之间的状态误判，并让人工操作对并发
边界有清晰结果。

待实现：

1. `triage_queued` 显示 **Cancel queued Triage**。
2. `triaging`、`fixing`、`verifying` 显示 **Manage Run**。
3. Manage Run 提供：
   - **Pause after current dispatch**：阻止新的 dispatch，已派发工作可完成；之后可 Resume。
   - **Resume**：从同一 Run/checkpoint 恢复，不创建伪造的新运行记录。
   - **Cancel permanently**：不可恢复，明确说明不回滚已经写入本地 worktree 的文件。
4. 取消按钮与 workflow 刚好进入下一状态的竞态必须幂等：
   - 若取消先被 Kernel 接纳，后到的 dispatch 必须被阻止；
   - 若 dispatch 已先被 Kernel 接纳，取消只能标记后续不再派发，并显示“当前 dispatch
     可能继续完成”；
   - 禁止出现 `rejected -> run-cancel` 这类非法转换。
5. Fix Run 取消投影为 `fix_cancelled`，不能统一投影为 `triage_cancelled`。
6. `failed` 只有在真实 Run 终态成立时才允许重新 Start；活动 Run 即使发生失败事件，
   仍应优先显示 Manage Run，避免队列被占用却误开放第二个 Triage。

验收：

```text
queued -> cancel       -> triage_cancelled
active -> pause        -> paused -> resume -> active
active -> cancel       -> fix_cancelled / triage_cancelled（按 Run 类型）
failed + active run    -> Manage Run
failed + terminal run  -> 可创建新的 retry attempt
```

### B. External Issue Triage 结论放宽与 `needs_info` 修复

**目标**：Issue 进入 GitHub 后，官方应尽可能独立完成源码检查、测试和 Fix proposal。
用户的产品项目、用户 Web、用户 runtime 和用户后台都不是修复前置条件；只有确实
无法由 Issue、artifact catalog、ZaoFu v7 源码和官方受控测试推导出的必要事实，才允许
要求用户补充。

当前问题：

- `self_issue_policy.py` 只写了“证据充分才 `ready_to_fix`”，没有可执行的充分性标准；
- 用户、worker、orchestrator、kernel 的日志、图片、分析和源码位置不一定进入同一
  个 workflow attempt manifest；
- “没有动态复现”被错误地当成“信息不足”；
- `external_issue_triage_status` 与内核认可的
  `external_issue_disposition` 之间存在传播缺口；
- 只有 `ready_to_fix` 和 `needs_info` 两个结果，没有中间状态；
- 内部已经产生的分析如果没有进入 admitted control result，不能影响最终 disposition。

待实现：

1. 把输入分为四类，明确哪些可以由系统独立推导：

   | 输入 | 是否必须用户提供 | 说明 |
   |---|---:|---|
   | 可观察症状/影响 | 是 | 至少能知道“发生了什么” |
   | 预期行为或验收目标 | 是，允许由影响推导 | 用户未写明时，Orchestrator 可从现有行为/测试推导并标注推断 |
   | 复现步骤 | 否 | 可由源码、事件、日志和 focused test 候选补齐 |
   | 用户产品环境 | 否 | 永远不作为 Self-Issue/External Issue 前置条件 |
   | 用户 Web/runtime | 否 | 不远程访问、不远程启动 |
   | 日志、截图、分析文件 | 否 | 用户未上传时由 ZaoFu artifact catalog 和官方受控运行补齐 |
   | source root | 否 | 固定为 `/home/uisee/zaofu_v7` |

2. 在 `needs_info` 前执行一次有界的内部补全顺序：

   ```text
   Issue 正文/comments
     -> artifact catalog（用户、worker、orchestrator、kernel、Playwright）
     -> ZaoFu v7 committed source inspection
     -> 已有事件/Trace/log evidence
     -> 最多 3 个受控 focused-test/reproduction candidates
     -> 证据一致性和 source revision 校验
     -> 仍缺不可替代事实时才 needs_info
   ```

   内部补全不得访问用户机器或产品服务，也不得为了“证明足够”无限增加 Agent 尝试。

3. 定义最低可修复条件：

   - 有可观察症状或影响；
   - 有 source revision，且范围固定为 `/home/uisee/zaofu_v7`；
   - 至少有一个可校验的证据锚点：日志、事件、截图、artifact、源码位置或测试线索；
   - 能形成一个有界的修复目标和验证目标。

   满足以上条件即可生成 `ready_to_fix` 或 `unverified_candidate`，不能因为缺少用户环境
   或动态 Playwright 自动降级为 `needs_info`。

4. `needs_info` 只允许使用以下阻断原因码：

   ```text
   missing_observable_symptom
   missing_expected_behavior
   no_safe_scope_or_source_binding
   evidence_unreadable_or_digest_mismatch
   contradictory_required_facts
   ```

   以下原因码禁止单独触发 `needs_info`：

   ```text
   user_product_environment_unavailable
   user_web_unavailable
   user_runtime_unavailable
   no_dynamic_playwright
   no_live_user_logs
   root_cause_not_proven
   ```

5. 增加 `unverified_candidate`：线索、范围和源码位置足够，但根因或动态验证仍未完全确认，
   交给人工决定是否继续 Fix；不要求用户重新提供产品环境。
6. `ready_to_fix` 允许带验证缺口：

   ```json
   {
     "outcome": "ready_to_fix",
     "confidence": "medium",
     "verification_status": "partial",
     "verification_gaps": ["未执行动态 Playwright 验证"]
   }
   ```

7. `needs_info` 必须生成结构化补充卡片；如果报告只提供了
   `external_issue_triage_status=needs_info`，Kernel 应生成安全的默认问题，而不是
   静默丢弃该结论。
8. `external_issue_triage_status`、`external_issue_disposition`、内部分析和 artifact refs
   必须在 admitted control result 中保持一致；如果证据丰富但字段传播失败，应修复契约
   传播，而不是向用户提问。
9. `run.completed`、`run.goal.completed`、`workflow.result.available` 等终态必须参与
   External Issue lifecycle 判断。

验收：

- 有 Issue 正文、复现步骤、日志和明确源码位置，但没有用户产品环境时：不得自动判为
  `needs_info`。
- 没有动态 Playwright 时：显示 `dynamic_validation=not_run`，不生成用户补充卡片。
- 没有用户写出的复现步骤，但系统可以从源码、事件或测试构造有界候选时：继续内部验证，
  不立即询问用户。
- 只有出现允许的阻断原因码时：生成最小化补充卡片，且提交后产生新的 revision 和 retry。
- 每一次 `needs_info` 都必须在报告中列出：已经完成的内部检查、已经读取的 artifact refs、
  缺失的不可替代字段，以及为什么该字段无法由系统推导。

### C. Web/runtime 分层验证与停止状态修复

**目标**：修复“runtime stopped”笼统提示，让报告准确说明哪一项验证没有执行。

待实现：

1. 分别投影：

   ```text
   Web Dashboard: live / unavailable
   ZaoFu runtime watcher: live / stopped
   ```

   不引入、也不检查产品项目 backend 状态。
2. 静态冷启动 Playwright 只检查 ZaoFu Web 已有投影，不要求 runtime watcher。
3. 热启动、实时事件、Resume 和调度验证要求 ZaoFu Web + ZaoFu runtime。
4. 报告改为验证矩阵：

   ```text
   Overview 静态投影       passed / unavailable
   Loop 静态投影           passed / unavailable
   Tasks/Board 静态页面    passed / unavailable
   实时事件刷新            passed / not_run_runtime_stopped
   热启动恢复              passed / not_run_runtime_stopped
   ```

5. 启动 runtime 后，旧的 `waiting_for_runtime` 必须自动重新检查并进入
   `assessment_requested`；报告记录新的 `observed_at`，旧的 stopped 只作为历史观察。
6. Web/API 页面可以在 runtime 停止时继续查看历史 projection；不得把页面可读性和事件
   调度能力混为一个状态。

### D. `/issue` 证据包与 artifact catalog

**目标**：让用户、worker、orchestrator、kernel 和 Playwright 产生的证据进入同一条
可审计输入链路。原设计详见 `146-external-issue-evidence-package.md`，本工作包将其
从 defer 方案推进到实现。

待实现：

1. 统一登记以下来源：
   - `/issue` 用户上传的日志、图片；
   - Issue 正文、评论中的图片链接；
   - worker 日志；
   - orchestrator/runtime 诊断；
   - kernel 事件、Trace、源码位置；
   - Playwright 截图。
2. 每个 artifact 写入不可变 sidecar/catalog descriptor：

   ```text
   ref, sha256, content_type, byte_count, source,
   task_id, triage_id, source_revision, captured_at, redaction_status
   ```

3. 为每个 Triage/Revision 生成不可变 manifest，workflow attempt manifest 必须携带
   `body_ref`、`comments_ref` 和 `attachment_refs`。
4. 附件进入 worker 受控读取范围；不能依赖用户浏览器路径、产品项目路径或未校验的
   远程 URL。
5. GitHub 二进制附件发布采用受控证据目录和 manifest/comment 链接；不直接把二进制塞进
   Issue 正文，不写入 dev 源码分支。
   GitHub CLI 自动上传图片/视频要求 `gh >= 2.99.0`，且 GitHub 当前只接受 OAuth
   token、classic PAT 或 fine-grained PAT；GitHub App user token（`ghu_`）只能继续发布
   Issue/文本 comment。能力不足时必须先发布带独立 fallback marker 的脱敏文本 comment，
   把媒体状态保留为可恢复失败，不能静默丢失整个 comment 或重复创建 Issue。
6. 下载失败、digest 不匹配、脱敏失败或权限不足必须 fail-closed，并给出具体证据缺口。

验收：

- worker 能在受控 manifest 中读取用户日志、图片和内部分析 refs；
- 同一个文件被重复提交不会覆盖旧 sidecar，而是通过 digest 幂等收敛；
- 下一轮 Triage 能读取上一轮补充证据；
- artifact 不会泄漏 token、Cookie、Authorization header、个人绝对路径或未脱敏原文。

### E. Retry attempt、revision 与终态识别

**目标**：人工取消、真实失败或已完成但 `needs_info` 的 Run 重新开始时，生成新的
Triage/Task attempt，而不复用旧运行身份。

待实现/核对：

1. `run.completed`、`run.goal.completed`、`workflow.result.available`、
   `run.cancelled`、`run.failed`、`run.goal.blocked`、`workflow.invoke.rejected` 和
   `run.admission.rejected` 均进入终态判断表。
2. 新 retry 必须拥有新的 `workflow_run_id` 和 triage attempt；旧 Run 保留审计历史。
3. retry payload 必须绑定：

   ```text
   retry_of_workflow_run_id
   source_key
   source_revision
   task_id
   triage_attempt
   ```

4. 已完成但结论为 `needs_info` 的 revision 可在用户提交补充后创建新的 retry；不会把
   `needs_info` 永久锁死在旧 Run 上。
5. 正在运行或仅有失败事件但未到终态的 Run 不允许再次 Start；只能 Manage Run。
6. 新 retry 的 Triage/Task ID 可搜索、可在 Issue 详情的 Triage 历史中查看。

### F. Issue 详情与状态展示补齐

**目标**：让操作员能够区分 Issue、Task、Triage、artifact 交付和 Fix 状态。

待实现/核对：

1. Issue 详情展示当前 Triage ID、Task ID、source revision 和完整 Triage history。
2. `artifact delivery passed` 与 `triage ready_to_fix` 分开显示，不能用一个绿色状态代替
   两个不同事实。
3. 同时显示：

   ```text
   provider state
   workflow lifecycle
   triage disposition
   artifact delivery
   verification gaps
   current run / retry chain
   ```

4. 状态筛选继续使用现有 States 下拉框，Queued 类状态可以通过 `Queued` 前缀聚合；不
   新增独立筛选框。
5. `needs_info` 卡片显示明确的当前轮次、最大轮次、问题来源和提交后会创建新 retry 的
   提示。

### G. Start Triage → merge 全链路审查

**目标**：在上述修复后，重新按实际代码和事件契约核对完整链路，而不是只验证单个页面。

审查范围：

1. GitHub polling/手动 Start Triage → source revision 和 artifact manifest；
2. Triage workflow admission、Run Contract、workflow metadata 和角色 activation；
3. worker/orchestrator/verifier 结果进入 Kernel 的 control result；
4. `needs_info`、`ready_to_fix`、`unverified_candidate`、failed、blocked 和 retry；
5. Fix proposal exact approval、Fix Run、verifying 和 verified candidate；
6. 本地 review branch、人工 push、PR URL Record、PR refresh、merge 投影；
7. runtime 重启、pause/resume、取消竞态和 Web/API 读投影；
8. 权限、路径、digest、secret redaction、artifact sidecar 和事件幂等。

输出必须包含：

- 事件类型 → payload/schema → projector/store → Web/API 的覆盖矩阵；
- 仍未阻断但需要标注的缺口；
- 可复现的源码 bug 与验证命令；
- 未覆盖的真实 Provider、host、Docker Playwright 层，并明确不能冒充已验证。

## 四、明确暂不实现/暂不纳入本批次

- Feishu 多维表格显示、提醒和双向同步；
- GitLab Issue ingress；
- 公网 HTTPS 反向代理、cloudflared tunnel 和 webhook ingress；
- 用户产品项目后台、用户产品 Web、用户产品 runtime 的远程启动或远程复现；
- 自动 push、自动创建 PR、自动 merge、自动关闭 GitHub Issue；
- 公开证据仓库或无人工确认的公开附件托管。

## 五、仍需人工确认的决策

当前真正会改变实现的决策只有以下几项：

1. 是否同意将 `unverified_candidate` 作为正式的第三种 Triage 结论；
2. artifact catalog 的 GitHub 证据目录是否使用现有 Issue 仓库的专用目录，还是延后到
   独立证据仓库（默认建议：现有仓库专用目录、独立 manifest、人工确认）；
3. `needs_info` 最大轮次是否继续保持 3 轮；达到上限后是否固定为
   `needs_info_exhausted` 并停止自动 retry；
4. Manage Run 的永久取消是否对所有 Run 类型统一不可恢复，并始终保留本地文件。

除以上决策外，Web/runtime 与用户产品环境分离、官方源码固定为 v7、官方人工审核后
交付 verified candidate 已经确定，不再作为实现分支选项。

## 六、推荐实现顺序与验收闭环

按以下顺序实施，避免先修 UI 而继续产生错误的 canonical 状态：

1. 先修终态、retry、活动 Run/failed 投影和 Manage Run 状态机；
2. 再修 `external_issue_disposition`、`needs_info` 门槛和
   `unverified_candidate`；
3. 实现 artifact catalog、manifest 和 `/issue` 附件输入链路；
4. 实现 Web/runtime 分层验证和验证矩阵投影；
5. 补齐 Issue 详情、状态标签和 Triage history 展示；
6. 执行 Start Triage → Fix → verified candidate → PR/merge 的影响闭包审查；
7. 运行 focused backend tests、Web typecheck/build、`scripts/dev-premerge-gate.sh` 和
   相关 Docker Playwright（如环境可用）；
8. 展示完整实际 diff，确认后再按显式 pathspec 提交；不自动 push。

每个工作包都必须满足：源码变更、直接回归测试、事件/Store/API 边界测试，以及一条
可操作的验收命令。未完成或仅保留设计的部分不能标记为已修复。

## 七、Feishu 运行时边界收口（2026-09-15）

### Issue 生命周期多维表格投影（2026-09-29）

在现有 Feishu 卡片投影之外，`zf feishu sync-issue-table` 将 Portable
Issue Session、外部 Issue/Triage、候选 PR 和 `delivery.merged` 投影到独立
Issue 表。该投影只读取 Session/Issue Store 与 `events.jsonl`，通过
`FeishuSyncLedger` 幂等更新，并由 `EventWriter` 记录同步事件；不会把 Feishu
写入当作 Issue canonical state。Issue 表使用真实日期字段并提供 Grid 与
Gantt 视图，详细报告继续通过卡片/Web 受控读取。

- Feishu 的 Issue/Self-Issue 适配器只归一化不可信消息/卡片输入，只读查询读取
  canonical 投影；所有写操作均经 `EventWriter` 和 `ControlledActionService`。
- Issue Triage 控制卡片在首次发送和后续更新时均设置 `config.update_multi=true`，
  满足飞书按消息 ID 更新共享卡片的要求。取消排队后，投影原位更新同一条消息，
  显示取消状态并移除“取消排队”；接口回读正确不等同于客户端显示已完成验收。
  历史非共享卡片不能仅依赖相同 PATCH 的成功回执认定修复，需要单独验证或替换。
- `self_issue.assessment.requested`、`artifact.delivery.verified` 和 `run.resumed`
  是外部/受控产生的机械边，必须唤醒 Layer 1 housekeeping；它们同时属于
  `LAYER2_NOISE_EVENTS`，除非角色显式订阅，不得因投影/调度事实消耗 Layer 2 turn。
- 同一 External Issue revision 的终态 Task 重试必须通过
  `TaskContractAuthorityService.replace(..., reopen_terminal=True)` 的 CAS 和 receipt 边界；
  禁止适配器直接调用 `TaskStore.reopen`。
- 真实 Feishu 写入入口在建立 transport 前执行生产预检；凭据、签名、app-scoped
  `open_id` 或精确项目群路由任一缺失时 fail-closed。Mock 验证不代表真实上线验收。
- Feishu `/issue-merge` 投影跟随 Portable Issue Session：`self-issue-new` 创建
  `self-issue-capture` 会话，卡片读取会话状态并只通过受控 Session action 继续调查、
  确认报告和生成发布草稿；旧 Intake 命令继续作为兼容入口。
  Feishu 支持 `/issue 描述`（可在前面 @机器人），统一归一化为 `self-issue-new`；
  不支持 `/issue+描述`。描述保留换行、引号，当前消息附件及引用消息 ID 原样传递，
  身份、精确群路由和去重检查不变。Webhook、bridge/watch、consume 与 Kanban Agent
  入站均使用同一命令解析器。
  会话从配置的 `state_dir/self-issues/portable-sessions` 读取，新建消息附件先落入
  与 Web 相同的受校验私有 sidecar；下载失败会阻止新建，不静默丢弃附件。
  飞书独立文件消息可先上传，再引用该消息发送 `self-issue-new` 或签名的
  `self-issue-session-answer`。读取引用文件时校验同一群、同一 open_id 用户，
  下载使用原文件消息 ID；批量消息也保留附件原消息和发送者，不依赖最后一条消息 ID。
  Session 补充回答再次验证 sidecar，合计最多 5 个文件、单文件 20 MB；回答与附件
  在 Session transaction 内通过 Store 一次保存，再恢复调查。只有附件时保存文件名说明，
  不生成用户未陈述的诊断结论。附件仍为私有证据，不在接收时发布到远端 Issue。
  创建后独立发送的图片/文件不会自动归入最近会话；等待补充时，上传后引用文件消息
  发送卡片中的签名回答命令，明确绑定该 Session。新建时也可先上传，再引用发送
  `/issue 基础 wheel 未声明 CLI 导入所需的 httpx 运行依赖`。
- 等待回答时，卡片提供 `/zf self-issue-session-answer <session_id> --token <token> "回答"`。
  token 绑定动作、会话、群、过期时间和一次性 nonce；身份和精确群路由检查仍生效。
  空回答不消耗 nonce。卡片语义摘要排除 token，使正常轮询保持幂等、到期后重新签发。
- Session 的“分析过程”只展示动作（含工具名）、命令与已记录状态
  `running` / `failed` / `completed`，不展示结果正文或结果摘要，不生成分析过程续页。
  项目群内的动作和命令保留用户本地路径，不替换为 `[REDACTED_PERSONAL_PATH]`；
  凭据过滤仍生效，对外发布报告的脱敏规则不变。
  命令保留换行，正常长度下不再限制最近 8 条或裁剪到 220 字；序列化元数据超出
  卡片预算时保留最近的完整条目并明确提示省略，单条超长也显式标记。
  Working Set 中可见动作/命令/状态变化触发同一张折叠卡片原位更新，不依赖
  Session revision；仅工具输出变化不更新卡片。完整工具结果仍由 Web 受控读取入口提供。
- 群内 Agent 回复默认折叠正文：普通回复使用“回复详情”，流式回复将文字、
  思考和工具过程放进同一个外层折叠面板；当前状态和失败/中断提示保留在外部。
  `/issue` 的分析过程、报告正文（含续页）与发布预览各自可展开/收起，问题提示和
  受控操作按钮保持可见。使用飞书原生 `collapsible_panel`（客户端 V7.9+），
  展开/收起不发送业务回调、不修改 Session/Event 状态、不截断原有正文。
- 报告使用 Web 同源的 `stage_report_contract` 合并 `report/fields/gaps`，显示问题、
  实际/预期行为、复现步骤、分析、证据和调查缺口。输出前脱敏，长报告按 3500 字符
  分段为同群续页卡片，正文不截断；续页以 Session ID、报告内容摘要和段号幂等投影，
  主卡标明当前报告版本，旧轮次续页保留为历史。
- 报告确认同时提供签名文本命令 `/zf self-issue-session-confirm <session_id> --token <token>`，
  与按钮共用 nonce；继续执行 approver、精确群路由、action/target/chat/expiry 校验。
  确认报告不等于发布。按钮回调仅返回短 toast，禁止把完整报告 JSON 放入回调提示；
  判断“回调服务未在线”时先检查事件和动作结果，避免已成功确认后误判离线或重复执行。
- 发布预览投影直接读取 `PublicationIntent.payload` 的目标仓库、标题和正文，长正文分段
  显示并绑定发布批次。发布动作的脱敏结果通过带源事件 ID 的不可变 sidecar 保存，
  飞书按群/Draft 读取结果，明确区分尚未发布、授权等待、失败和已发布。
- GitHub 设备授权复用受控 `self-issue-github-device-start/poll` 动作，两者均要求
  approver、精确群路由和签名。授权绑定从可信飞书用户、群及 Draft 派生；输入不能
  覆盖 credential subject、workspace 或 session binding。卡片只展示授权 URL、
  用户设备码和有效期，绝不展示 device_code/access_token。用户完成授权后主动检查，
  Kernel 按原确认批次继续发布。确认已过期则重新预览/确认，不能绕过时效校验。
- 已过期的 confirmed intent 不参与新预览复用。签名按钮的幂等键纳入 token 摘要，
  同一回调仍去重，而同卡片重新签发后的授权检查和失败重试可正常执行。
  确认报告只冻结报告，生成草稿后仍需独立的发布预览、确认和发布动作。
- `needs_info` 卡片只提供远端 GitHub Issue 链接和问题摘要，报告者在远端回复；
  `triage_queued` 与 `fix_queued` 分别使用对应的取消动作，并绑定当前队列的 run 和 attempt。
  没有本地可取消队列的远端标签不生成取消按钮；Fix 审批仍使用现有真实 proposal 卡片。
  回归入口：`tests/test_feishu_issue_adapter.py`、`tests/test_feishu_issue_sessions.py`。

### Feishu bridge 重启与遗留 Self-Issue Session

- 每个 Portable Session 的后台调查由 `state_dir` 与 `session_id` 组成的跨进程执行锁保护；
  同一 Session 在不同项目 state 目录中仍可并行，旧版 Headless provider thread lock
  也会被探测，避免升级期间重复接管。
- bridge 关闭先停止新调度，再排空消息 dispatch；仍在调查的 Feishu Session 通过
  `ControlledActionService` 的 `self-issue-session-pause` 保存 Working Set 和证据检查点，
  设置 `interruption_source=system`、`resume_after_restart=true`，随后取消 provider run，
  最后执行有界 join。晚到的 Agent 结果由 Session revision/暂停门槛丢弃。
- bridge 启动扫描配置解析出的精确项目 `state_dir`，只经受控
  `self-issue-session-recover` 恢复 `execution_owner=feishu` 且状态为
  `created`/`investigating`，或带 `resume_after_restart=true` 的 `paused` Session。
  用户暂停、失败、取消、完成、等待补充和待确认会话不自动恢复；执行锁或旧 provider
  锁仍被占用时保持原状态，待下一次启动重新检查。恢复继续使用 Session 保存的
  `source_binding.repository_root`，不会跟随当前项目设置漂移。
- 旧版本没有 `execution_owner` 的遗留 Session 不作来源猜测；需要人工通过 Web/受控
  恢复动作明确接管，避免把 Web 会话误认成 Feishu 调查。
