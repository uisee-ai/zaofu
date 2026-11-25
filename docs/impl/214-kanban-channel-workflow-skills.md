# ZaoFu 的 Kanban Agent、Channel Group 与 Workflow Skill 加载清单

核对版本为 `8ee5770f0`。下文按入口、角色和触发时机列出当前策略。Workflow 主表取 8×8 与 ls-v1 的角色配置，用此版本的 Skill 目录展开依赖。末尾附历史 manifest、会话读取见证和其他生产控制器示例。核对在临时目录完成物化，无需操作 Run。

## Kanban Agent

| 入口 | 类别 | Skill | 生效时机 |
|---|---|---|---|
| Web 聊天，headless | 每个模型回合带正文 | `zf-document-reading` | 每次构建用户回合提示时内联完整正文与 SHA-256。方法要求在编写或评审 PRD、Plan 时应用。 |
| Workflow 路由提案 | 按需 | `zf-workflow-start-planner` | 系统提示要求在方法可用时，用它分类任务、澄清缺失信息、推荐当前配置中的路由。 |
| 显式研究 fanout | 按需的项目入口 | `zf-research-fanout-trigger` | 项目安装该方法、用户显式调用或要求启动研究 fanout 时适用，其说明也调用启动规划方法。Kanban 和 Channel 都可发起。 |

Kanban 发起研究后，固定 fanout 的五个角色是 `source_researcher`、`product_analyst`、`technical_analyst`、`risk_critic`、`synthesizer`。自适应研究走 `research_root`。这六个角色各自的 Skill 列在 Workflow 主表。

Operator 终端的 `SKILLS.md` 汇总项目池和角色直接声明的名称，用于规划。在本次 8×8 与 ls-v1 配置中，投影的 17 个名称相同。

`context-hygiene`、`source-verification`、`zf-cr`、`zf-goal-closure-judge-contract`、`zf-goal-closure-replan-contract`、`zf-harness-state-sync`、`zf-prd-plan-synth`、`zf-project-adapter-matrix-enrichment`、`zf-project-suggest`、`zf-research-adaptive-root`、`zf-verify-rescan-replan`、`zf-workflow-adaptation-boundary`、`zf-yoke-critic-role-context`、`zf-yoke-dev-worker-role-context`、`zf-yoke-orchestrator-role-context`、`zf-yoke-planner-role-context`、`zf-yoke-test-evaluator-role-context`。

项目 Agent Profile 可从项目的 `skills/*/SKILL.md` 选择方法名。Web 将已应用的 profile 随回合记录。Provider 原生目录的可发现范围还依赖实际项目、安装和客户端。

[Kanban 回合构造](../../src/zf/web/headless_agent.py) · [内联正文](../../src/zf/web/headless_methods.py) · [终端目录](../../src/zf/web/operator_session.py) · [目录来源](../../src/zf/web/projections/operator.py) · [研究触发方法](../../skills/zf-research-fanout-trigger/SKILL.md) · [研究角色](../../examples/prod/controller/common/profiles.yaml)

## Channel Group

有 6 个内置模板、25 个默认成员席位，默认模式下的有效方法合计 17 种。8×8 与 ls-v1 的目标配置没有自定义 Channel profile，在其对应状态目录未找到已邀请成员的 profile 快照。表中逐项列出内置模板的默认绑定。项目覆盖成员 profile 或讨论模式后，以该群的快照为准。

- **建群或邀请**须成功解析所选方法及其依赖，记录引用与摘要。
- **普通回合**提供成员在该讨论模式下的有效引用。盲评首轮和自适应下一轮的上下文不内联 Skill 正文，受限阶段只使用提供的上下文。
- **问题去重、合成、交叉评审、共识评审**触发完整台账时，把该成员所有有效方法的正文和摘要放进上下文包。各模板启用哪些阶段还取决于其讨论配置。
- **模式切换**中，`zf-channel-discussion-participant` 和 `zf-channel-discussion-synthesizer` 仅在 `multi_lens` 保留。过滤在展开依赖后按名称执行。因此普通讨论仍可能保留两者引入的文档阅读、浏览器验收、需求对账方法。

“默认有效”一列同时回答普通回合可见哪些引用、触发完整台账时哪些正文必带。“可撤”标出默认加入、允许通过模板覆盖关闭的成员。

| 模板与默认模式 | 成员 | 邀请时直接声明 | 默认有效，含依赖 |
|---|---|---|---|
| `prd-clarification` · `conversation` | `product_pm`、`arch`、`critic`、`dev_reviewer`（可撤） | `zf-channel-discussion-participant` | `zf-document-reading` |
| `prd-clarification` · `conversation` | `synthesizer` | `zf-channel-discussion-participant`、`zf-channel-discussion-synthesizer` | `zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `prd-clarification` · `conversation` | `security_reviewer`（可撤） | `zf-channel-discussion-participant`、`zf-channel-security-review` | `zf-channel-security-review`、`zf-document-reading` |
| `research-review` · `conversation` | `researcher`、`arch` | `zf-channel-discussion-participant`、`zf-channel-research-participant`、`zf-research-preflight-law`、`source-verification` | `zf-channel-research-participant`、`zf-research-preflight-law`、`source-verification`、`zf-document-reading` |
| `research-review` · `conversation` | `critic` | `zf-channel-discussion-participant`、`zf-channel-research-participant`、`zf-research-preflight-law` | `zf-channel-research-participant`、`zf-research-preflight-law`、`zf-document-reading` |
| `research-review` · `conversation` | `synthesizer` | `zf-channel-discussion-participant`、`zf-channel-research-synthesizer`、`zf-channel-discussion-synthesizer`、`zf-research-preflight-law` | `zf-channel-research-synthesizer`、`zf-research-preflight-law`、`zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `architecture-review` · `multi_lens` | `arch` | `zf-channel-discussion-participant`、`zf-channel-discussion-synthesizer`、`zf-cr`、`zf-harness-design-impl-game-review` | `zf-channel-discussion-participant`、`zf-channel-discussion-synthesizer`、`zf-cr`、`zf-harness-design-impl-game-review`、`zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `architecture-review` · `multi_lens` | `security_reviewer` | `zf-channel-discussion-participant`、`zf-channel-security-review`、`zf-harness-evidence-collection` | `zf-channel-discussion-participant`、`zf-channel-security-review`、`zf-harness-evidence-collection`、`zf-document-reading` |
| `architecture-review` · `multi_lens` | `dev_reviewer` | `zf-channel-discussion-participant`、`zf-harness-design-impl-game-review` | `zf-channel-discussion-participant`、`zf-harness-design-impl-game-review`、`zf-document-reading` |
| `architecture-review` · `multi_lens` | `critic` | `zf-channel-discussion-participant`、`zf-harness-gate-evaluator` | `zf-channel-discussion-participant`、`zf-harness-gate-evaluator`、`zf-document-reading` |
| `quick-change` · `conversation` | `tech_leader` | `zf-channel-discussion-participant`、`zf-channel-quick-change`、`zf-channel-discussion-synthesizer` | `zf-channel-quick-change`、`zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `quick-change` · `conversation` | `dev_reviewer` | `zf-channel-discussion-participant`、`zf-channel-quick-change` | `zf-channel-quick-change`、`zf-document-reading` |
| `quick-change` · `conversation` | `qa_analyst` | `zf-channel-discussion-participant`、`zf-channel-quick-change`、`zf-harness-verification-checklist` | `zf-channel-quick-change`、`zf-harness-verification-checklist`、`zf-document-reading` |
| `incident-triage` · `clarification` | `tech_leader` | `zf-channel-discussion-participant`、`zf-channel-incident-triage`、`zf-channel-discussion-synthesizer` | `zf-channel-incident-triage`、`zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `incident-triage` · `clarification` | `qa_analyst` | `zf-channel-discussion-participant`、`zf-channel-incident-triage`、`zf-harness-evidence-collection` | `zf-channel-incident-triage`、`zf-harness-evidence-collection`、`zf-document-reading` |
| `incident-triage` · `clarification` | `security_reviewer`（可撤） | `zf-channel-discussion-participant`、`zf-channel-incident-triage`、`zf-channel-security-review` | `zf-channel-incident-triage`、`zf-channel-security-review`、`zf-document-reading` |
| `prd-clarification-on-demand` · `conversation` | `product_pm`、`arch`、`dev_reviewer`（可撤） | `zf-channel-discussion-participant` | `zf-document-reading` |
| `prd-clarification-on-demand` · `conversation` | `synthesizer` | `zf-channel-discussion-participant`、`zf-channel-discussion-synthesizer` | `zf-document-reading`、`zf-browser-e2e-contract`、`zf-requirement-reconciliation` |
| `prd-clarification-on-demand` · `conversation` | `security_reviewer`（可撤） | `zf-channel-discussion-participant`、`zf-channel-security-review` | `zf-channel-security-review`、`zf-document-reading` |

[模板与可选成员](../../src/zf/runtime/channel_templates.py) · [邀请时解析](../../src/zf/runtime/control_actions_channel_templates.py) · [依赖展开](../../src/zf/runtime/channel_skill_materialization.py) · [模式过滤](../../src/zf/runtime/channel_contracts.py) · [上下文打包](../../src/zf/runtime/channel_context.py) · [各阶段读取约束](../../src/zf/runtime/channel_provider_prompt.py)

## Workflow

8×8 配置共 29 个角色实例，包括 8 个开发和 8 个验证 lane。ls-v1 共 44 个实例，包括各 16 个 lane。两者按名称去重均为 37 个 Skill，其中角色必读 7 个，按需 30 个。8×8 有独立的 `plan-critic`，ls-v1 的角色配置中没有该实例。仓库默认 `zf.yaml` 和 `zf-prd-v5.yaml` 折叠同职责 lane 后，与 8×8 主表一致。

表中的“必须读”是该角色进入实质性工作前应调用或读取的方法。角色简报给出方法索引，正文来自物化包。角色的 `skill_loading` 优先于 Skill frontmatter；按需项在当前任务匹配时读取。Task Pipeline 派发还会合并池、worker profile 和任务的 `skills_required`，重试可沿用原绑定。单次 attempt 的最终策略应查其冻结包。

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `product-scan`、`tech-scan` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `context-hygiene`、`source-verification`、`zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `planner` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `plan-critic` | `zf-yoke-critic-role-context` | `zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |
| `dev` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |
| `flow-discovery` | `zf-yoke-test-evaluator-role-context` | `zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `judge-prd` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `research_root` | 无 | `source-verification`、`zf-project-suggest`、`zf-research-adaptive-root` | 无 |
| `source_researcher` | 无 | `source-verification`、`zf-project-suggest` | 无 |
| `product_analyst`、`synthesizer` | 无 | `zf-project-suggest` | 无 |
| `technical_analyst` | 无 | `source-verification`、`zf-cr` | 无 |
| `risk_critic` | `zf-yoke-critic-role-context` | 无 | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |

“dev”和“verify”分别合并同配置的开发、验证 lane。上表 7 个必读方法全部由相应角色直接声明。其余依赖按去重后的实际解析结果列出。

[8×8 基准](../../zf-prd-v5.yaml) · [策略覆盖](../../src/zf/core/skills/provenance.py) · [角色简报](../../src/zf/runtime/skill_briefing.py) · [Task Pipeline 派发与重试](../../src/zf/runtime/task_pipeline_runtime.py) · [Spec 214](../../docs/spec/214-workflow-skill-evaluation-and-optimization.md)

### 历史对照

使用已归档的 11 组配置和会话观测。跨全部配置共有 39 种当前适用方法，7 种必读、32 种按需。与 8×8 主表相比，多出的两种均出现在 echo-fms 的重构角色。

| 额外按需 Skill | 归档配置的角色 |
|---|---|
| `zf-refactor-plan-synth` | `refactor-plan-synth`、`refactor-scan-contract` |
| `zf-harness-spec-freeze-contract` | `refactor-scan-contract` |

“配置”列按当前目录重新解析。后三列按该组原始证据分别对方法名去重，manifest 与读取见证按各自保留的材料统计。读取见证要求命令指向物化路径且工具返回带有相应 Skill 文头，保留材料之外的阅读情况未知。

| 归档组 | 当前配置方法数 | 原 manifest 已解析 | 原 manifest 标为必读 | 留存会话有读取见证 |
|---|---:|---:|---:|---:|
| `qualification-20261120T122929Z` | 37 | 34 | 7 | 26 |
| `unattended-20261121T132814Z` | 37 | 28 | 6 | 27 |
| `unattended-20261123T151637Z` | 37 | 24 | 5 | 23 |
| `unattended-20261123T171120Z` | 37 | 24 | 5 | 26 |
| `unattended-20261123T234621Z` | 37 | 17 | 3 | 5 |
| `unattended-20261124T001644Z` | 37 | 18 | 4 | 10 |
| `ls-v1` | 37 | 31 | 5 | 28 |
| `ls` | 37 | 31 | 6 | 24 |
| `loshu-fms` | 34 | 31 | 7 | 22 |
| `fms-v1-r2` | 34 | 17 | 4 | 12 |
| `echo-fms` | 36 | 32 | 7 | 24 |

[逐归档、角色、方法的策略与见证表](../../web/src/data/workflow-skill-observations.csv) · [原始读取见证的提取规则](../../scripts/audit-workflow-skill-history.py) · [角色配置复算方法](../../scripts/compare-workflow-skill-history.py)

### 其他随仓发布的 Workflow 示例

以下展开 `examples/prod/controller/` 顶层的 YAML 示例。角色配置与加载策略完全相同的示例合并展示。选用通用、Issue 或重构控制器会改变方法集合。

#### bounded-agent-v1

[bounded-agent-v1.yaml](../../examples/prod/controller/bounded-agent-v1.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `bounded-executor`、`bounded-verifier` | 无 | 无 | 无 |

#### general-workflow-v3-claude

[general-workflow-v3-claude.yaml](../../examples/prod/controller/general-workflow-v3-claude.yaml)、[general-workflow-v3.yaml](../../examples/prod/controller/general-workflow-v3.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `general-collector-a`、`general-collector-b`、`general-scoper` | `zf-evidence-synthesis` | `source-verification` | 无 |
| `general-synthesizer` | `zf-evidence-synthesis` | `zf-project-suggest` | 无 |
| `general-verifier` | `zf-evidence-synthesis`、`zf-yoke-test-evaluator-role-context` | 无 | `tdd-evidence`、`verify-review`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |

#### issue-fanout-v3-claude

[issue-fanout-v3-claude.yaml](../../examples/prod/controller/issue-fanout-v3-claude.yaml)、[issue-fanout-v3-oa-pilot.yaml](../../examples/prod/controller/issue-fanout-v3-oa-pilot.yaml)、[issue-fanout-v3.yaml](../../examples/prod/controller/issue-fanout-v3.yaml)、[issue-task-pipeline-v4-canary-claude.yaml](../../examples/prod/controller/issue-task-pipeline-v4-canary-claude.yaml)、[issue-task-pipeline-v4-canary.yaml](../../examples/prod/controller/issue-task-pipeline-v4-canary.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `fix` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `flow-discovery` | `zf-yoke-test-evaluator-role-context` | `zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `issue-triage` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `debugging-triage`、`source-verification`、`zf-issue-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `judge-issue` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `plan-critic` | `zf-yoke-critic-role-context` | `zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |

#### prd-fanout-v3-claude

[prd-fanout-v3-claude.yaml](../../examples/prod/controller/prd-fanout-v3-claude.yaml)、[prd-fanout-v3-oa-pilot.yaml](../../examples/prod/controller/prd-fanout-v3-oa-pilot.yaml)、[prd-fanout-v3.yaml](../../examples/prod/controller/prd-fanout-v3.yaml)、[prd-task-pipeline-v4-canary-claude.yaml](../../examples/prod/controller/prd-task-pipeline-v4-canary-claude.yaml)、[prd-task-pipeline-v4-canary.yaml](../../examples/prod/controller/prd-task-pipeline-v4-canary.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `dev` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `flow-discovery` | `zf-yoke-test-evaluator-role-context` | `zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `judge-prd` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `plan-critic` | `zf-yoke-critic-role-context` | `zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |
| `planner` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `product-scan`、`tech-scan` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `context-hygiene`、`source-verification`、`zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |

#### prd-light-v3-claude

[prd-light-v3-claude.yaml](../../examples/prod/controller/prd-light-v3-claude.yaml)、[prd-light-v3.yaml](../../examples/prod/controller/prd-light-v3.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `dev` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `judge-prd` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |

#### prd-task-pipeline-v4-oa-bounded

[prd-task-pipeline-v4-oa-bounded.yaml](../../examples/prod/controller/prd-task-pipeline-v4-oa-bounded.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `dev` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `flow-discovery` | `zf-yoke-test-evaluator-role-context` | `zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `judge-prd` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `oa-primary` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `plan-critic` | `zf-yoke-critic-role-context` | `zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |
| `planner` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `product-scan`、`tech-scan` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `context-hygiene`、`source-verification`、`zf-prd-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |

#### refactor-lane-v3-claude

[refactor-lane-v3-claude.yaml](../../examples/prod/controller/refactor-lane-v3-claude.yaml)、[refactor-lane-v3-oa-pilot.yaml](../../examples/prod/controller/refactor-lane-v3-oa-pilot.yaml)、[refactor-lane-v3.yaml](../../examples/prod/controller/refactor-lane-v3.yaml)、[refactor-task-pipeline-v4-canary-claude.yaml](../../examples/prod/controller/refactor-task-pipeline-v4-canary-claude.yaml)、[refactor-task-pipeline-v4-canary.yaml](../../examples/prod/controller/refactor-task-pipeline-v4-canary.yaml)

| 角色 | 必须读 | 按需 · 角色直接声明 | 按需 · 依赖带入 |
|---|---|---|---|
| `dev` | `zf-yoke-dev-worker-role-context` | 无 | `debugging-triage`、`git-evidence`、`incremental-delivery`、`source-verification`、`tdd-evidence`、`zf-find-simplifications`、`zf-harness-done-contract`、`zf-harness-evidence-collection` |
| `judge-refactor` | `zf-goal-closure-judge-contract` | 无 | 无 |
| `module-parity-scan` | `zf-yoke-test-evaluator-role-context` | `source-verification`、`zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `orchestrator` | `zf-yoke-orchestrator-role-context` | `zf-harness-state-sync` | `context-hygiene`、`diagnosis` |
| `plan-critic` | `zf-yoke-critic-role-context` | `zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-plan-execution-readiness`、`zf-requirement-reconciliation` |
| `refactor-plan-synth` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `zf-refactor-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `refactor-verify-bridge` | `zf-yoke-test-evaluator-role-context` | `zf-goal-closure-replan-contract`、`zf-verify-rescan-replan` | `tdd-evidence`、`verify-review`、`zf-gap-task-synth`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier`、`zf-verify-gap-producer-contract` |
| `scan-contract` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-planner-role-context` | `context-hygiene`、`source-verification`、`zf-harness-evidence-collection`、`zf-harness-spec-freeze-contract`、`zf-refactor-plan-synth`、`zf-workflow-adaptation-boundary` | `grill`、`vertical-slicing`、`zf-browser-e2e-contract`、`zf-document-reading`、`zf-gap-task-synth`、`zf-plan-execution-readiness`、`zf-plan-task-map-contract`、`zf-requirement-reconciliation` |
| `scan-runtime` | 无 | `context-hygiene`、`source-verification`、`zf-harness-evidence-collection` | 无 |
| `scan-verification` | `zf-yoke-test-evaluator-role-context` | 无 | `tdd-evidence`、`verify-review`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |
| `verify` | `zf-project-adapter-matrix-enrichment`、`zf-yoke-test-evaluator-role-context` | `zf-workflow-adaptation-boundary` | `tdd-evidence`、`verify-review`、`zf-browser-e2e-contract`、`zf-harness-evidence-collection`、`zf-harness-verification-checklist`、`zf-mechanical-claim-verifier` |
