# 208：按需 Critic 的输入范围与返修收敛

> 状态：实施与验证完成。基于 dev `bbefdff0`；真实 Channel → Plan Preview 首次发现完整度标签问题，修复后同版浏览器复验通过。降本效果按实测报告，尚未证明总 token/耗时下降。

造父已经支持显式 `none / targeted / full`，但首次定向评审仍完整读取来源，报告与独立工件重叠，返修又携带历史大正文。本次让按需策略同时约束工作范围与输入路径，保留完整、可读的 PRD / Plan、来源证据和人工审批。

## 需求与事实

- R1：修复 `none` 的角色选择冲突和提案 STOP 被显示为运行中的问题；已有 Task、需求和拒绝原因必须保留，并支持受控重试。
- R2：减少交接报告与独立工件的重复输入。只做可证明的精确拆分和复用，不用摘要替换未知语义；原始不可变来源仍可展开。
- R3：首次 `targeted` 按批准的问题审查，不要求存在历史全文通过记录；历史增量复审继续要求版本、范围及阅读证据。
- R4：Channel 关注需求，Plan 关注新增设计、任务与验收责任，返修关注原 finding、变化及影响；同版 PRD 评审不能直接充当 Plan 通过。
- R5：修正场景抽取和片内/最终验收责任的方法。上轮 SCENARIO-01 膨胀来自 Planner 自写脚本，不向 Kernel 添加按业务语义判断的解析门禁。
- R6：用冻结样本核对质量、分页、输入/缓存/输出及耗时，再完成真实 Channel → PRD → Workflow → Plan Preview；终点为待 Owner 审批，产品实现和测试保持 NOT_RUN。

依据：上一轮真实报告 `reports/critic-quality-20261117-071631/continuations/20261117-0910/evaluation.md`（dev checkout 本地忽略证据）；[207](207-review-cost-and-incremental-evidence-optimization.md) 与现有局部评审实现。

## 方案及权责

### 评审策略

保留 `plan_review` 的 `mode/reason/focus` 合同和兼容默认 full。由 Workflow Synthesis 根据 Owner 已授权范围提出策略，经既有配置提案生效；Planner 不能自批免审。none 的 requested_roles 不应包含被移除的 Plan Critic，冲突结果必须拒绝，不能静默删角色。无 Critic 聚合仍须保留当前 Planner 明确声明的 `plan_content_profile`；不能从上游声明或正文长度推断完整度。

提案构建返回 STOP 是已完成但不允许启动的结果。consumer 必须发布同 operation/request 绑定的失败和完整 blockers，避免重复执行同一失败提案；重试需明确区分可复用 synthesis 的外部前置失败与必须重新综合的选择冲突。

### 首次定向与历史增量

首次 targeted 保留原始需求、批准配置、合成合同、完整 Plan、Task Map、来源索引及执行事实作为核心输入；可独立导航的补充矩阵按需读取。以绑定的产物身份识别材料，不按文件名或关键词猜测业务关联。未知来源、显式必读和不能证明身份的内容保持必读。

Critic 根据 focus 选择相关补充原文，并检查共享约束、依赖和反证。所有来源仍在同一 manifest 中，通过现有授权读取；记录实际覆盖与未覆盖范围，不宣称全文已审。首次定向不复用历史 approval，也不把机械检查当成语义充分性证明。

历史增量沿用 scoped_plan_review、历史包和独立阅读证据；原始需求、共享规则或任务图变化导致原基线不适用时扩大审查。显式 full 始终完整审查语义内容，但允许无损减少包装重复。

### 无损拆分

仅对明确的机器 JSON 包装沿字段路径分区：所有字段都保留，内嵌完整正文和独立来源在精确一致时复用现有选读去重。独有字段、冲突正文、额外 finding 都必须交付；读取权限和每个 occurrence 的回执保持独立。未知结构、摘要不符或非法 selector 不进入优化路径。不能通过固定截断字数、删除原文或缓存命中宣称已读。

返修复用相同机制，避免将嵌套历史报告再次整包输入；finding 的关闭、影响范围和扩读由 Agent 判断。沿用现有回合预算，不新增决策 Agent；未完成阅读或预算耗尽保持失败/未审，不能自动批准。

### 真实验证发现的合同反馈

同一真实 Channel 综合连续两次将 `requirement_coverage.evidence_refs` 写成 ref/digest 对象数组；旧诊断只称缺少引用，未指出类型。补充 Prompt/Skill 的 `list[str]` 约束及实例，并让机械错误直接说明期望类型；保持原有拒绝行为，不将对象静默转成批准证据。通过实际提交 → repair event → 受控修复消息测试，防止仅修改未被调用的说明。

### 方法与呈现

在既有 Skill 中明确：场景提取应以同级或更高层级标题为边界，独立核对首/中/末场景及附录边界，保留稳定来源 ID；不要把 PRD 后缀塞进一个场景。来源矩阵保存导航，不复制正文。

Plan 每项 mandatory AC 的完整语句必须能由对应 owner 在该阶段验证；未来双入口、最终 README、集成测试归实际后续生产者。Critic 不得靠解释缩窄已有 AC 来批准矛盾合同。按需角色仍须遵守本次真实阅读策略，不能被通用 Skill 重新扩大成所有全文必读。

人读 PRD/Plan、下载内容和版本绑定保持完整。展示区分策略、实际输入优化、未覆盖项与执行失败，不用定向通过替代全文通过。

真实验证还发现 Critic 将定向覆盖误写成普通 `derived_plan`，覆盖了 Planner 的完整文档声明。文档完整度与评审范围分开：当前候选合同绑定的作者声明可恢复，必须同时校验合同、Run、Plan 正文 digest 和唯一作者；缺失、篡改或歧义不提升。显式 `summary_only` 不被覆盖。未来聚合保留该声明；既有预览可只读恢复同版声明，不回写历史事件、审批包或正文。

## 验收

| ID | step → verify |
| --- | --- |
| AC1 | 构造 none + requested Critic 冲突 → verify: 明确失败，含原 STOP、绑定 request/operation；刷新/重启不显示无限运行，修正后可重试 |
| AC2 | 对相同正文的包装与独立来源读取 → verify: 精确选读正文去重，每个来源有回执；冲突、权限、摘要、未知字段和补读不被绕过 |
| AC3 | 派发首次 targeted → verify: 无历史基线也可降低补充全文必读；完整核心输入及全部可展开来源仍在，显式/full/未知来源保护有反例 |
| AC4 | 派发历史增量与返修 → verify: 保留既有基线身份和回退测试，新的读取诊断不把首次 targeted 误标为历史已审 |
| AC5 | 更新角色方法 → verify: 三份技能分发一致，真实场景及验收责任反例能定位；完整 Plan 不因输入优化被缩短 |
| AC6 | 冻结相同产物比较 → verify: 分列字节/分页/token/缓存/耗时与语义发现，包含有缺陷和干净样本；不以 full 输出当自动真值 |
| AC7 | 真实 Channel → Plan Preview → verify: 当前候选代码、真实 Provider、同版 PRD 确认、v5 策略、实际 Critic 派发和待审批 Plan，Docker 浏览器渲染/下载及来源核对；不批准实施 |

测试层：影响闭包 pytest、dev-verify、sentinel、mock PRD UI、真实 Provider 与 Docker Playwright。失败分为产品、测试脚本、Provider/环境与未审；保留首失败和修复后复验。降本比例是实验结果，不预设已实现的 50% 或要求 Critic 一定低于 Planner。

## 实施顺序

1. S1：提案 STOP 与重试、none 角色方法。
2. S2：无损来源拆分与读取回执；补充首次 targeted 的输入策略及诊断。
3. S3：Channel/Plan/返修职责、场景边界与片内验收反例。
4. S4：固定输入对照、真实 Channel 与 Web 证据、提交及任务归档。

## 本轮验收结果

本地证据入口为 `reports/critic-scope-20261117/evaluation.html`（gitignored）。初始必读字节减少 46.4%，但真实 Critic 完整会话输入仍约 475 万 token（含缓存），为 Planner 的 2.30 倍，operation 耗时 18.12 分钟；不能据此宣称实际降本。固定输入字节对照与真实模型教学样本分别报告，不混作因果 A/B。

真实 PRD、Plan 和 4 个 Task Spec 已独立阅读，定向 Critic 一次批准，无 Plan 语义返修；完整度修复由同版真实 UI 复验、新运行 mock 和调用者回归证明。原始失败保留，产品实现未启动。后续重点是减少读取往返、Channel 大文档重做、追溯重复及 Planner 路径文本误判，不以删除阅读证据换取通过。

## 公开依据

- [Anthropic evaluator–optimizer](https://www.anthropic.com/research/building-effective-agents)：评价标准与可测收益。
- [Spec Kit analyze](https://github.com/github/spec-kit/blob/main/templates/commands/analyze.md)：渐进阅读与跨工件一致性。
- [上下文工程](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)：引用、按需获取与必要背景。
- [Claude Code 评审](https://claude.dev/blog/solving-code-review-at-anthropic/)：有界返修、差量复审和事故评测；该文仍每 PR 做 AI review，不作为全部免审依据。
- [OpenSpec verify](https://github.com/Fission-AI/OpenSpec/blob/main/skills/openspec-verify-change/SKILL.md)：明确未覆盖、不补造不适用材料。
