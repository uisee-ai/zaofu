# 208：按需 Critic 的输入范围与返修收敛

> 状态：两批实施与验证完成。基于 dev `bbefdff0`；第一批完整度问题已修复，第二批真实 Channel → Plan Preview 从头通过。各批降本效果分别按实测报告，不混合作因果比较。

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

首次 targeted 保留原始需求、批准配置、合成合同、完整 Plan、Task Map 及执行事实作为核心输入；可独立导航的补充矩阵按需读取。第一批来源索引也属于核心；第二批将已绑定索引转为按需导航，条件详见下文。以绑定的产物身份识别材料，不按文件名或关键词猜测业务关联。未知来源、显式必读和不能证明身份的内容保持必读。

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

## 第二批：实际阅读成本与生成路径收敛

本批依据第一次真实测量继续实施，原结论保留。验收以同一冻结候选的真实用量和缺陷识别为准。

- 首次 targeted 保留完整原始需求、Plan、Task Map、共享约束及执行事实。候选作者明确引用、唯一 digest 绑定且 schema 为 `source-index.v1` 的索引转为导航材料，按 focus 展开映射；未知/冲突/显式必读/full/历史返修路径不降级。索引原文和全部访问权限保持可用，未读取部分不冒充全文已审。
- 必读交付继续逐来源授权、逐 occurrence 记账，JSON 默认兼容；提供最高 32 KiB 的显式文本页，正文免外层 JSON 转义。保留游标/版本、精确输出观察与断点恢复；不以 stdout 成功代替 Provider 完整收到，不接受截断输出作为续读依据。
- Skill 约束来源索引只承载稳定 ID、定位、必要摘录和映射，避免重复整篇场景；完整产品正文、定性约束和可执行 Task 合同不缩短。按当前上下文读取技能，恢复后有缺口才补读，不用历史交付假装仍记得。
- Channel 只在结构化 `prd_markdown`（或现有文件 ref）输出完整正文，避免 JSON 外再次整篇输出；机械类型错误优先修复结构。实验显式固定模型/推理，通过现有项目配置传递，不修改本机全局默认。
- 目录保护只识别受限的 `cat > path <<'DELIM'` 字面数据写法；真实目标仍校验 assigned workdir、符号链接和运行时禁写路径。执行型 heredoc、未引号 delimiter、替换、管道与复合命令不进入豁免。
- 测试包含固定候选配对、不同产品正/负例、真实 Channel → Plan Preview；保留一次通过、重试与失败数据，产品实施仍待 Owner 批准。

项目可在 `zf.yaml` 的现有 `channel` 配置内显式设置 `codex_model: gpt-6.1-sol` 与
`codex_reasoning_effort: medium`。空值保持现有默认，成员已配置的 model 优先于项目 model；
推理强度由项目配置指定。适配器启动与每次 turn（含续会话）传递当前值，started 事件记录
选用配置；实际模型仍以 Provider native turn context 核验。其他项目及本机全局配置不变。

读取示例：`zf artifact read --required --format text --page-bytes 32768`，继续使用当前
briefing 要求的身份和 attempt 参数。续页保持格式与预算并带返回的 cursor；若 Provider
截断，原 cursor 降低预算重读，不能把截断计为完整交付。默认 JSON 页仍为 8192 字节；
单来源 `--page-size` 仍最多 8192 字符，不能混用这两个单位与上限。

### 第二批验收结果

证据入口：`reports/critic-cost-next-20261117/evaluation.html`（本地忽略报告）。同一冻结
候选按 ABBA 各重复两次，必读页 51 → 8，四次评审均通过且完整页可在 native 输出中
核验；输入合计均值下降 79.5%，新输入下降 41.4%，缓存输入下降 80.9%。这是实际
Provider 读取实验，不是费用账单或普遍性能保证。两种新产品的正反样本均完成校准；
初始退款正例的遗漏被两组指出，修订样本版本并保留原失败，未据此调低评审标准。

真实旅程约 41.9 分钟，从头通过 Channel、v5、一次 targeted Critic 和待审批页面。
Channel 综合一次准入、无结构修复，native 模型固定为 gpt-6.1-sol / medium；仅一个
完整 PRD 正文载体。Planner 输入合计 1,163,317、Critic 1,089,413，比例 0.94；对应
operation 耗时 10.16 / 6.85 分钟。两轮生成稿不同，不把与上轮 2.30 倍之差当冻结 A/B。
实际 8 个文本页完整交付全部 59 项必读要求；来源索引仍可展开。3 个 Task、17 条
任务 AC 与 21 条来源 AC 已对账，最终原文、下载、刷新和三个模块规格同版。产品未实施。

读取/续读、调用者、索引保护、写入边界及技能分发均有回归；Web、sentinel、mock flow
通过。影响闭包另有两个在修改前基线同样失败的既有用例，报告保留复现，未宣称全库
全绿。真实 Critic 仍发生两次上下文压缩及部分方法重读，矩阵约 100 KB；角色上下文
预算与矩阵唯一正文是后续可评测方向，本批没有以删原文或取消 Critic 掩盖它们。

## 公开依据

- [Anthropic evaluator–optimizer](https://www.anthropic.com/research/building-effective-agents)：评价标准与可测收益。
- [Spec Kit analyze](https://github.com/github/spec-kit/blob/main/templates/commands/analyze.md)：渐进阅读与跨工件一致性。
- [上下文工程](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)：引用、按需获取与必要背景。
- [Claude Code 评审](https://claude.dev/blog/solving-code-review-at-anthropic/)：有界返修、差量复审和事故评测；该文仍每 PR 做 AI review，不作为全部免审依据。
- [OpenSpec verify](https://github.com/Fission-AI/OpenSpec/blob/main/skills/openspec-verify-change/SKILL.md)：明确未覆盖、不补造不适用材料。
