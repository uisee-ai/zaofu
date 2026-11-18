# 208：按需 Critic 的输入范围与返修收敛

> 状态：前两批实施与验证完成；第三批经用户授权实施中。基于 dev `bbefdff0`；第一批完整度问题已修复，第二批真实 Channel → Plan Preview 从头通过。各批降本效果分别按实测报告，不混合作因果比较。

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

## 第三批：上下文预算、唯一矩阵正文与 replan

### 问题与调研结论

用户希望首次 Plan 和 replan 都减少无效评审成本，同时保持缺陷识别、可读正文、需求
来源和人工审批。第二批原生记录显示 Critic 模型窗口为 475000，v5 的 roleDefaults
却为 120000，适配器按 0.7 得到 84000 的 auto-compaction 上限；两次压缩均发生于
约 8 万上下文，之后又重读材料。该值不是模型窗口不足的证据。

测试矩阵有 114 个 tests 行，许多行重复同一 AC 全句及通用 Given；已有 consumer
需要稳定 test/source/acceptance/command 标识与 Given/When/Then，并不要求把参数
笛卡尔积展开成重复正文。所有不同失败结果、验证阶段和责任必须仍有明确覆盖。

公开依据（2026-11-17 核验，方法借鉴，不直接照搬平台实现）：

| 资料 | 采用 | 不采用 |
| --- | --- | --- |
| [Anthropic context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents) | 小而有效的上下文、按需取原文、压缩后保留决策与未决事项 | 仅扩大窗口、不测质量；摘要代替读取证明 |
| [Claude Code review](https://claude.dev/blog/solving-code-review-at-anthropic/) | 局部返修、差量复审、用缺陷回归约束降本 | 用代码评审策略自动免除产品 Plan 审查 |
| [Spec Kit analyze](https://github.com/github/spec-kit/blob/main/templates/commands/analyze.md) | 按需求/任务/覆盖关系审一致性，优先确定缺陷，避免重复正文 | 为展示再造第二份合同 |
| [OpenSpec verify](https://github.com/Fission-AI/OpenSpec/blob/main/src/core/templates/workflows/verify-change.ts) | 分开完整性、正确性、一致性；明确未验与不适用 | 将执行验收方法生搬到尚未实施的 Plan |
| [Codex config reference](https://developers.openai.com/codex/config-reference) + 本地 CodexAdapter | 区分模型窗口和触发压缩的 token 限制；本地参数与 native 取证 | 声称增大模型真实容量或修改宿主全局配置 |

### 设计决定

1. v5 保留默认角色预算 120000 和既有 0.7 阈值，即原生压缩请求 84000；
   400000/280000 作为显式环境覆盖的实验配置，不改变 Codex 自报窗口或 Kernel hard-cap。
   固定输入六次实验未证明扩大阈值能降低累计输入，故不推广为全部角色的默认值。
   首次、返修及 replan 同一角色启动路径共享该配置。预算修订不授权增加并发或跳过审查。
2. 在既有 Planner 方法中要求矩阵 tests 使用最小完备场景集：同一 owner/tier/command、
   相同前态与失败不变项的参数可在同一 When/Then 中显式列举；不同结果/责任/阶段不得
   合并。使用原有字段，来源以稳定 ID 定位，保留每项独有约束；人读 Plan 仍完整。
   方法阅读按当前 scope 选择，先完成必要原文读取再一次形成 findings，不反复启动全审。
3. replan 分两条明确路径：有完整历史证据的局部继承继续使用既有 scoped_plan_review
   校验；没有继承资格的当前 targeted 重新审查，不冒充旧 approval 可复用。后者只在
   绑定且完整的 plan-rework-context、既有批准 targeted/focus、无需求修订/人工覆盖、
   无待验证增量证书时，对**当前作者**明确绑定的补充矩阵/索引使用按需导航。
   原始需求、当前完整 Plan/Task Map、旧候选、返修原因/finding、执行事实及未知输入保持
   必读，所有原文可展开。full、显式必读、失效/歧义上下文及增量证书失败都保持原保护。
   这是当前定向审查的输入路由，不是历史增量证明，也不产生自动批准。
   即使 rework 标签全部缺失，只要仍携带旧候选而没有可验证返修上下文，也不得误判
   为首次导航；该缺失变体以失败回归证明，回退保留完整必读。
   实测另发现 Planner 合法使用标准 `test_matrix_ref` 等引用字段，而非每项都写进
   `plan_ports`，首次必读仍为 296505 字节。补充兼容既有具名引用字段：仅当前单一
   作者、同一 manifest 精确 ref、已验证摘要及已知 v1 schema 的矩阵/目录可导航；
   保留原文可访问。缺失/未知 schema、多个作者、字段别名冲突、core 引用及与显式
   port 的 ref/digest 冲突保持必读，不用文件名或正文相似度猜用途。对此增加真实
   生成包的固定输入重放；已启动流程的旧选择结果保留，不改写历史读取证据。
4. 真实测试通过 Web 受控退回已生成的待审批 Plan，要求一个明确局部修正，等待真实
   Planner 和 Critic 形成新版本，再核对预览与下载。产品实施仍为 NOT_RUN。完整链路
   分阶段计量，不能把少跑阶段或不同模型的耗时当作固定输入降本。

### 验收及停止边界

- C1：编译 v5、构建首次/恢复命令 → verify: 84000 默认、显式覆盖保留、不覆盖原生窗口；
  真实记录压缩次数、最大上下文与耗时，不假设必须零压缩。
- C2：同一需求生成矩阵与 Plan → verify: 独有错误/边界/共享不变量和 owner/tier 保留，
  反例仍 reject；矩阵行数/字节只作为观察，不按固定长度裁剪。
- C3：真实调用 prepare_call_operation → verify: replan 只降低当前明确补充工件，旧事实
  仍全部交付，digest/权限/回执/重放保持；首次与 full/显式/失效证书有反例测试。
- C4：真实 Channel + v5 + Web 退回重规划 → verify: 两个版本、退回原因、两个 Critic
  dispatch/verdict、同版 preview/download，前版不冒充后版，均不启动产品实施。
- C5：固定输入/多缺陷样本与影响闭包 → verify: fresh/cache/output/耗时/压缩分别计量，
  保留首次失败；若新范围选择漏掉缺陷或失去绑定，先收紧回退再复测。

新增语义仍属于 Agent/Skill，代码只检查来源类型、批准策略、摘要和读取绑定。后续实测
若发现更合适的有界方案，先在本节补充依据再实施；不自动增加无限优化循环或新状态机。

### 第三批实测中的方案修订

固定输入首组显示 84000 阈值有一次压缩，280000 阈值无压缩且更快，但累计输入增加。
因此“零压缩”不是成功指标：保留全部 ABBA 数据，再加入 140000 的中间阈值两次对照，
结合 fresh/cache、耗时、缺陷和真实链路选最终默认；未取得账单时不声称费用下降。
保留启动时的 400000 配置记录，不回写历史；另按实际 Workflow Apply 后的配置绑定
判定运行预算。实测 Channel 在 Web 应用 v5 模板后已改为 120000，故两条真实
Workflow 均属最终默认验证，大预算只属于固定输入实验。

六次固定输入实验（每档两次）中，280000 相对 84000 的平均累计输入增加 91.2%，
fresh 输入增加 4.3%，平均耗时降低 17.1%；140000 累计输入增加 65.6%，fresh 降低
14.2%，平均耗时降低 15.8%。样本小且宿主有并行工作，不据此声称显著提速或费用最优。
因此最终保留原默认，以减少不必要输入、等价场景去重和定向复审为主；保留显式大预算
覆盖供已测场景使用。实验运行与最终默认验证分列，不篡改运行中的配置快照。

另一个实测反例：同一 AC 在 Task Map 指定人工阅读，product_acceptance_spec 却写
E2E/CLI，六次评审只有一次指出。增加通用的跨工件验收责任对账方法及固定正反例：
沿同一 source/AC ID 核对 Task Map、命令登记、acceptance_matrix 与产品验收规格的
owner、tier、阶段和证据生产者；多种互补验证可以并存，但须区分义务及生产者。
不把端口默认标签当作实际验证能力，不为此新增语义 Runtime gate 或复制一套合同。
两条真实首稿又分别暴露了跨阶段复制：正文已局部化，但 Task Map 或测试行仍保留
后继 HTTP/最终 README 的要求。补充作者方法：先拆出当前任务实际承担的子断言，
不能靠“本任务范围内”前缀改变完整来源条款的含义；交付前读取实际序列化 AC 与
测试行，而非只解析 JSON 或查看已整理的人读正文。原完整来源义务保留在真实生产者。
保留启动和实际角色工作目录的方法摘要；新方法再对固定历史反例及实际生成包真实复审。
长期回归样本及执行边界见
[验收责任校准](../../tests/e2e/fixtures/plan-review-assignment/README.md)；最终默认另跑
真实 Kanban → Plan → Web 退回 → replan，并核对实际应用后的配置，不能仅从项目启动配置推断角色预算。

真实 Channel 首次待审批预览另发现身份冲突：作者将产品代号 `LOCAL-DRAFT` 写入
Task Map 的 `pdd_id`，而运行绑定为父任务 ID；现有 Web provenance 检查正确报错。
补充方法将 `pdd_id` / `feature_id` / `workflow_run_id` 明确为当前上下文绑定，禁止
自行生成产品代号；未提供的可选字段省略。保留 Web 身份保护，不把错误隐藏或降级。
修订需生成新候选，经正常结果提交及独立评审；不能就地修改已准入工件。本次真实
报告分别记录旅程可打开与审核包身份一致性，避免将前者冒充全部质量通过。
新增 C6 验收：来源冲突及工件不可读均由测试审计拒绝；普通 Owner 待审批不冒充
工件错误。经 Web 再退回并独立评审的新候选须消除身份错误，保持 Task/AC/命令/依赖
和既有恢复说明，下载与刷新仍与当前正文一致；原失败版本及诊断必须保留。
同次矩阵读回还发现 roles 行将合法读取和权限拒绝合并，却只写非零/403 预期。
在方法中补具体反例；同一有界修订须将两入口的成功/拒绝预期按输入分别表达，保持
原 AC 和命令责任，不借“场景精简”消除成功分支。语义仍由作者及独立 Critic 判断。

### 第三批验收结果

本地证据入口为 `reports/critic-replan-20261117/evaluation.html`。固定真实候选 ABBA
各两次，必读字节减少 38.1%，平均累计输入减少 26.9%、新输入减少 17.9%，四次均指出
真实阶段缺陷。新增身份/参数分支方法另有校准及最终真实复验，不混作同一 A/B。

真实 Kanban 旅程及 Web 退回后的新版通过。Channel 原完整审计因身份冲突失败，保留
原记录；再次 Web 退回产生第三版，独立 Critic 与浏览器核验通过，身份及分支修复，
任务合同保持，预览/下载/刷新同版。最终实际 replan 导航必读字节减少 32.1%，没有
继承旧批准。两条真实 Workflow 使用最终默认预算，示例产品没有获批实施。

本批 Runtime 影响闭包 848 passed / 1 skipped；后续预览完整性回归 56 passed，
两组有重叠。Web、serial、sentinel、mock 与 fake Docker replan 通过；未执行全库
pytest。全部必读项有同角色/operation 的原生输出证据，额外未完整观察的返回保留
说明。真实全过程 Critic/Planner 累计输入比均约 1.03，新输入比分别约 1.17 和 1.33；
不同候选之间不作因果降本比较。Replan 仍有多次压缩，Kanban 矩阵仍约 100 KB，
端到端延迟与作者首稿稳定性尚需后续观察。

## 第四批：生成经济性、任务输入与执行中 Replan

基线为 `bf6eccf6`。用户授权三批七项：信息权威及消费者核验、重复生成收敛、首稿
一致性、任务输入分配、Replan 范围及恢复、执行测试入口、真实对照评测。

### 事实与方法

第三批 Kanban 三个任务的矩阵 99942 字节、79 行，79 行 `then` 与 `expected_result`
相同，51 行 `parameter_coverage` 又等于 `then`；source_index 111411 字节，其
stage_projections 重复描述任务 AC。增加 Agent 无法消除这些序列化重复和实际依赖。
参考前述 Anthropic 上下文及分工实践和
[Agent scaling v3](https://arxiv.org/abs/2512.08296v3)：先减少重复生成、明确责任和
上下文，再在独立任务上验证并行收益。研究结论不是本产品的成本保证。

1. 先证明消费者：Task Map 保持当前任务合同，test_matrix 保持具名场景和结果，
   source_index 负责来源定位与映射。无消费者且非配置必需的重复字段由作者省略；
   独有参数、异常、共享不变量、owner/tier/时点不能省略。机械精确重复诊断只提供
   写作反馈，不新增语义准入门禁、不修改已经准入的工件。人读 Plan 仍有完整正文。
2. Planner 先确定来源义务与责任，再填入相关引用；本片 AC 不复制后继义务。沿用
   最终包读回与跨工件评审，保留身份、人工/自动责任和成功/拒绝历史反例，新增未见
   校准样本；明确已知回归不证明通用召回率。
3. 复用现有 task-plan-input-selection.v1 与 task-scoped-plan-input.v1，核验
   是否可以直接选择已有 execution_spec，避免为相同责任再造 task_execution_input。
   显式选择须与同包 Task Map 一致，完整共享条款及本任务分区可读；候选验收仍读取
   完整包，未声明选择沿用原行为。语义充分性由 Planner/Critic 审查，Kernel 只验证
   声明、身份、版本和读取证据。
4. Replan 保持原因分流：实现缺陷返回 Impl；合同/依赖错误返回 Plan；需求变化由
   Agent 重新界定影响；环境/Provider 失败恢复 operation。当前局部重审和历史批准
   继承仍分别处理。恢复笔记只记录绑定引用、原 finding、变更及未读位置，不作审批
   或完整阅读证明；未知影响和失效证据保持保护。
5. 保留 pending-preview 旅程，另扩展执行恢复入口。不得仅解除 --replan/--delivery
   互斥就声称完成执行中 Replan。三类验证为：保留 commit 的合同修订及恢复交付；
   需求/共享规则变化下的新旧合同与下游恢复；中断重启、重复事件及有界失败升级。
   通过正常受控操作、实际 dispatch/result admission 与独立候选验收取证，不直接
   修改状态或伪造 Agent verdict。真实模型、mock、OS 重启和浏览器层分别报告。

### 验收与测量

每批先定向回归，再真实验证。读取对照冻结同一候选，生成对照固定需求/模型/预算，
执行恢复计入失败和重试；Channel/Kanban 使用同需求，深度故障按代表性链路分配。
记录 unique/重复文本、实际读取、fresh/cache/output、阶段与端到端耗时、返修、
缺陷及误拒。新输入降低 20%、中位耗时降低 15% 为实验目标，不是裁剪合同的门禁；
小样本仅报告观察，不宣称账单或普遍速度保证。保留 120000 默认预算，不新增固定
Agent 编制。是否并行由已证明的独立性及收益决定。

P1–P7 的命令、版本、结果和未覆盖项记入本批任务与本地报告；完整需求、独有反例、
当前绑定和最终交付是验收依据，不能以文件变短、模型 approve 或 Preview 可打开代替。


### 第四批执行验证入口

沿用 `tests/e2e/prd_ui/run.py`，新增显式 `--execution-replan --delivery`（只允许 real）。
输入 `tests/e2e/fixtures/prd-ui/execution-protocol.md`；`${EXTERNAL_PROTOCOL}` 在隔离 root
绑定为外部只读协议。首个 Impl 产出 commit 并派发 Verify 后，测试操作员更新协议 v1→v2，
不改已批准包、任务状态或 Agent verdict。测试要求真实发现版本冲突、新 Plan 审批、
新 generation 恢复派发及唯一 goal completion，并在最终 Git 对象上执行独立黑盒 oracle。
首次和重新规划的审批分别要求实际 Plan ID/正文摘要匹配的本地审核文件；运行命令不会
自动批准任意新计划。原 `--replan` 仍只表示 pending-preview 退回，与 delivery 互斥。

执行示例（须已有真实 Provider/小产品实施授权）：

```bash
.venv/bin/python -m tests.e2e.prd_ui.run --provider real --journey kanban \
  --requirement-file tests/e2e/fixtures/prd-ui/execution-protocol.md \
  --delivery --execution-replan --timeout 5400 --turn-timeout 900
```

本用例证明外部合同版本变化下的执行恢复；不能外推为任意业务需求变更、OS 崩溃或
多次超限均已真实通过。恢复矩阵、mock/Provider/浏览器层以及失败版本分别保留。

第四批实测还发现 Plan Critic briefing 将同一必读策略展开成 65 条命令，虽已有完整
策略 sidecar 和批量读取 CLI，仍重复交付定位信息。因此对 plan-synth 采用绑定策略
定位：仅当 immutable policy 的摘要、Run、attempt 和完整 required_reads 与当前派发
一致时，正文改为策略 ref/digest、选择数量和既有批量读取/index 命令。任何不一致或
旧配置均保留原逐项命令。完整正文、JSON paths、读取回执及准入要求一项不减；这属于
机械展示优化，不是缩减 Critic 审查范围。实测冻结派发的字节变化与真实 token 分开报告。

实测补充（第四批）：Channel 的一个显式 `skill_adapter_plan` port 在 Critic 输入准备时
已固定到 state sidecar，最终汇总却重新保留原项目绝对路径，直到 Package 准入才失败。
汇总必须复用既有 pin 方法，在内容摘要匹配时给出与审查准备相同的不可变引用；原内容
审查后发生变化时拒绝，不能重新签发摘要。显式 descriptor 的 preflight 同步采用
Package 的 sidecar 路径/摘要合同；可物化的普通 `*_ref` 输入仍沿用原合同。此项属于
机械协议修复，不改变 Critic 的语义判断，也不把协议错误伪装成质量驳回。

执行验证另发现：新外部协议要求 Owner 修订 Plan，但 Verify 只在文字中提出重规划，
结构化结果仍为产品拒绝/源码修复范围，导致旧候选重复验收。方法必须区分当前实现缺陷
与尚未批准的合同变化：后者沿用 `blocked` 及带证据的 `plan_ports:<port>` 交接，
不从自由文本猜测控制动作。候选 reader 收到已准入的 Plan port 阻塞时，必须让既有
candidate recovery 处理，不先重发相同候选；缺少准入引用、非 Kernel 事件和普通产品
拒绝仍沿用原路由。历史结果保持不可变；需操作员介入的续跑单列，不能称自动恢复通过。


第四批实测边界：执行中协议变化样本已发现真实冲突，但尚未完成新版交付。旧 Verify
结果未形成 Plan-port 阻塞；操作员请求 OA 后，候选 reader 又遇到 writer-only
`return_to_plan` 目标限制。前述方法/优先级修复已有机械回归，不能追溯替换真实失败。
后续须为候选阶段设计精确候选、Run、operation、generation 和失败证据绑定的修订
意图路径，由 Planner 判断受影响 Task；reader 仅请求修订而不获得实现写权，不把
候选父 Task 冒充实际 writer。新审批、恢复执行及冻结 commit 独立验收全部满足前，
该次运行结束时 P5/P6 待验证；后续真实交付及复验见本节末。完整测量在本地 `reports/plan-economy-20261118/evaluation.md`。

### 候选 Verify 阻塞与执行中修订

候选 Verify 在已完成检查后发现批准的合同阻碍实施时，提交 blocked 裁定、受阻
AC、对应的 rework item 及真实 Plan port。预检和准入核对 port 是否存在于冻结包，
并保留同次检查中已通过或失败的其他条款。聚合阶段从已结算的 reader operation
取回已准入正文，对照当前 fanout、候选、合同快照及目标 commit。冻结 AC 上的
`source_task_id` 与 `source_acceptance_id` 定位实施子任务。映射不完整时由 Planner
重新判断范围；候选的全部输入和 reader 所在的锚点任务都不能代替该判断。

历史候选如果曾提交 rejected，操作员可沿现有 semantic failure 入口提交实际的候选
检查 operation 和聚合失败事件。OA 根据证据决定是否请求 `return_to_plan`。Kernel 在
建立检查点和应用决定时分别校验 Run、代际、当前候选、失败聚合和 reader attempt。
确认修订后，沿现有 Plan 生成和 Owner 审批路径继续。reader 的能力仅限于提供此次
Plan 修订的目标证据；对产品的写入、返工预算和最终验收仍受各自原合同约束。
重复请求、候选更新、作用域无法确认和普通实现缺陷有独立回归。

隔离旅程可通过 `--kanban-model` 固定已验证可用的真实模型；运行报告记录模型。
验收同时要求本 Run 的候选失败指向 Replan、新包请求与批准对应、该包的首次 Impl
派发晚于批准，且唯一完成事件指向该代际实际冻结的 Git commit。最终在该 commit
运行协议 v2 的八项黑盒用例。历史事件回放只证明修复能处理旧证据。

候选冻结收据保留完整集成历史。新版 Task Map 显式替换已完成的旧任务时，最终 Verify
使用新版任务的验收合同。只有当前包、同 Run 同分支的历史冻结 commit、全部替换任务
的当前 Verify 证据及其基线一致时，才将旧任务移出当前验收范围。仍在新版 Task Map
中的任务或未能证明替换关系的条款继续阻塞。
派发和结果准入共用这一校验，均从绑定的子任务快照和同一冻结收据推导范围。

当新 Plan 保留历史行为义务，Thin Judge 还需读取触发该修订的已准入候选验收。
输入绑定旧包代际、候选 commit、原报告、原 spec 与准入引用。逐条验收可与整份
报告的阻塞裁定并存。当前产品仍需本版独立报告。已进入 hold 的旧 Run 可以用
`zf recover goal-evidence --blocked-event <id>` 预览新增证据，再加 `--apply` 重新
请求独立评审。提交裁定时重新校验旧结果信封、内部控制结果、原报告和本次派发事实的摘要。
新版产品报告仍绑定新版冻结提交。当前包或候选改变、终结、人工决定、重复提交均不能通过此入口。

候选恢复命令为重新派发的 candidate.ready 显式记录 kernel 来源。审计旧版空来源
重试时，须核对同一 Run 中失败的 fanout、已执行的恢复收据、原始 kernel 冻结
记录及交付提交，才能取出该 Git 对象进行独立黑盒验收。

本轮 v5 Kanban 执行验证现已完成。两次 Web 审批分别对应各自 Plan，候选失败引发的
修订只替换 T-PROTOCOL-001。最终 Judge 关闭 17/17 项，Kernel 唯一完成事件
`evt-dbe7a0dde4be` 完成交付。归档 Git bundle 上对 03fb9ad8 复跑 8/8 通过。
首次浏览器限时以及同一 Run 中的恢复介入均已记录。该样本覆盖一次协议替换；成本
与延迟的实验目标仍未达到。[整批验收记录](../../tasks/done/2026-11-18-0000-plan-economy-execution-replan.md)

### 从本次派发绑定的 Plan 包开始局部 Replan

执行中协议变化时，Planner 在当前 operation 下用 `zf plan draft-baseline` 导出实际要编辑的
旧端口。一次签发的读取能力绑定 Run、attempt、基线包和端口摘要。Planner 派发中的单条命令会在所属
工作目录创建独立草稿，既有脚本也可从标准输出取 tar。成员按逻辑端口命名并保留原始字节。单个成员缺证则整个导出失败。草稿复用不产生
消费回执。执行中修订的基线已获 Owner 批准。审批前被驳回的预览也可能绑定已经准入的
包，复用它的原件须保留驳回理由，审批状态仍以事件为准。Planner 核实任务、AC、命令和下游入口的影响后，在完整新版正文开头记录
旧新行为、保留项和相应证据，沿现有预览、独立评审及 Owner 审批路径提交新版。

Judge 准备上代候选材料时与提交时共用同一份绑定核验，建立在当前 Run、代际、失败事件、
冻结目标及原验收报告之上。派发之后证据变化仍须重新核对。性能比较使用相同输入的隔离
旅程，分别记录生成、评审和等待时间；超时、人工恢复和实际完成分别计数。
