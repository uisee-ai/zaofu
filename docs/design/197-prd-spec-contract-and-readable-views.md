# PRD/Spec 合同与分层阅读视图

> 状态: candidate
>
> 本文是设计提案，记录 PRD 生成、校验和展示的目标形态；不表示当前 Runtime
> 已经实现本文全部能力。

## 1. 背景与问题

Channel Group 已经可以从 `conversation` 讨论生成正式 PRD，并继续进入 Task 与
Workflow。当前 PRD 同时承担了产品说明、来源追溯、质量评估、验证命令和交付流程
合同，导致三个问题：

1. 产品正文没有稳定地覆盖 Spec Kit 的用户故事、独立测试和可度量成功标准；
2. OpenSpec 所要求的单行为 Requirement、场景和变更增量没有成为统一格式；
3. 产品经理、架构师和资深开发看到的是同一份混合文档，正文和机器合同重复，阅读成本高。

本设计采用“语义由 Skill 负责、机械合同由 Runtime 负责、展示由 Web 负责”的边界，
不把产品判断硬编码进 Kernel。

## 2. 目标

- 让 Channel 生成的 PRD 具备 Spec Kit 的产品完整性：用户、范围、优先级、用户旅程、
  验收和可度量结果。
- 让 PRD 的行为部分具备 OpenSpec 的可验证性：每条 Requirement 只有一个可观察行为，
  每条 Requirement 至少有一个 Given/When/Then 场景。
- 将 Proposal（为什么）、PRD/Spec（做什么）、Design（怎么做）、Tasks（执行步骤）
  和 Evidence（如何证明）分层保存。
- 保留 ZaoFu 的来源、digest、Owner 决策、权限审计和 Workflow 交接合同。
- 为产品经理、架构师和资深开发提供同一事实源上的不同阅读视图。
- 在 Owner Confirm、Task 创建和 Workflow 启动边界提供可重放的机械校验。

## 3. 非目标

- Runtime 不判断用户故事是否有商业价值，不替代产品、架构或安全角色。
- 不为 `conversation`、`clarification` 和 `multi_lens` 维护三套 PRD 格式。
- 不把 Docker、Playwright、Provider、内部事件名或 Workflow 审批写入产品正文。
- 不让 Channel 直接修改 Task、Workflow 或其他 Kernel 管理的 canonical state。
- 不在本设计中规定具体的数据库、API 框架或前端组件。

## 4. 分层产物

一次 PRD 讨论对应一个可冻结的规格包。正文和机器合同绑定同一 revision、ref 和 digest，
但职责不同。

| 层 | 主要内容 | 责任角色 | 下游消费者 |
| --- | --- | --- | --- |
| Proposal | Why、Why now、目标、范围、非目标、影响能力 | product_pm / Owner | 人工评审、PRD 作者 |
| PRD/Spec | 文档控制、问题、目标、用户旅程、Requirement、Scenario、业务规则、非功能要求、验收和成功指标 | synthesizer | Planner、产品和技术评审 |
| Design | 架构边界、依赖、数据模型、接口、状态、迁移、技术取舍 | arch | Planner、开发 |
| Tasks | 有序任务、依赖、每项验证方式 | planner | Task/Workflow |
| Evidence | source refs、coverage、decision log、命令、执行结果、digest | Runtime / Verify | 审计、复盘、质量门 |

Channel 的正式 PRD 仍通过现有 `prd_markdown` 或文件/composition 入口生成；
`requirement_coverage`、`acceptance_criteria`、`non_functional_requirements`、
`success_metrics`、`decision_log`、`verification_commands`、`readiness` 和 `source_refs`
作为结构化附录或 sidecar，不作为正文的第二份 JSON。

## 5. PRD/Spec 正文合同

不存在适用于所有组织的唯一 PRD 模板；本合同提供 ZaoFu 的标准结构。章节标题是稳定的
可读约定，正文负责解释产品行为和目标，Runtime 只检查章节存在和引用关系，内容质量由
Skill 和独立评审负责。

```text
# Capability / Feature

## 00. Document control
- Document ID, revision, status, owner, updated-at, source refs, approval state

## 01. Overview
- What the product or capability is, why it exists, and why now

## 02. Background and problem
- Current pain, affected users, existing alternatives, and impact

## 03. Goals, non-goals, and scope
### Goals
### Non-goals
### In scope
### Out of scope / future scope

## 04. User personas and prioritized scenarios
### Target users
### P1 - <journey>
- User story and value
- Why this priority
- Independent test or demo

### P2 - <journey>
### P3 - <journey>

## 05. User flow and interaction
- Main flow, alternative flow, system response, error feedback, and recovery entry points

## 06. Functional requirements
### FR-001: <one observable behavior>
The system SHALL ...

#### Scenario: <named case>
- GIVEN ...
- WHEN ...
- THEN ...

#### Acceptance
- AC-001: <objective acceptance statement and verification method>

## 07. Business rules, permissions, and state
- Business constraints, roles, permissions, observable states, transitions, idempotency,
  exceptions, and recovery rules

## 08. Non-functional requirements
- Measurable performance, security, reliability, auditability, accessibility, compatibility,
  scalability, and observability constraints

## 09. Success metrics
- SC-001: <measurable user or business outcome>

## 10. Dependencies, assumptions, risks, and open questions
### Dependencies
### Assumptions
### Risks and mitigations
### Open questions and decision register

## 11. Rollout, migration, and operations
- Add when the change requires rollout, migration, rollback, monitoring, or operational ownership

## Appendices
- Glossary
- Requirement / acceptance / task traceability
- Evidence and source references
- Technical Design / SDD reference
```

正文规则：

- `Document control` 必须标明当前 revision、Owner、来源和确认状态；Owner Confirm 只确认
  一个具体 revision。
- 一个 Requirement 只表达一个外部可观察行为；使用 `MUST` 或 `SHALL` 表达强制性。
- 每个 Requirement 至少一个 Scenario；成功、失败、权限、边界和恢复场景按风险覆盖。
- P1/P2/P3 只描述可以独立测试和演示的用户旅程；P1 单独实现也应形成可用 MVP。
- 每条 Acceptance 必须有客观通过条件、验证方法和证据归属；Acceptance 是功能正确性，
  `Success metrics` 是用户或业务结果，二者分别维护。
- 非功能要求必须写成可度量约束；“高性能”“容易使用”等定性表述需要补充观察方法或
  明确保留为待决问题。
- 业务规则和状态只描述产品可观察约束；技术实现、命令、内部流程和组件选择放入
  Design、Tasks 或 Evidence。
- Open question 未解决前不得在正文中写成已确定的产品决定；每项应记录影响范围、决策者、
  最迟决策时间和允许继续的范围。
- 依赖、风险和上线/迁移要求不能只写在聊天或 Provider transcript 中，必须进入正文或
  可追溯的附录。
- 既有能力的变更在增量层使用 `ADDED`、`MODIFIED`、`REMOVED`；`MODIFIED` 必须保留
  完整的更新后 Requirement 和全部场景。

正文只保留产品语义和稳定 ID。`source_refs`、`requirement_coverage`、`decision_log`、
`verification_commands`、`ref/digest` 和 Task/Workflow 交接字段放入结构化附录或 sidecar，
不在正文复制整份机器合同。

## 6. 结构化合同

正式 synthesis 继续使用现有字段，并补充一个轻量的格式声明：

```json
{
  "spec_format": "prd-spec.v1",
  "document_control": {
    "revision": "<revision>",
    "status": "draft|confirmed",
    "owner": "<owner>",
    "profile": "lite|full"
  },
  "prd_markdown": "<human-readable product specification>",
  "requirement_coverage": [],
  "acceptance_criteria": [],
  "non_functional_requirements": [],
  "success_metrics": [],
  "decision_log": [],
  "verification_commands": [],
  "readiness": {},
  "source_refs": []
}
```

`spec_format` 只声明正文合同版本，不代表质量通过。质量判断仍由 Skill、独立评审和
Owner authority 完成。

Runtime 负责以下机械检查：

- `prd_markdown`、`prd_file`、`prd_composition` 只能三选一；
- `spec_format`、revision、source ref 和 digest 可解析且彼此一致；
- `document_control` 的 revision、status、Owner 和 profile 与外部确认状态一致；
- Requirement、Scenario、NFR、Success Metric、Acceptance ID 不重复；
- 每个 Requirement 有场景，每个 covered requirement 有正文 section 和 acceptance ID；
- 每个 mandatory acceptance 有验证命令和 producer path；
- `success_metrics` 与 `acceptance_criteria` 分别表达结果指标和功能通过条件；
- `open_questions`、`readiness.verdict`、Owner confirmation 和 `implementation_start`
  不相互矛盾；
- Task/Workflow 引用的 PRD ref 和 digest 与 Owner 确认版本完全一致。

Runtime 不检查：优先级是否正确、需求是否有价值、推荐方案是否最好、文字是否足够
有说服力。这些由角色 Skill、cross-review 和 Owner 完成。

## 7. Skill 责任

### 7.1 Synthesizer

扩展 `zf-channel-discussion-synthesizer`，要求正式 synthesis 按第 5 节生成正文，
并保留现有的来源、覆盖、决定、异议、风险和验收字段。

Synthesizer 必须：

- 先完成 requirement reconciliation，再写 PRD；
- 将 Owner 决定、暂缓事项和推荐方案区分开；
- 为每个 Requirement 生成至少一个可测试场景；
- 对高风险需求覆盖权限、异常、恢复和边界行为；
- 把技术实现选择留给 Design，不把验证命令堆进产品正文；
- 在正文中使用稳定 ID 引用附录，而不是复制整份机器合同。

### 7.2 Planner

`zf-prd-plan-synth` 将 Requirement/Scenario/Acceptance 映射为 Design 条目和 Task Map。
一个 Task 不得凭空出现；每个 Task 至少引用一个 Requirement 或明确的交付合同条目。

### 7.3 Review / Verify

审查分成两个时点：

1. Owner Confirm 前：检查 Proposal 是否解决正确问题、范围是否膨胀、Requirement 是否
   可测试、场景是否覆盖最重要的失败路径。
2. 实现后：检查 Completeness、Correctness、Coherence，并将未覆盖场景转为 rework 或
   明确的 Owner 决定。

### 7.4 Spec bridge 与 freeze

手工 Markdown 的 `zf spec validate/ingest` 继续由 `zf-harness-spec-bridge` 负责；
Channel fanout 的计划仍使用 `task_map.json`，不得用 `zf spec ingest` 重铸任务。

规格进入 Task/Workflow 前由 `zf-harness-spec-freeze-contract` 负责稳定 ref、digest 和
attestation；Agent 不直接写 canonical state 或 attestation 文件。

## 8. 人类阅读视图

同一份 PRD 提供三种过滤视图，不复制三份正文。

### 产品经理视图

默认展示 Purpose、用户、P1/P2 旅程、范围、成功指标、非目标、Owner 决定和未决问题。
隐藏 source digest、Provider、Workflow action 和原始命令。

### 架构师视图

在产品正文基础上展开实体、权限、安全、可靠性、状态变化、依赖、迁移和风险。技术
选择来自 Design，不把实现选择伪装成产品需求。

### 资深开发视图

展开 Requirement/Scenario、输入输出、错误行为、并发和版本约束、Task 映射、验证命令
和 producer paths。仍然以同一 PRD revision 为来源，不重新解释产品范围。

默认页面只显示正文；追溯、验证和交付合同使用可展开附录。不得在正文和附录重复复制
大段 JSON；只保留稳定 ID 和必要链接。

## 9. 生命周期

```text
conversation / clarification / multi_lens
  -> semantic synthesis
  -> draft PRD/spec
  -> independent review and Owner questions
  -> Runtime contract validation
  -> Owner Confirm
  -> freeze ref/digest
  -> Design / Task Map
  -> Task approval
  -> Workflow approval and start
  -> Verify against Requirement/Scenario
  -> revise, sync or archive
```

草案可以包含 `needs_owner` 或 `deferred`，但不能进入 Task/Workflow。Owner Confirm 只
确认具体 revision；后续修改必须生成新 revision 并重新绑定来源、评审和确认。

## 10. 验收标准

### Contract

- 生成一份包含文档控制、Overview、Background、Goals/Non-goals、用户旅程、功能需求、
  业务规则/状态、非功能需求、Success metrics、Acceptance、依赖/风险/未决问题的正式 PRD。
- 每条 Requirement 至少绑定一个 Scenario 和一个 Acceptance；非功能需求必须有可观察的
  指标或明确的验证方法。
- 缺少任一必需结构、存在重复 ID、覆盖关系断裂或状态矛盾时，Owner Confirm 前被拒绝。
- Draft 可以继续讨论；未确认 PRD 不得创建 Task 或启动 Workflow。

### Readability

- 产品经理能只读正文回答“给谁、解决什么、先做什么、如何判断成功”。
- 架构师能展开附录回答“边界、依赖、权限、状态、风险是什么”。
- 资深开发能从 Requirement 到 Scenario、Task 和验证命令建立完整映射。
- 正文不出现内部 Provider、Workflow action、原始 JSON 或重复合同表。

### Traceability

- 每条 Requirement 至少有一个 Scenario、Acceptance、Task 和验证证据。
- 每个 Success Metric 和非功能需求都有来源、责任人或验证入口；无法验证的指标保留为
  Open Question，不得伪装成已确认目标。
- 每个 Task 都能回溯到确认的 PRD revision。
- PRD revision、Task contract 和 Workflow proposal 的 ref/digest 一致。

## 11. 风险与取舍

- **格式过重**：通过 Lite/Full 两档控制。简单改动只需少量 Requirement 和场景；涉及
  安全、迁移、权限、跨服务或不可恢复数据时使用 Full。
- **正文过长**：正文只保留产品行为和稳定 ID，证据与命令进入附录/sidecar。
- **语义误判**：不将产品质量硬编码为 Python 规则，保留独立角色评审和 Owner authority。
- **旧 PRD 兼容**：没有 `spec_format` 的历史文档继续可读；只有新 revision 选择新合同，
  通过 composition 或显式修订逐步迁移。

## 12. 待审核问题

1. 是否将 `prd-spec.v1` 作为所有新 Channel PRD 的默认正文合同？
2. Lite/Full 的切换由模板声明、Owner 选择，还是由 Runtime 根据风险字段建议？
3. Web 是否默认使用三个阅读视图，还是先提供正文与“技术/证据附录”两层？
4. Spec Kit/OpenSpec 的外部 CLI 校验是否只作为 CI/Skill 工具，还是允许在 Owner Confirm
   前作为可选增强检查？
