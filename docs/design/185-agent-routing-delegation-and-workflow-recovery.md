# 主 Agent 路由、受控协作与 Workflow 断点恢复

> 状态：candidate — 分期设计；2026-10-23 已部分实施，未整体签收。实施与逐分支证据见 [验证记录](../impl/185-routing-admission-validation.md)。
> 日期：2026-10-23。
> 范围：Intake → 处理方式选择 → Workflow／有界 Agent 执行 → 异常恢复；包含 Kimi Code 本地源码对照。
> 基线：ZaoFu `b493d88608413e8042c1e62cf9cbf06d607a3df2`，184 worktree；Kimi Code `874d9abb32f7d8e23fd95da5fbfbd446266653ec`。
> 编号说明：185 承接既有 184 实施主题。本 checkout 未包含 184 设计正文，不在此补写或宣称恢复历史文档。

## 1. 决策摘要

1. 主 Agent 先决定如何处理请求：回答、澄清、仅记录、有界 Agent 执行、Workflow。
2. 有界 Agent 执行支持两种协作策略：一个执行者直接完成，或者一个根 Agent 调用受限助手。
   后者在产品上仍是一次任务执行，技术上必须如实显示存在多个 Agent。
3. `headless`／tmux 是运行方式；single／delegated／Workflow 是协作与编排方式；
   Codex／Claude Code／Kimi Code 是 Provider。三个维度独立，不互相替代。
4. 自动执行优先采用可观测、可取消的 headless 接口；终端能力按实际需要选择。
   headless 不表示自动批准，也不表示关闭权限控制。
5. 主 Agent 负责语义推荐，Kernel 负责批准绑定、执行权、状态迁移和完成证据。
   Run Manager 保留恢复决策入口，Recovery Agent 是按需调用的诊断／修复能力。
6. Workflow 故障时优先接替一个失败阶段，保持 Task、依赖和独立验证；
   改为整段串行执行或改变拓扑，必须走受控重规划。
7. 复用现有 Task contract、Proposal、Execution Profile、Workflow Operation、TaskAttempt、
   State Packet 和受控 action，不新建平行任务账本或 Agent 调度状态机。
8. 第一阶段使用已接入的 Codex／Claude 能力；Kimi 先做 adapter canary，
   通过权限、子树取消、结果和恢复合同验证后再开放 native delegation。
9. 区分 Kanban Agent 直接提交和 Channel Group 从 canonical PRD 提交两条入口；
   统一规划能力，保留各自的来源、权限和批准绑定。PRD ready 本身不触发执行。
10. 增加 Workflow Planner／Configurator 的两阶段适配：需求就绪后初评，
    Task Map 形成后复核；优先复用有效 profile，必要配置变更走受控准入。

## 2. 当前实现与缺口

### 2.1 已核对的 ZaoFu 能力

| 能力 | 当前证据 | 本设计的处理 |
| --- | --- | --- |
| 配置派生路线目录 | [workflow_route_catalog.py](../../src/zf/runtime/workflow_route_catalog.py) | 继续以当前有效 `zf.yaml` 为唯一配置来源 |
| 主 Agent 语义选路 | [headless_agent.py](../../src/zf/web/headless_agent.py)、[选路 skill](../../skills/zf-workflow-start-planner/SKILL.md) | 增加执行方式选择方法；不使用关键词决定状态 |
| 启动预览、绑定和提案 | [workflow_start.py](../../src/zf/runtime/workflow_start.py) | 保留明确 `route_id` 的确定性启动入口 |
| 有界 Provider turn | [webchat_execution_bridge.py](../../src/zf/web/webchat_execution_bridge.py)、[AgentTurnStore](../../src/zf/web/agent_turn_store.py) | 复用对话执行生命周期；交付事实仍走 Task／Operation |
| Execution Profile | [execution_profiles.py](../../src/zf/runtime/execution_profiles.py) | 已有策略、协作和能力检查，但此解析器明确是 E0 shadow、`dispatch_effect: none`；不能称为已贯通的自适应调度 |
| Provider 能力投影 | [provider_capabilities.py](../../src/zf/runtime/provider_capabilities.py)、[backend.py](../../src/zf/runtime/backend.py) | Kimi 尚无注册 adapter；原生协作能力需通过实际接入证据开放 |
| Workflow checkpoint／apply | [workflow_resume.py](../../src/zf/runtime/workflow_resume.py)、[workflow_resume_apply.py](../../src/zf/runtime/workflow_resume_apply.py) | 扩展已有恢复链，不建立第二状态机 |
| v4 operation 恢复 | [task_pipeline_recovery.py](../../src/zf/runtime/task_pipeline_recovery.py) | 同合同故障保留 operation、创建新 attempt；语义返工按新 generation 处理 |
| 新会话恢复 briefing | [recovery_briefing.py](../../src/zf/runtime/recovery_briefing.py) | State Packet 为接续依据，transcript 仅作辅助证据 |

当前主 Agent 建单默认 `execution_mode=workflow`；`direct` 仅用于用户明确要求的
legacy/direct 派发，不能把它直接更名为本设计的 single-agent 通用执行能力。
主 Agent 具有多种受限写入 profile，也不等于允许在普通只读对话中自动切换权限。

### 2.2 已复现的路线目录缺口

以下为实施前基线复现。对同一 `prd-task-pipeline-v4-canary.yaml` 分别以 `shadow`、`blocking` 加载：

- `task_pipeline.mode` 与 `profile_digest` 不同；
- `workflow_route_catalog()` 输出及其中 `config_digest` 完全相同；
- 目录摘要当前来自路线投影，不能作为完整有效配置的摘要使用。

2026-10-23 已补 `execution_policy` 与 `flow_topology` 投影，并纳入目录摘要；旧提案须重新预览。
CLI/Web 旧策略批准零 invoke、消费者旧有效配置拒绝的测试已通过，详见实施记录。
基线缺口不代表所有下游批准检查都可被绕过。
建议保留明确的 route-catalog 摘要语义，并将影响执行的策略摘要另行绑定至提案；
迁移时对旧提案重新预览，避免静默改变既有字段含义。

路线投影需要区分 Flow family、真实 Flow topology、lane 数、execution profile、
Task Pipeline mode、策略摘要、支持的验收能力与 rollout 状态。
例如 Issue v4 canary 是 `topology: fanout`、`lanes: 1`；`single_lane` 不等于 light。

## 3. YAML、Flow 和执行策略的关系

`apiVersion` 仍为 `zaofu.dev/v1`；Task Pipeline v4 是 PRD／Issue／Refactor 的执行 profile，
并非整个 YAML 的版本。v3 仍为默认，v4 为显式 canary，不能由模型因“任务复杂”自动启用。

| 路线／模式 | 当前形态 | 路由原则 |
| --- | --- | --- |
| PRD | 默认 fanout，含 Scan、Planner/Critic、Task Map、实施和验收 | 产品功能、多模块需求 |
| Issue | 底层默认 light；显式 fanout 支持更复杂修复，v4 要求 fanout | 缺陷复现、修复与回归 |
| Refactor | 扫描、规划、实施、验证及 parity／gap 收敛 | 重构、迁移、行为等价改造 |
| light | PRD／Issue 的拓扑；单任务、实施、Verify、Judge | 范围清楚的小交付，仍属于 Workflow |
| General | 注册通用入口及受限 `kind: Workflow` 编译路径 | 已注册能力和产物合同；不是任意图生成兜底 |
| Research | fixed 研究路线与 opt-in adaptive pilot | 证据、选型、风险分析；后续交付另行决策 |

light 没有 Planner 生成 `product_acceptance_spec`，当前拒绝
`productAcceptanceMode: blocking`。不得为了选择 light 而静默降低质量要求。
Generic Workflow 也不是任意新配置的容器；当前有效配置只允许一个 safe Generic Workflow
contract，扩展或替换需走现有配置变更准入，不能直接追加第二份合同。

基线根 `zf.yaml` 实际只暴露 `delivery:prd:standard`、`research:fixed`、
`research:adaptive-pilot`。示例文件存在不表示当前项目已启用对应路线。
不同 Flow 可以通过现有多文档／profile 机制配置；同 family 多变体的并存能力需单独验证，
不能假定添加两个同 kind 文档就能无冲突地选路。

## 4. Single Agent 在哪里触发

### 4.1 产品入口

| 入口 | 语义决策者 | 实际启动者 | 限制 |
| --- | --- | --- | --- |
| Kanban／主 Agent 对话 | 主 Agent | 批准后的受控执行入口 | 回答和澄清不强制建 Task；可追踪交付绑定 Task |
| Task 上的执行方式选择 | 主 Agent 提案／用户选择 | Kernel | 展示目标、范围、权限、预算、产物及验证方式 |
| Channel 的已确认产物 | 获授权的主 Agent／leader 路径 | 同一受控执行入口 | 保留 canonical PRD、Owner 权限和来源摘要 |
| CLI／API | 操作者显式选择或请求预览 | 同一 Kernel action | 不由 CLI 私造执行事实 |
| Workflow 故障／恢复页 | Run Manager，必要时调用 Recovery Agent | Kernel | 绑定当前 checkpoint、Task／operation 和旧 attempt |

主 Agent 可根据语义建议执行方式，用户也可明确指定。语义判断考虑：
用户是否要求执行、范围能否被一个责任主体承接、输入是否充分、写入与外部副作用、
依赖数量、是否需要独立验证、预算及当前可用能力。
问题包含“研究”“修复”等文字不直接生成运行请求。

建议产品选项为“自动推荐／单 Agent／辅助协作／Workflow”，不让用户先选 SDK 或传输协议。
“自动推荐”仅允许在已授权范围内选择；缺少权限或关键输入时进入提案／澄清。

### 4.2 默认运行方式

- 对话分析：沿用已有 durable WebChat/headless turn。
- 有界执行、恢复和后台协作：优先结构化 headless Provider adapter；
  要有运行 ID、流式事件、最终结果、取消、超时和会话恢复接口。
- 需要真实终端交互或当前 Provider 只有已验证 tmux 路径时，使用该 transport；
  UI 必须说明需要操作者介入，不能假装是无人值守成功。
- Web 连接断开只影响观看；是否继续运行由 durable operation 策略决定。
  显式取消必须停止已授权的整个执行子树，不能等同于关闭 SSE。
- 权限受既有 profile／controlled action 约束；不得让后台运行依赖不可见的终端确认。

一次可追踪执行关联 Task、operation、attempt、Provider session 和必要的 WebChat turn。
各对象保持现有职责；不能同时让 AgentTurnStore 与 Workflow Operation 独立判定 Task 完成。
运行成功只说明 Provider turn 结束，交付成功还需 Task 合同和对应证据门禁。

### 4.3 两种提交方式：Kanban Agent 与 Channel Group

两种入口复用主 Agent 的语义规划能力及 Kernel action，但不能合并成没有来源区别的
“文本转任务”。是否绑定 Channel 取决于请求所引用的真实产物，而不是当前使用哪个页面。

| 比较项 | Kanban Agent 直接提交 | Channel Group 提交 |
| --- | --- | --- |
| 需求来源 | 当前用户需求链、已有 Task、用户明确指定的文件或 PRD | Channel 中当前版本的 canonical PRD／确认产物 |
| 是否必须先有 PRD | 不必须；明确 Issue、有界任务可直接形成 Task 合同 | PRD 交付路径必须具有当前可用的 canonical PRD 及确认状态 |
| 提交意图 | 用户明确要求创建、跟踪或执行；普通问答不建单 | Owner 明确要求将该产物转成任务／交付；讨论完成不等于执行请求 |
| 规划请求者 | Kanban Agent 基于用户请求提出方案 | 通过当前有效 Leader 权限路径请求规划，再复用 Kanban 主 Agent 能力 |
| 来源绑定 | Task contract、需求来源、来源版本与请求 origin | 额外绑定 channel/thread、Leader 身份及 revision、PRD ref/digest/revision |
| 建单确认 | 受控创建 Task；仅记录时不启动执行 | 确认建单后创建 Task，并保留完整 PRD 来源和批准范围 |
| 启动确认 | 执行提案绑定当前 Task、路线及有效策略 | 除左侧条件外，重新核对 Channel 权限与 PRD 版本 |
| 返回位置 | 原 Kanban 会话和 Task／Run 投影 | 原 Channel/thread 加对应 Task／Run 投影；不由模型临时指定其他目标 |

```text
路径 A：Kanban Agent 直接提交

用户输入 / 指定文件 / 已有 Task
        |
        v
识别意图 -> 澄清或形成需求合同
        |
        +-- 只讨论 --------------------> 回答，不建单
        +-- 只跟踪 --------------------> 确认建 Task，不启动
        |
        v
确认创建 Task / 绑定已有 Task
        |
        +--------------------------------------------+
                                                     |
路径 B：Channel Group 提交                           |
                                                     |
成员讨论 -> canonical PRD -> Owner 确认              |
        |                                            |
        +-- 暂无交付意图 -----------> 保留产物        |
        |                                            |
        v                                            |
Owner 请求提交 -> 当前 Leader 权限与 PRD 版本校验     |
        |                                            |
        v                                            |
复用 Kanban 主 Agent -> Task 提案 -> 确认建单          |
        |                                            |
        +--------------------------------------------+
                                                     |
                                                     v
                                  Workflow Planner / Configurator
                                  执行方式 + 路线 + 配置适配建议
                                                     |
                                  Preview -> 批准绑定 -> Kernel
                                                     |
                                  按源头返回进度、结果或阻断原因
```

上图是逻辑依赖，不要求每一步增加独立页面。初步推荐可包含在建单提案中；
只有 Kernel 创建真实 Task 后，才能绑定其 ID／contract digest 并形成可执行提案。
当前 Channel 提示词已区分 Task 确认与后续 Workflow 选择、启动确认；本设计保留这个边界，
不能把 PRD 确认、建单确认、配置变更批准和启动批准当作同一项授权。
已存在的有效批准可以按精确绑定复用，不额外制造重复确认。

Channel 的完整产品交付沿用 `channel_prd_scope=full_product`；仅当 Owner 明确请求
有界评审、验证或演练时使用现有 `approved_task` 范围。任务“简单”不能成为自动缩减
canonical PRD 验收范围的理由。Kanban 中明确引用同一 Channel PRD 时也必须走相同权限检查；
普通 Kanban 任务不能因为上下文中出现了 Channel PRD 就被自动绑定过去。

PRD revision、Leader revision 或 Task contract 变化后，旧规划结果和提案必须重新校验；
禁止退回无来源的直接提交来绕过过期检查。相关当前入口见
[Channel 权限校验](../../src/zf/runtime/channel_workflow_authority.py) 与
[主 Agent 提案约束](../../src/zf/web/headless_agent.py)。

## 5. 支持三种协作方式，保持一种运行权威

```text
用户输入 / Task / 已确认 Channel 产物
                    |
                    v
            主 Agent 语义推荐
                    |
      +-------------+------------------+
      |             |                  |
 回答/澄清/记录   有界 Agent         Workflow
                    |                  |
            +-------+-------+     PRD / Issue /
            |               |     Refactor / ...
         单执行者       根 Agent + 助手    |
            |               |          |
            +-------+-------+----------+
                    |
          Proposal / 已授权策略准入
                    |
          Kernel 创建并管理执行
                    |
         Provider adapter / transport
                    |
          结构化结果 -> 校验 -> 收敛
```

### 5.1 单执行者

适用于有界分析、产物整理、清楚且可验证的小任务。
软件交付若合同要求独立 Verify，则单执行者提交之后仍交给独立验证步骤，
不能由同一执行者自行证明所有完成条件。

### 5.2 根 Agent 带受限助手

根 Agent 在一个批准的 operation 内自主拆出有限的辅助问题，如检索模块、分析日志、
比较方案。根 Agent 汇总、承担合同责任，并通过唯一根结果通道提交。

- Provider 原生子 Agent 不是额外的 ZaoFu canonical Task；它们有 child lineage 和操作证据。
- 子 Agent 不获得 Kernel 写入权限，不直接发送终态 Task／Workflow 事件。
- 初期仅开放具备强制只读边界的助手；受控写入助手需额外验证隔离工作区和 owned paths。
- 子 Agent 分析结果可作输入，不能代替要求独立性的正式 Verify／Judge。
- root/child 数量、递归深度、时间、费用、权限和取消范围需要统一上限。
- 子 Agent 尚在运行时，根 Agent 的一句最终答复不能自动让整个 operation settled。

产品上应显示“1 个根 Agent + N 个助手”。根 operation 有多个模型参与时，不能标为
“仅一个 Agent”，也不能把一次 native spawn 宣称为完整 Kernel Workflow。

### 5.3 Kernel 管理的 Workflow

当工作需要独立验收、多阶段持久化、跨任务依赖、并行写入或严格交付门禁时，选择 Workflow。
Task Map、任务状态和 happy-path 派发继续归 Kernel。
配置的 `orchestrator` role、入口主 Agent、Recovery Agent 都不能成为第二个状态机。

执行中发现需要升级模式时，产生继续执行提案，说明剩余工作、现有产物、权限和新增成本。
若只是已批准 profile 内的只读助手调用，可直接执行；若改变写入范围、Workflow 拓扑、
验收标准或预算上限，则绑定新方案后重新准入。已在运行的任务不能静默换图。

## 6. Kimi Code 源码 review

### 6.1 审查范围和定位

本次只读检查本机 `/home/min/workspace/kimi-code`，HEAD 见文首，工作树检查时干净。
以下路径相对于该仓库；它们是本地版本证据，不表示公开发行版与此版本相同。
没有运行 Kimi 测试、安装依赖、调用模型或复制其实现。

| 源码位置 | 已确认行为 | 借鉴方式 |
| --- | --- | --- |
| `apps/kimi-code/src/main.ts`；`src/cli/v2/run-v2-print.ts` | print/headless 创建或恢复会话，运行主 Agent，并处理后台任务退出策略 | CLI／Web 共用执行内核，headless 与子 Agent 能力分离 |
| `apps/kimi-code/src/tui/commands/swarm.ts` | 显式 `/swarm` 入口和模式切换 | 用户可显式要求协作；ZaoFu 仍需自己的语义提案和权限准入 |
| `packages/agent-core-v2/src/agent/tools/agent/subagent-task.ts` | 子 Agent 接入任务完成／失败／取消处理 | 子任务可观察，终态不能仅依赖自由文本 |
| `packages/agent-core-v2/src/features/swarm/swarmFeature.ts` | `AgentSwarm` 经 feature 注册到工具入口 | 不是只有库函数；实际工具可触达 |
| `packages/agent-core-v2/src/features/swarm/tools/agent-swarm/agentSwarmTool.ts` | 模型调用工具进入 swarm，支持新建与恢复子 Agent | 自然语言请求可由 Agent 选择工具，不依赖专门 UI |
| `packages/agent-core-v2/src/features/swarm/session/sessionSwarmService.ts` | 聚合执行、取消传播、恢复时校验子 Agent 归属 | 保留 caller／child 身份，不允许跨父执行接管 |
| `packages/agent-core-v2/src/features/swarm/session/agentRunBatch.ts` | 并发约束、速率限制重试和批次结果 | ZaoFu 需要能观察等待、失败、未启动和取消 |
| `packages/agent-core-v2/src/session/subagent/subagentService.ts` | profile allowlist、spawn／fork、父权限模式继承 | 复用分层能力的思路，不照搬权限假设 |
| `packages/agent-core-v2/src/session/subagent/spawn.ts` | fork 与 resume 互斥；fork 继承自身类型和模型，受实验开关约束 | 区分恢复原任务与从历史创建独立分支 |
| `packages/agent-core-v2/src/session/agentLifecycle/profile/profiles.ts` | 主 Agent 提供 Agent／AgentSwarm，coder 默认工具不含这两项；explore 声明 prompt-enforced read-only | 默认限制递归；不能把提示词限制当隔离证明 |

### 6.2 不能直接作为 ZaoFu 安全保证的部分

1. `explore` 工具集合仍包含 Bash，描述明确是 prompt-enforced read-only。
   这不是 OS／工具执行层只读隔离的证明。
2. 普通 spawn 路径复用 caller runtime identity，且继承父 permission mode 和用户工具。
   这条代码不能证明每个 writer 子 Agent 有独立 worktree 或只拥有子集权限。
3. `AgentSwarm` 的 schema 上限为 128 个条目，这是输入上限，不是推荐并发数，
   也不是 ZaoFu 已批准的 token／费用预算。实际启动节流与并发另由 batch 逻辑处理。
4. headless 有 `exit`／`drain`／`steer` 策略；主 turn 结束不必然代表所有后台子任务结束。
   ZaoFu adapter 必须声明并验证终止策略，不能把进程退出码直接等同于完整交付。
5. 存在相关测试文件不表示本次测试已通过。本次仅阅读调用链及部分测试断言。

已检查测试定位包括 `apps/kimi-code/test/cli/run-v2-print.test.ts`、
`packages/agent-core-v2/test/session/subagent/spawn.test.ts` 和
`packages/agent-core-v2/test/features/swarm/sessionSwarm.test.ts`。

### 6.3 接入决策

支持 Codex、Claude Code、Kimi Code 作为目标方向；不增加一个专门常驻 Kimi
orchestrator 来调用另一层 Codex orchestrator。
主 Agent 使用项目批准的 Provider，助手能力由执行 profile 和实测 capability 决定。
Provider 内多模型选择不等于可以原生跨 Codex／Claude／Kimi 的会话系统。

第一版不实现“自动寻找最聪明模型”；先支持显式 Provider 选择与有证据的能力匹配。
若某 Provider 不支持强制权限边界、完整子树取消或可验证结果通道：

- 按已批准 fallback 策略退回普通单 Agent；
- 或提出 Kernel Workflow 方案；
- 无合法 fallback 时明确阻断，不能谎报已经启动辅助协作。

## 7. 执行 profile 与配置变更

### 7.1 当前基础与目标边界

复用已有 `ExecutionProfileConfig` 的 strategy、continuation、collaboration、limits、
capability_policy，以及 Task contract 的 profile ID／digest；不新建第二套 execution schema。
现有 E0 shadow 必须先接到真实 caller、结果和恢复合同，再开放运行效果。

路线选择建议携带以下事实，具体字段以实施时既有合同扩展为准：

- 目标 Task／来源 Requirement 的 revision 与摘要；
- 推荐处理方式、理由、缺失信息；
- Workflow route（适用时）及当前目录摘要；
- 执行 profile 与有效策略摘要、Provider capability 证据；
- 允许的写入路径、工具、外部副作用、预算和产物验收要求；
- 用户指定模式与最终选择的差异、是否需要批准。

这些是可审核提案数据，语义优劣由 Agent／skill 评判；Kernel 校验字段、绑定、
权限上限、预算、状态与证据存在性。

已有路线满足需求时仅绑定运行参数；没有合适能力时才进入 Workflow Synthesis／
workflow-config proposal → validate → approve → apply。配置写入后必须证明消费者
已读取新版本，重新生成路线目录和提案；不能把文件写入成功等同于热加载成功。
本设计不提供可直接粘贴的新增 YAML 字段，避免把 candidate 字段误当已支持配置。

“动态加载”需要分别验收：配置解析／展开、配置变更生效、选定 Flow 激活、
on-demand 角色启动。已有角色按需启动不能证明按难度生成配置的闭环已完成。
当前 [候选配置生成](../../src/zf/runtime/workflow_proposal.py) 除 Generic `Workflow`
外，`4907ab45` 补充了已有 PRD／Issue／Refactor Flow 的受限 lanes 候选，并接入
配置 CAS 和 submit 消费者；light 扩容、v4 只改 lanes 均拒绝。这不能证明全部难度适配
或 Task Map 后运行中重配已完成。以下 7.2–7.5 仍按各分支分别实施和验收。

### 7.2 Workflow Planner／Configurator 的职责

由 Kanban 主 Agent 调用同一专项能力；Channel 提交先通过 4.3 的授权交接。
简单请求可由主 Agent 使用 skill 完成评估；需要深入代码分析时按已授权策略调用有界助手，
不增加另一个常驻 orchestrator，也不让配置 Agent 直接改 canonical `zf.yaml`。

评估输入包括：完整需求与验收、当前代码及测试事实、已有 Task contract、当前路线目录、
有效配置与执行 profile、Provider capability、预算和用户指定约束。
如果有 Task Map 则引用其当前 generation；初评没有 Task Map 时显式标为尚未形成。

| 评估维度 | 证据 | 可提出的适配 |
| --- | --- | --- |
| 工作范围与上下文 | 涉及模块、现有实现、代码量与接口事实 | 单执行者、辅助协作、light 或标准 Workflow |
| 需求确定性 | 未决问题、验收缺口、架构未知 | 澄清、Research、必要扫描或规划 |
| 依赖与可并行性 | 任务依赖、owned paths、共享状态、集成顺序 | 实施并发、任务分组、Worker 池建议 |
| 风险与独立性 | 外部副作用、关键状态、合同要求 | Critic／Verify 责任、验证层次与审批边界 |
| 运行能力 | 工具、Provider 能力、已注册 Skills／profiles | 执行 profile、已准入助手策略、会话方式 |
| 资源约束 | 成本、时间、并发和恢复上限 | 预算、超时和有界恢复策略 |

不在 Kernel 写“出现某关键词就选某路线”或固定难度分数阈值。
Agent 必须解释具体证据与取舍，而不只输出简单／中等／困难。
硬限制仍由 Kernel 执行：只允许已注册能力、有效字段、批准范围、预算上限与证据门禁。
复杂不意味着自动启用 v4，简单也不意味着删除独立验证或降低 blocking 验收。

### 7.3 两阶段适配

**阶段一：需求就绪后初评。**

- Kanban 路径以需求和 Task 合同可规划为前提，不强制生成一份 PRD；
  Channel 路径以当前 canonical PRD、权限与交付意图为前提。
- 建议执行方式、Flow、已有 profile、规划资源与实施资源上限，列出暂缺证据。
- 不把尚无 Task Map 支撑的并发估计当成确定结论。
- 完成批准绑定后冻结本次有效配置，进入选定流程；标准 Workflow 可先执行扫描和规划。

**阶段二：Task Map 形成后执行准备复核。**

- 对真实依赖、写入冲突、任务粒度、验收责任与 Worker 容量进行评审。
- 原批准范围和配置能够承接时继续，不强制增加配置变更或人工确认。
- 需要改变冻结策略、权限、预算、拓扑或验收时，返回受控修订／重规划，
  保留已完成规划产物并按现有规则创建新 revision／generation。
- 运行中的 Workflow 不允许通过重新 classify、直接修改 `lanes` 或换 YAML 静默切图。

light 不经过 Planner／完整 Task Map 规划链，不能依赖阶段二补齐被省略的语义规划。
若初评证据不足以证明任务适合 light，先澄清或选择带规划阶段的有效路线。

### 7.4 适配产物与配置应用顺序

评估结果以已有 Proposal／artifact 机制承载，扩展所需语义字段，不增加第二 Task schema。
持久化内容至少包括：

1. 输入绑定：Task／Requirement、来源 ref/digest/revision、Channel 权限（适用时）、
   配置／目录／profile 摘要和已有 Task Map generation。
2. 评估结论：范围、依赖、风险、验证成本、未知项、推荐理由和未选方案的原因。
3. 执行建议：方式、route、profile、角色／Skills、容量、预算、权限与验收要求。
4. 配置效果：复用现有配置、仅绑定允许的运行参数，或需要修改 canonical 配置；
   变更路径必须提供候选、差异、影响范围及回退依据。
5. 验证与批准：编译／preflight 结果、待澄清项、适用批准及失效条件。

```text
Kanban 需求就绪 / Channel PRD ready + 有效交付请求
                          |
                          v
               Agent 初评与执行方式推荐
                          |
                 Workflow 配置适配
                          |
          +---------------+----------------+
          |                                |
    已有 profile 足够                 能力／配置有缺口
          |                                |
    绑定允许的参数                    候选配置 + diff
          |                                |
          |                         编译 / preflight
          |                                |
          |                         配置批准 / 受控 apply
          |                                |
          |                         确认消费者配置版本
          |                                |
          |                         重建目录与启动提案
          +---------------+----------------+
                          |
               启动批准绑定 / 冻结有效配置
                          |
               workflow-start / 按需激活
                          |
             标准流程：Scan / Plan -> Task Map
                          |
               Agent 执行准备复核
                          |
          +---------------+----------------+
          |                                |
       配置适用                      需要改变冻结策略
          |                                |
       继续实施                      受控修订 / 重规划
```

复用配置的运行不能反复重写项目 `zf.yaml`。配置变更时应重新检查基线摘要，
防止两个 Task 的配置提案相互覆盖；已有 Run 保留原有效配置绑定。
新配置在实际消费者生效后才允许基于它派发；需重启时显式记录并走受控恢复，
不得假定任意字段都支持热加载。

### 7.5 结果回传与失败处理

两条入口都展示推荐原因、最终有效配置、验证状态和未满足条件，但沿原 origin 回传：
Kanban 返回原会话／Task，Channel 返回绑定的 Channel/thread 与对应 Task。
模型生成未知角色、非法拓扑或不可用 profile 时，编译器拒绝并保留诊断；
缺少批准或能力时保持待处理，不能转成不受控 direct 派发。
已提交／运行中的请求复用原身份与受控恢复／重规划入口，不通过重新提交另一条入口重复启动。

## 8. Workflow 异常时如何接手

```text
Worker / Provider / Kernel 故障
                |
                v
Supervisor 记录事实；Kernel 故障由外部进程管理恢复
                |
                v
Run Manager 核对账本、checkpoint、工作区与副作用
                |
       +--------+----------+-------------------+
       |                   |                   |
  确定性重试         Recovery Agent         重新规划 / 等待
  恢复原会话         诊断或接续一个阶段      Owner / 预算 / 输入
       |                   |                   |
       +-------------------+-------------------+
                           |
                Kernel 准入并取得执行权
                           |
          结构化结果 -> 原 Verify / Judge -> 后续阶段
```

### 8.1 故障分流

| 场景 | 行为 |
| --- | --- |
| 会话／进程丢失，合同仍有效 | 恢复原 Provider 会话，或新会话读取 State Packet 接续 |
| 实施任务中断 | 保留 Task 和 operation，Kernel 准入新 attempt；保留其他已完成任务 |
| 环境准备失败 | 有界修复环境，重试受影响阶段；不伪造产品验证结果 |
| 验证发现语义缺陷 | 受控返工；保留失败证据，按现有 generation 规则执行 |
| 结果可能已执行但未记账 | 核对副作用和幂等记录，未知期间禁止盲目重放 |
| 计划、范围或验收要求变化 | 新计划／generation，重新绑定批准和任务依赖 |
| 用户主动暂停／取消 | 不自动恢复；区分 Provider 意外停止与用户意图 |
| 预算或恢复次数耗尽 | 明确终态／阻断原因，禁止无限交给下一个 Agent 重试 |

终态 Run 不能由 Agent 直接改回 running。按照既有受控 reopen／replan 路径处理；
不允许 reopen 的终态创建有关联的新 Run，保留原结果与因果关系。

### 8.2 恢复包

扩展既有 State Packet 和 immutable sidecar，绑定：

1. Task、Run、operation、attempt、计划 generation、最后可信事件和 checkpoint。
2. canonical contract、有效配置／profile 摘要、批准范围、剩余预算。
3. base／HEAD、工作目录、dirty diff 快照、已验证产物及摘要。
4. 未完成步骤、阻断原因、失败测试、已完成但尚未核对的外部副作用。
5. Provider／session／子 Agent lineage、能力证据、原执行者存活状态。
6. 恢复允许动作、必须重验项、结果提交入口及返回阶段。

大语义体原子持久化，事件只记录 refs／digests；不把 transcript 当完成事实。
跨 Provider 接续使用新会话和规范化恢复包，不假设原生 session 可以跨产品导入。

### 8.3 执行权与旧结果

- 恢复前确认旧执行者已退出或隔离；旧租约失效不等于旧进程不再写文件。
- 无法确认停止时，禁止在同一可写工作区启动替代 writer；保留现场并隔离工作区。
- 通过现有 attempt／lease／generation 校验拒绝迟到结果，未验证旧产物不得直接采纳。
- 重复恢复请求使用同一幂等身份，只能产生一个有效恢复效果。
- 子 Agent 不直接提交根结果；Provider 原生协作不能绕过父 operation 的取消与预算。

### 8.4 一人接手与串行化的边界

接手一个失败阶段是本设计第一阶段目标。若剩余 Task 需要由一个实施 worker 顺序处理，
仍保留 Task DAG、独立 Verify 和逐任务状态，不把所有任务合并成一段自由聊天。
并发容量、配置或拓扑已被冻结时，不能直接改运行中的 `lanes` 或 `zf.yaml`；
需要现有受控重配置／新 generation 的明确支持与批准。

## 9. Provider 与协作能力准入

| 层次 | 首期要求 | 开放条件 |
| --- | --- | --- |
| 普通 headless root | start、stream、terminal、cancel、timeout、受限权限 | mock 合同与隔离真实任务通过 |
| native resume | 精确会话 ID、配置匹配、工作区核对 | 原会话存在／丢失两条路径都验证 |
| native delegation | child lineage、根结果唯一、强制权限、深度／并发上限 | 实际运行子树并验证所有边界 |
| delegation recovery | 停止子树、拒绝旧结果、未完成孩子恢复／重建策略 | root 崩溃与 child 残留故障注入通过 |
| cross-provider recovery | 新会话加载规范化恢复包 | 代码、验收和执行权不因换 Provider 丢失 |

`BackendCapabilities.native_multi_agent` 等默认值当前保持保守；Codex 的
`provider_session.max_parallel_agents` 已有支持，不代表上表所有条件都已验证。
先验证实际安装版本和选定 adapter，再更新能力声明。

Kimi 接入应优先使用已验证的结构化 SDK／headless 边界；若选择 Node SDK 或服务协议，
以薄 adapter 映射到 ZaoFu Provider 合同，不引入其内部 Session／Agent store 管理 ZaoFu Task。
root 操作的归属、取消和状态仍由 ZaoFu 决定；Kimi 内部事件作为 Provider operation 证据保存。
Provider 升级导致能力或权限语义变化时重新签收，不只更新版本号基线。

## 10. 分期实施候选与验收

本表是设计拆分，不是完成清单。2026-10-23 用户已授权实施并建立八项 backlog；
01/04 的准入绑定已落代码，其余包含部分实施与未实现项，逐项状态以任务和实施记录为准。

| 阶段 | 工作 | step -> verify |
| --- | --- | --- |
| A | 补齐 route／profile／策略摘要绑定 | 切换 v4 shadow／blocking、权限或关键执行策略 -> verify：旧提案失效，展示有效差异 |
| B | 主 Agent 处理方式选择与两类入口汇合 | Kanban 直接需求与 Channel PRD 提交 -> verify：不误建单、PRD ready 不自动运行、两条路径保留来源及权限；建单与启动批准分离 |
| B1 | 需求难度评估与初始配置适配 | 不同范围、依赖、风险、预算及验收要求 -> verify：推荐有证据，只选有效能力；复用路径不改 YAML，变更路径提供可校验 diff |
| B2 | Task Map 后执行准备复核 | 任务图与初评假设不一致 -> verify：原配置可承接时继续，需要改冻结策略时受控修订；不在运行中静默切图 |
| B3 | 双入口来源与配置失效验证 | PRD／Leader／Task／配置版本变化、并发配置提案和重复提交 -> verify：旧绑定被拒，配置不互相覆盖，消费者生效后才派发，结果回到原入口 |
| C | 单 Agent 受控执行 | Kanban、Task、CLI/API 进入同一动作 -> verify：绑定 Task／operation，重复请求不重复执行，结果按合同验收 |
| D | 单阶段恢复 | 杀死 worker／丢失 session -> verify：保留已完成任务，创建合法 attempt，独立验证后解锁后续 |
| E | 受限辅助协作 | root 调用只读助手 -> verify：权限强制、预算累加、根结果唯一、子树取消、后台任务收敛 |
| F | Kimi adapter canary | root headless 后逐步启用 delegation -> verify：结构化结果与生命周期合同通过，权限不满足时拒绝开放 |
| G | 跨 Provider／串行接续 | Codex ↔ Claude；Kimi 准入后再加入 -> verify：来源证据、工作区、依赖和独立验收完整保留 |

最低故障矩阵：

- Provider 进程退出，Kernel 重启，网络断开但 Provider 仍在运行。
- 已写文件／已完成外部动作但尚未记录结果；重复恢复命令。
- 旧 attempt 晚到，旧 child 仍在运行，root 已结束但后台任务未完成。
- session 丢失，Provider 配置变化，权限收紧，Task／计划版本过期。
- Verify 失败，环境修复失败，用户取消，预算耗尽，恢复次数耗尽。
- 不支持 native delegation 的 Provider 请求协作：明确 fallback 或拒绝。

确定性测试覆盖状态、事件、Store、caller 和幂等合同；涉及恢复／配置／编排的实现需运行
impact closure、pre-merge sentinel 和相关 mock E2E。真实 Provider 单列验证：
隔离 state、工作区及进程，保存版本／模型／输入／原始结果／副作用证据并清理。
不把 mock 通过、进程退出码为 0 或模型自然语言声明作为真实交付成功。

## 11. 外部参考与适用边界

以下官方资料于本次讨论检索，具体协议仍须与本机版本核对；借鉴设计，不要求引入新引擎。

- [LangGraph](https://github.com/langchain-ai/langgraph) 与 [interrupt 恢复规则](https://docs.langchain.com/oss/python/langgraph/interrupts)：
  恢复可从节点开头重跑，副作用必须可安全重复或拆分到独立边界。
- [Temporal](https://github.com/temporalio/temporal) 与 [Activity 幂等说明](https://docs.temporal.io/activity-definition)：
  结果未记账时可能重试；使用副作用服务认可的幂等键，不能承诺任意外部动作 exactly-once。
- [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) 与 [官方编排说明](https://developers.openai.com/api/docs/guides/agents/orchestration)：
  bounded specialist-as-tool 适合保持外层责任，不等于转移 Kernel 状态所有权。
- [Codex App Server](https://learn.chatgpt.com/docs/app-server)：
  thread resume／fork 和 turn interrupt 可作为 Provider adapter 能力，不能替代任务账本恢复。
- [Claude Agent SDK sessions](https://code.claude.com/docs/en/agent-sdk/sessions) 与 [checkpoint 限制](https://code.claude.com/docs/en/checkpointing)：
  会话与文件系统状态分别处理，不能把 resume／fork 当 worktree 快照。

相关本地记录：[184 Intake 路由](../impl/184-intake-semantic-routing.md)、
[184 真实验证](../impl/184-live-validation.md)、
[当前调度与 v4 边界](../manual/13-plan-task-map-orchestrator-dispatch.md)。
184 已有真实验证未完成完整交付闭环；本设计不将已有路由验证升级为恢复／协作验收证明。

## 12. 最初设计交付与后续实施

最初交付为本设计及索引登记；复核了 ZaoFu 与 Kimi 的指定源码和调用入口，
沿用前一轮只读 YAML 加载与目录差异复现，以及官方资料检索。
文档提交前检查本地链接、索引归属和 diff 空白错误。
最初设计轮未修改运行代码、配置或 Kimi 仓库；未启动 Provider、运行 Kimi／ZaoFu 产品测试，
未验证 native delegation 的强制权限或跨 Provider 真实恢复。

2026-10-23 实施轮已修改路线策略绑定、配置消费者准入与双入口交接，运行定向回归和隔离真实任务。
ready 且历史仅规划的 Channel PRD 可在精确启动提案中以 `authorize_channel_execution: true`
请求另一次 Owner 执行授权；必须匹配当前来源与授权主体，不改写 PRD readiness 或产品非目标。
该能力不等于 unresolved PRD 自动豁免，也不代表通用 single/delegated 或 Kimi 已完成。
完整变化、失败和未覆盖分支见 [184/185 路由准入实施与逐分支验证](../impl/185-routing-admission-validation.md)。

同日续批增加 `bounded-agent-v1` 有界工件入口、Task Map 评审输入绑定、恢复前进程终止确认，
以及显式 `kimi-headless-canary` 无工具文本入口。single 的源码交付及 native delegation
未因此开放；Kimi 的真实权限与生命周期签收仍需本机可运行环境。实现边界与本批验证见
[续批记录](../impl/185-bounded-execution-and-recovery.md)。
