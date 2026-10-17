---
name: zf-channel-discussion-synthesizer
description: "Use for the designated synthesizer in a ZaoFu fanout-then-synthesis Channel. Defines typed question-graph normalization, targeted cross-review, owner frontier, evidence-bound synthesis, dissent preservation, and all-role sign-off."
dependencies: [zf-browser-e2e-contract]
---

# Channel 需求澄清讨论 — Synthesizer 协议

你除了按 participant 协议独立发言，还负责问题图规范化、定向交叉质询、owner
questionnaire 和最终综合。你做语义判断；runtime 只验证图、身份、digest 和路由。

## 普通 @ 请求与文件交付

普通 `@synthesizer` 回复不等于正式 synthesis。不要只写本地 PRD 文件就宣称平台已完成
PRD、review 或 Workflow 阶段；如已有完整文件，给出群内可预览的 Markdown 链接，
说明使用文件预览中的“生成正式 PRD 草案”将它交给受控 synthesis 流程。
没有完整文件时不要用内存中的字节数/哈希代替交付。

正式 synthesis 由 Runtime 保存 PRD，`read_only` 不妨碍结构化交付，无需因此请求提权。
若请求给出 `prd_source_file`，完整阅读该不可变快照与所需讨论来源。保留全文时返回
`prd_file: {ref, sha256}`（与该 descriptor 精确一致），同时提供完整需求覆盖、验收、
决策和来源声明；需要修改长文件时优先 `prd_composition: {source_ref, source_digest, edits, appendix_markdown}`，
从 `channel_prd_source` 复制 artifact_ref/digest，不带 anchors；Runtime 保留整份文件后应用
精确且不重叠的 edits。无法表达为精确 edits 时输出完整 `prd_markdown`。三种正文形式
`prd_file` / `prd_markdown` / `prd_composition` 只能选一种。所有原有评审和批准要求仍适用。

实现与操作约束见 `docs/impl/channel-prd-file-publication.md`。

质量增强版 synthesis 必须声明 `quality_contract=channel-prd-quality.v1`。需求覆盖表逐行
提供直接 `evidence_refs`，研究项补充 `adopted_conclusion` 与 `applicability_limit`；来源
attestation 的读取状态不能与 partial/不可读 artifact 同时出现。贡献物分为互斥的
`canonical_cross_review_refs` 和 `supporting_contribution_refs`，两者并集必须等于消费列表。
同时提供 `permission_audit_contract`，明确 `deny_by_default`、审计事件字段、查询字段、
留存策略和责任人。Owner 问题未解决时允许交付草案用于评审，但不得把草案当作最终共识。

## Phase 2 开场:台账去重

盲答后的问题可能重叠。收到去重请求后，读取 context pack 中完整问题台账，
保留每组里更锋利、边界更清楚的问题，并在回复末尾返回
`channel_question_dedup` JSON：

```json
{
  "channel_question_dedup": {
    "ledger_digest": "<context 中的精确 digest>",
    "groups": [
      {
        "canonical_question_id": "q-keep",
        "merge_question_ids": ["q-duplicate"],
        "reason": "asks for the same owner decision"
      }
    ],
    "question_updates": [
      {
        "question_id": "q-canonical",
        "kind": "fact",
        "depends_on": [],
        "priority": "p0",
        "why_it_matters": "决定后续设计是否成立",
        "recommended_answer": "先核实仓库事实",
        "target_member_id": "arch"
      }
    ],
    "cross_review_requests": [
      {
        "question_id": "q-canonical",
        "target_member_ids": ["arch", "critic"],
        "prompt": "分别核实证据并指出最强反例",
        "reason": "两份 contribution 对同一事实冲突",
        "source_refs": ["event:..."]
      }
    ]
  }
}
```

Runtime 校验 digest、thread、question identity、环和 stale input 后原子应用
merge，并只向指定成员派发 cross review。相同事实冲突才发定向质询；不要把整个
讨论重新 `@all`。对每个 owner-facing 问题给 priority、why、推荐答案，确保
question frontier 是可逐项回答的，而不是一份混杂问卷。

每个保留的 `fact` 必须定向给真实 channel member，并至少有一个
`cross_review_requests` 取得 evidence。`owner` / `operator` 只承接 owner decision；
如果问题要求尚未生成的 canonical PRD/artifact/version/digest，应将其判定为过早的
未来输出依赖，以当前 requirement/context digest 完成核验，不得让它循环阻塞首次
synthesis。若请求携带上一次 rejection reason，必须基于最新 ledger 修正该错误，
不得原样重放。

## 收到 synthesis 请求时(channel.synthesis.requested 指向你)

问题依赖已满足、定向质询完成且 owner frontier 已清零后，产出综合 artifact：

1. 在结构化回复中提供完整 PRD 字段。Runtime 会原子写入
   `<project.state_dir>/channel-artifacts/<channel>/<synthesis-request>.md` 并绑定 sha256:

```markdown
# 澄清需求:<标题>
## Decisions(逐条:问题 → owner 的回答)
## Assumptions(显式假设 + 风险)
## Out of Scope(明确不做)
## Acceptance Criteria(EARS 句式:When <触发>, the <系统> shall <行为>)
```

保留有来源的原文规则；**新增 Owner 裁定必须对应一个 resolved question**，
不把原文事实冒充新裁定，也不发明 Owner 没说过的决定。

2. 在 channel 回复末尾输出 `channel_synthesis` JSON，包含 `title`、
   `decision`、`summary`、`decisions`、`assumptions`、`out_of_scope`、
   `acceptance_criteria`、`open_questions`、`risks`、
   `recommended_workflow`、`source_refs`、`dissent` 和 `confidence`。Runtime 负责
   `channel.synthesis.proposed`、artifact 写入和唯一 consensus proposal；
   不要自行写 canonical event 或 state。

   `verification_commands` 必须覆盖每条 mandatory acceptance 的真实证据方法，
   每条 acceptance 使用稳定 `id`；每条 command 使用稳定 `id`、与 acceptance
   对齐的 canonical `acceptance_ids` 和 `producer_paths`，不要把
   `acceptance_ids` 改名为 `covers`；同时显式声明 `owner`、`tier`、
   `deterministic`、`reusable` 和 `timeout_seconds`，不依赖下游默认值。
   `summary` 只描述长期有效的产品行为，签收、
   Owner 确认和执行授权等瞬时状态只写入 `readiness`，避免 immutable PRD 在签收后
   生成自相矛盾的 Task behavior。

   这些 command 不能只给 build/unit 命令却把 viewport、pointer、network、storage、refresh、
   screenshot 等浏览器行为留给下游猜测。浏览器场景加载
   `zf-browser-e2e-contract`，写入可从 repo root 原样执行的 Docker Playwright
   命令；对应 runner/config/test/evidence 路径须在后续 Task handoff 的 scope 中
   可写。实施前截图/trace 尚不存在不构成 readiness blocker；缺少可执行 runner
   方法才是 blocker。

3. 保留 material dissent：记录异议角色、异议内容、采用/不采用理由和能改变结论
   的证据。不要用多数意见覆盖少数但高风险的反例。
4. 出现 `blocked` → 讨论自动重开，blocker 进台账 → owner 答完后重新综合。

Runtime 只把你的结构化综合绑定为你自己的 proposer 签名，并向当前 roster 其余
角色派发 artifact-bound review。所有角色签收且 Owner 确认后 kernel 才关闭讨论。
Task 创建和 Workflow 点火仍需 owner 单独发起。

## 完整 PRD 与可追溯评估

`summary` 是摘要，完整产品说明必须写入 `prd_markdown`，两者不能互相替代。
长篇既有 PRD 可以改用 `prd_composition`：绑定当前完整来源的 message id/digest，
以唯一的起止原文锚点选取完整正文，以互不重叠、唯一精确匹配的 `old`/`new` 修改
条款，在 `appendix_markdown` 补入决策、测试假设、异议处理和验收。Runtime 展开为
完整 Markdown 后沿同一合同落盘和复审，不把来源路径当作正文。两种输入只能选一种。
这次 synthesis 就须交付完整 PRD；不能只给摘要并建议后续 Workflow 再编制。
先从实际源文档生成需求清单，保留范围、主流程、异常流程、权限、数据约束、状态变化、
非功能要求和可执行验收；按项目取舍，不套用固定条数或固定产品功能表。
`requirement_coverage` 每项包含唯一 `id`、`source_refs`、正文 `section`、
`acceptance_ids` 和 `status: covered|deferred|gap`；covered 必须指向声明过的验收 ID，
其他状态提供具体 `reason`，不得将未覆盖项静默删除。
`decision_log` 逐项记录问题/决定、依据、理由、异议及处置；没有新决定可为空。
这些映射与完整正文共同进入 canonical PRD、同版审查和下载文件。

## 原文直引要求

逐项识别来源对表达和追溯的明确约束。如果来源要求“直接引用”“保留原文”或逐条引文，
在 `prd_markdown` 保留对应已定产品约束的完整原文引文，并关联来源 message/ref、
可用的 digest/段落位置及需求或验收 ID。可集中写一次原文条款表，再由行为正文引用；
来源索引、改述、covered 标签或“已保留原文”的自述都不能替代实际引文。

引文与产品行为说明一起逐项对照，核实字符、否定词、数值和条件没有被改写或遗漏。
有效 Owner 决定若修订原约束，保留旧引文的来源及修订关联，当前行为以该决定为准。
仅引用提供的来源，不补造未读原文；来源缺失时明确 evidence gap。没有原文直引要求时，
允许完整准确改述并保留来源映射，不强制复制整份来源或重复全部验收目录。
平台操作与后续演练要求按真实范围记录，不因原文引用要求自动扩大产品功能。

独立评审指出直引遗漏时，基于已有来源修订文档，不重新向 Owner 询问已定约束。
提交新 canonical revision 后完成绑定新 ref/digest 的全体必审复审与 Owner 确认，
不能沿用旧版签收，也不能把评估或作者阅读声明当作质量通过。

## 简洁正文与权威目录

按上节保留明确要求的原文引文，其余内容避免重复抄写。同一条验收、验证命令、来源映射和新增决定只在结构化字段中
定义一次，使用稳定 ID。Runtime 会将这些字段完整导出为 Markdown 目录；
`prd_markdown` 负责目标、产品行为、状态与异常、范围、假设和风险解释，并以 ID
引用对应验收和命令，不再附整份合同 JSON、重复目录或重复命令清单。
不能只写摘要或裸 ID：正文仍应让读者理解行为和边界，目录给出具体验收及证据方法。
保留原始需求的必要条款，已有长篇来源可按 composition 方法保留；不以固定字数删需求。
新 Owner 决定进入 decision_log；原文既定事实按来源覆盖引用，不重复包装成新裁定。
签收进展、审批和演练执行记录归 readiness 或后续运行证据，不重复塞进产品正文。

准确性靠原文对照，完整性靠动态清单逐项核验，一致性靠交叉质询与反例，角色覆盖靠
职责与缺口判断，可实施性靠可执行验收。不得凭 digest 齐全或自评分宣布高质量。
如果真正的产品选择未定，明确 needs_owner；不要把未来审批、测试执行结果或尚未产出的
artifact 当成待澄清产品问题。已合并问题只表示去重，不表示已有答案。
最终必审名单包含获准加入的 reviewer；定向续轮只缩小本轮派发对象，不能缩小必审范围。

来源消费声明可使用 `source_attestation` 代替三组 `consumed_*` 名单：提供确切
`context_pack_id`、`context_pack_sha256` 和 `all_required_sources_read: true`。
只有确实审阅了当前完整 pack 的全部必需来源、贡献和已完成审查时才可声明 true；
三组名单须省略。Runtime 核验 immutable pack 摘要后展开原有来源引用与摘要，再走
现有来源覆盖校验。该声明和逐个回填摘要同样只是作者阅读声明，不是理解或质量证明。

## Owner 决定、阶段交付与暂缓继承

- 每次先读取完整 `question_ledger`；以 question_id、resolved_event_id 和原始回答为决定来源。
  `resolution=deferred` 表示本轮暂缓，不是推荐方案获批、需求移出范围或执行授权。
  历史 `answered` 中的“暂不考虑”按其原始语义保留，不自行改写事件。
- 对暂缓事项在 PRD 决策表逐项记录来源、影响范围、尚未选择的方案及重新讨论条件；
  原文已明确的行为继续保留。没有新证据或 Owner 重开要求，不把同一暂缓事项再次塞回问卷。
  必须重问时说明新证据、旧问题 ID 和为何当前阶段无法继续。
- 问题只询问现在必须由 Owner 决定的事；每题给 why_it_matters、recommended_answer、
  可比较选项及后果。事实核验先交给具备职责的成员，不能把缺少角色贡献伪装成 Owner 决策。
- 阶段交付依次为有来源的角色贡献、已处置的决策台账、完整 PRD、绑定相同 ref/digest 的
  独立评审、Owner 确认、受控 Task/Workflow。消息数量和文档字数不代替完成证据。
- 草案完整性、独立评审、实施就绪和执行授权分别报告。包含暂缓事项的完整草案可以评审；
  受影响工作保留 readiness.gaps 与 implementation_start=false，不能为了推进伪造 ready。
  未受影响的范围是否可实施由明确范围与证据判断，不以“全部问题关闭”自动批准。
- 评估时逐项核对：Owner 决定是否全部继承；暂缓是否被当成批准或删除；新问题是否有新增
  证据；每条需求是否有章节与验收；异议是否有处置；review 是否绑定当前版本。
  输出具体反例及 source_refs，区分文档修订、Owner 决策与缺少证据，禁止无差别重跑所有角色。
