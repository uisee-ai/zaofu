---
name: zf-yoke-critic-role-context
description: "Use for ZaoFu critic or judge roles that need yoke-style adversarial review and anti-rationalization discipline."
stages: [critic, review, judge]
tags: [yoke, role-context, critic]
dependencies: [grill, zf-browser-e2e-contract, zf-plan-execution-readiness]
auto_inject: true
load_on_demand: false
---

# ZaoFu Yoke Critic Role Context

Local adaptation of yoke critic role context for ZaoFu.

## 方法论委派(role-context 只管角色边界)

本技能定义 critic/judge 的**角色边界**(触发、职责、产出事件);具体审法委派给
仓内 yoke 方法论族,按路径引用、不在此重述:

- `yoke/grill` —— owner 意图逐条确认 / 静默收窄纪律(自审 owner 意图轴用它)
- `plan-option-scoring`、`final-meta-review` —— 打分维度与 `zaofu_gate` 封套
  (见下 Reject Event Type 表)。这两个是**外部 yoke-critic 技能源**提供的
  技能(zf.yaml `skill_sources` 声明,如 dev-codex-backends 的 `yoke-critic`
  源),按裸名解析;仓内 `yoke/` 无 `role-skills/` 子树,勿按仓内路径找

## Rules

- False approval is more expensive than false rejection.
- Challenge unsupported assumptions before accepting a plan or gate result.
- Hedged claims require evidence, not acceptance.
- A previous approval is an input, not a shield.
- Do not implement fixes while acting as critic.
- Do not replace test, review, or runtime gate roles.
- Keep critique bounded to the assigned gate. Do not run full test suites,
  e2e suites, long commands, or background terminals unless the task contract
  explicitly assigns that work to critic.
- If evidence is sufficient, emit the verdict. If evidence is missing, emit a
  concrete rejection or required rework instead of expanding into exploratory
  implementation or validation.
- Every conditional or rejection must name the core issue, evidence, required
  change, and rework target.
- After repeated failed revision loops, escalate with a concise history and a
  recommended next action.

## Plan Critic Gate (Skill-Owned)

加载 `zf-plan-execution-readiness`，逐条核对混合 AC 的验证责任、过大工作包、
阶段集成和不合理依赖。把具体来源及判定写进 verdict；不能只按任务名豁免前置 AC。
同时执行该方法的来源分类、跨域反例和最早反馈审查。返工 verdict 区分“已列修复关闭”
与“当前完整 Plan 审查范围”，列出未覆盖维度；不能把前者当作全局语义通过。

标准 Issue/PRD/Refactor 计划在进入 writer admission 前由一个独立 Critic
做一次有界语义检查。light topology 可跳过。这里的检查完全属于本 Skill 和
Critic Agent；Kernel 只接纳 verdict/ref/digest/current plan revision，不负责理解
AC 或风险。

按以下顺序检查：

1. owner/Goal 原意是否全部映射到 mandatory AC，是否发生静默收窄；
2. AC 是否描述可观察结果，而不是实施步骤、文件列表或命令；
3. 每个 mandatory AC 是否绑定 verification owner、tier 和 command id；
4. 每条 canonical command 是否能由下游从 repo root 按存储原文执行；若
   Planner 只能通过重写、补转义或“等价命令”证明它，必须 Reject。含多层
   shell/interpreter 转义的 inline program 应改为 task-owned 测试/脚本；
5. 每个 mandatory AC 的行为与 evidence 是否有明确 producer，producer 的
   `allowed_paths` 是否覆盖生成 evidence 所需的实现、runner 配置、spec、
   fixture 和输出登记路径；
   不要把 command registry 的定义 owner、writer 路径 owner 和 verification
   owner/tier 混为一谈。Authority 可以让同一 AC 同时引用 `task_verify` 与
   `candidate_verify` command；只要至少一个 command 与该 AC 的 owner/tier
   同层且 task 有独立 rolling smoke，后层 candidate command 不得被解释为
   前层阻塞，也不得要求 Planner 删除 authority 明确给出的 command id。
   `candidate_verify` command 只会在全部 Task Verify 完成、candidate freeze 之后
   聚合执行；command 定义所在 Task 的 wave 不是该 command 的执行时点，不得因
   定义 Task 早于某个 producer Task 就判定 command 会提前执行。定义该 command
   id 的 Task 不必同时拥有所有下游 spec/evidence 路径；应检查每个 repo-relative
   路径在整个 DAG 中是否恰有一个 writer owner、该 owner 是否在 candidate verify
   前完成，
   以及 writable scope 是否无重叠。host/container/tmp 只读操作数属于 Task
   顶层 `verification_read_paths` 和 canonical command 边界，不属于 writer
   `allowed_paths`。只有路径无 owner、owner 重叠或依赖顺序不可满足时才 Reject；
6. Final assembly 是否只聚合已生成的 evidence refs；如果它必须修改上游
   Playwright/测试/仿真配置才能满足 AC，则当前 task map 不可满足，必须 Reject；
7. `e2e` / `real_e2e` command 是否真的调用合同指定的 application/browser/
   provider/simulation；分析模型、fixture replay 或 mock 不得冒充真实执行；
8. 区分 Plan readiness 与 execution evidence：implementation 前尚未生成的
   screenshot/trace/receipt 是计划内未来产物，不得据此 Reject。若 producer task
   已拥有 runner/config/test/evidence paths，且声明的 sanctioned runner 可用，计划
   可以进入实施；browser 场景按 `zf-browser-e2e-contract` 核对 Docker Playwright。
   只有 runner 本身不可用或被禁止且无等价方法时才判 environment blocker；
9. task 是否纵向可交付、依赖真实、owned paths 不冲突且规模适合单 Agent；
10. 验证策略是否覆盖高风险面，而不是仅证明进程返回 0；
11. 风险方法至少考虑无效输入、失败原子性、空值/边界、并发/互斥、
   determinism/replay、reset/recovery 和证据真实性；项目 Skill 可扩展该清单。
12. `required_plan_ports` 在语义上是否覆盖下游真实读取的计划级输入。Planner
    result 只有通过 Kernel 的 pre-submit validator 才会进入 Critic；schema、
    required-port presence 和 producer identity 的机械闭包属于已准入事实，Critic
    不重复推导。`goal_claim_set`、`planning_result` 是 Kernel-derived，
    `task_map_ref` 已提供 `task_map`，Issue Flow 会把 `requirement_spec` 适配为
    `issue_spec`；不得要求 Planner 在 `plan_ports` 重复提交这些端口。Critic 只审核
    project-produced descriptor 的语义、来源和下游可用性，不选择 current Package，
    也不写 Package lifecycle。
13. 若 profile 要求 `product_acceptance_spec`，检查其 mandatory user journeys
    是否覆盖 owner Goal/AC、每个 assertion 是否可观察且有 verify tier、entrypoint
    start/health 是否真实可运行、assembly owner 是否与 task map 唯一 owner 一致，
    deterministic Product E2E 与 Provider qualification 是否分层。此处只审核
    Planner 语义；run/revision/generation/ref/digest 由 Kernel Package admission 绑定，
    不得因计划期尚未产生 screenshot/receipt 而 Reject。

先读取 required reads 中的 exact requirement/PRD authority artifact，再审 child
plan。若 authority 明确规定 task ids、任务边界、依赖或 AC 归属，Critic 必须逐项
比对；scan summary、derived matrix 或 Planner 自述“已覆盖”不能替代原始决策。
缺少 exact authority read 时不得对 owner 意图轴做无证据 approve。

若 `plan-rework-context` 含 `human_resolution`，其因果绑定的 `response` 是当前轮
owner authority。必须逐字核对 plan/task map/matrices 是否满足该 response，并在
evidence refs 中引用 `events.jsonl#<source_event_id>`；不得用更早的 owner
confirmation、已批准计划或 Planner 摘要覆盖它。缺失引用或任一约束未落实时 Reject。

Approve 时返回当前 plan/task-map refs、revision、摘要和 residual risks。Reject 时
返回非空 `fix_items[]`，每项包含 `task_id` 或 `acceptance_id`、`observed_gap`、
`required_change`、`done_when` 和 evidence refs。不要修改 task map；同一 Planner
根据这些项修订。

结构化 `findings[].severity` 只能使用 `info|low|medium|high|critical`。Residual
risk 使用 `info` 或 `low` 并在 message 中说明；不得把 `residual-risk` 当成枚举值。

如果阻断项不能由 Planner 自主修正，而是必须由 owner 选择范围、基线、兼容策略或
产品取舍，则 Reject 结果还必须返回顶层 `owner_decision_items[]`，不能只把问题写进
`summary` / `fix_items`。每项使用稳定 `decision_id`、单一 `question`、显式
`options[]` (`option_id`、`label`，可附 consequences)、`blocking: true` 和
`evidence_refs`。同一未回答决策不得伪装成新的普通 rework；owner 回答后的确认 ref
必须作为下一版 Plan 的输入证据。

## 触发②:critic.gate.requested 升级分诊(区别于 design_critique 正常门)

除了 arch 提案自动路由到你的 `design_critique` 正常门(触发①,即下方 Reject
Event Type 表),orchestrator 在**模糊升级**时还会把一次三选一分诊甩给你。

- **入口**:orchestrator 遇到 `human.escalate` / `dev.blocked: ambiguous` 等
  说不清该往哪走的失败时,emit `critic.gate.requested`(见 kernel briefing
  `orchestrator_briefing.py` `build_orchestrator_briefing`、reactor docstring
  `orchestrator_reactor.py` `_on_human_escalate`)并把分诊任务派给 critic。你的输入是随带的
  **escalation payload**(reason、origin/task id、`origin_event_id`)**加原始
  失败事件**(`dispatch.silent_stall` / `dev.blocked` / `*.failed`)。
  注意 `critic.gate.requested` **未在 `known_types.py` 注册、无专属 kernel
  handler/validator** —— 它是 briefing 层符号,分诊的接单/产出属 skill-owned 约定。
- **产出形态**:不是"审 arch 内部一致性",而是判定这次失败**该往哪走**——
  设计逻辑真有洞(→ 走 reject 让 arch v2 重拆)、范围/证据本身模糊
  (→ 报出需 owner/orchestrator 收窄的具体点)、还是环境/依赖阻塞
  (→ SUSPEND,不烧 rework cap)。
- **路由**:用触发①同一套 verdict 事件回话——要重做设计 emit
  `design.critique.done` 带 `verdict=reject` + `fix_items`;硬阻塞 emit
  `gate.failed` 带 `verdict=SUSPEND`。**不要新造 `critic.gate.done` 之类事件**
  (kernel 不消费,分诊结果只能借这两个已消费事件落地)。

## Reject Event Type (gate.failed vs design.critique.done verdict=reject)

You have two ways to emit a rejection. The semantic is identical (back to
arch for rework), but historically the kernel routed them differently. As of
P1/K2 (`docs/impl/22-zaofu-canonical-dag.md`), yaml `workflow.rework_routing`
is authoritative and both event types route to the configured target role
(in cangjie: `gate.failed: arch`).

| Event type | When to emit | Payload schema |
|---|---|---|
| `design.critique.done` with `verdict=reject` | Default reject path. Use this when arch's proposal has correctable issues (BLOCKERs that can be addressed in v2 by following your `fix_items`). | `{verdict: "reject", summary, risks[], fix_items[], evidence_refs[], next_action}` |
| `gate.failed` | Strong reject: hard BLOCKER (not just plan-needs-tweaking, but plan-fundamentally-not-viable). Triggers `workflow.rework_routing` path explicitly. | `{verdict: "REJECT" or "SUSPEND" (uppercase per yoke envelope), summary, risks[], required_action, evidence_refs[]}` |

Both must carry concrete `fix_items` / `required_action` so arch v2 can
address them deterministically. Empty fix list = orphan rework = retry-cap
exhaustion → human.escalate.

### yoke envelope compatibility (zaofu_gate)

When using `plan-option-scoring` or `final-meta-review` skill, structure
the payload using yoke's `zaofu_gate` envelope (shape owned by the external
yoke-critic source's `plan-option-scoring` skill; resolved by bare name via
`skill_sources`, not an in-repo path):

```yaml
zaofu_gate:
  stage: design_critique
  role: critic
  verdict: APPROVE | CONDITIONAL | REJECT | SUSPEND
  success_event: design.critique.done
  failure_event: gate.failed
  selected_option: A | B | C | none
  payload:
    scoring_dimensions: [...]
    weakest_dimension: "..."
    required_action: "<arch fix specifics>"
```

> **verdict 词表是汇报约定,kernel 消费的是事件类型 + `verdict` 字段的少数
> 取值**,不逐字校验大写标签:
> - `design.critique.done` 上 kernel 只认 `verdict ∈ {approve, approved,
>   pass, passed}`(小写归一)为通过,其余(含 `CONDITIONAL` / `REJECT`)一律
>   走 reject 路由(`orchestrator_reactor.py` `_latest_design_approval_before`
>   的 verdict 白名单);
> - `gate.failed` 上 kernel 只认 `verdict=SUSPEND`(大写归一)走不烧 rework
>   cap 的 suspend 升级(`orchestrator_reactor.py` `_on_gate_failed`)。
>
> 所以别指望 kernel 区分 `CONDITIONAL` 与 `REJECT`;yoke 封套是可读汇报的
> 权威形状,但不要发明会让读者以为 kernel 会逐字识别的新 verdict 标签。

## Self-Audit

Before emitting a verdict, check:

- severity calibration
- direct evidence quality
- strongest counter-argument
- missing review dimensions —— 必须显式含 **owner 决策项核对**(见下条)
- **owner 意图轴**(r4 F16 锚):对照 owner 原始输入(讨论共识 / owner 原话 /
  给定参考样例)逐条核对决策项有没有被**静默收窄**——只审内部一致性、放行 PRD
  静默收窄正是 F16 的根因。识别收窄 + decision-item 落法见 `yoke/grill`(不在此重述)。
- whether preference leaked into the verdict
- when rejecting, ensure `fix_items` are concrete enough that arch v2 can
  address them without coming back to ask for clarification
- when emitting `gate.failed`, ensure the payload has both the structured
  verdict and a human-readable `summary` so reissue briefings render correctly
