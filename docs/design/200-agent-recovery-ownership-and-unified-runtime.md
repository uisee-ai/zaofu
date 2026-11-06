# 200 - Agent 恢复职责与统一执行 Runtime

> 状态: candidate
>
> 日期: 2026-11-06（UTC）。承接 199 的架构讨论与代码复核。
> 演进基线为 `zf-prd-v5.yaml`；本文补充 v5 的恢复交接、职责边界和共同执行合同，不表示已实现、部署或通过真实运行验收。
> 保留 [199](199-worker-failure-recovery-and-role-handoff.md) 的原始证据、R/A/B/C 编号和验收场景，不改写其正文。

实施跟踪：本设计已于 2026-11-06 获准在 199 worktree 实施，见 [200 实施记录](../impl/200-agent-recovery-implementation.md)。
下文“当前实现”“待实施”描述设计复核时点；实现与验证结论以该记录逐项证据为准，不因授权自动改为 implemented。

## 1. 目标与设计决定

让 Agent 能处理项目相关、开放式和未知问题，同时让长程执行具有可恢复的身份、权限、状态与交接。
本设计在 v5 已有的 WRC 正常调度、blocking Task Pipeline、独立 Verify 和串行集成之上补齐恢复闭环，不重新建设调度体系。
本设计沿用 [AGENTS.md](../../AGENTS.md) 的核心规则：

> Agents judge business meaning, applicability, and action plans; the runtime
> validates the authorization, identity, version, and evidence bindings of
> those decisions and executes state transitions.

1. **OA 按已启用检查点负责目标约束下的执行与恢复编排。** 包括修复策略、专业能力委派、成果复用和局部重规划；不限于 PRD 或合同矛盾。正常派发仍归 WRC，不为每次派发、工具调用或普通重试新增 OA 审批。
2. **RM Agent 按需承担运行诊断与修复任务。** 可以使用获准工具验证假设，在明确修复身份下直接操作，或委派专业修复 Worker；不能只用观察或建议代替交付。
3. **WRC 与 RM runtime 收敛已有执行合同及重复入口。** 复用准入、operation 生命周期、容量、lease、派发、恢复与结果消费；保留工作流推进、恢复跟踪和独立监督职责，不把所有正常续作移入 RM 队列。
4. **每个当前恢复步骤只有一个决策负责人。** 多角色可协作提供证据，运行恢复与任务策略可以分成关联步骤；不让 OA 和 RM Agent 对同一个决定重复审批。
5. **恢复必须证明效果和交接。** 请求发出、operation 完成、故障消除、恢复交付完成和 Task 完成分别核验。

“已知／未知”不是唯一分界：已知的数据库错误仍可能需要项目判断；未知故障也可以由 runtime 可靠记录、派发诊断并跟踪期限。
runtime 不硬编码项目排障方法、业务验收或最佳修复方案；Agent 不直接修改 Kernel 管理的 canonical 状态。

范围仅为 ZaoFu 通用能力、角色方法和验证合同。LS／LS-v1 继续作为证据来源或获准验证样例，不在本文派发其业务修复。

## 2. 当前实现与目标能力分开记录

### 2.1 v5 演进基线与实际运行配置

本设计以注册 PRD 控制器 [zf-prd-v5.yaml](../../zf-prd-v5.yaml) 为演进模板；实际加载版本为 `5.0.1`。
具体运行仍以受控装配、批准并应用后的项目 `zf.yaml` 及该 Run 已接纳的配置快照为准，模板不是第二控制平面，不能直接替换活跃 Run 的配置。
装配入口见[注册控制器实施说明](../impl/registered-controller-assembly.md)，配置合同见 [test_prd_v5_config](../../tests/test_prd_v5_config.py)。

| 项目 | v5 实际加载结果 | 对本文的约束 |
| --- | --- | --- |
| 正常前沿 | `normal_frontier_owner: wrc` | 已有唯一正常调度 owner；不按“尚需将正常调度交给 WRC”拆任务 |
| Task Pipeline | `prd-flow-v4-task-pipeline`、`mode: blocking` | v5 是控制器版本，内部 profile 名含 v4；不能据此判断 v5 未启用 Pipeline |
| 执行与验证容量 | Impl 8、Verify 8、最多 16 个活动 Task Pipeline | 保留独立池、容量和背压，不引入 Agent 逐次审批槽位 |
| 集成与最终验证 | 串行 CAS 集成、integration capacity 1、rolling smoke、`frozen_exact_commit` | 保护已有版本绑定、独立 Verify 与候选冻结，不另造集成调度器 |
| PRD 的 OA 策略 | `semantic_control`；`pre_impl: shadow`，`semantic_failure: blocking`；`review_blocked_tasks: true` | 实施前检查不升级为强制模型门；语义失败在已启用检查点按需介入 |
| OA 自主动作 | `delegate / continue / rework` | 不据角色描述扩大权限；合同变更、重规划和其他动作仍走既有准入 |
| RM Agent | resident agent 已启用、独立会话；同步 reflect 默认未启用 | 复用现有诊断／修复入口；不能把未启用 reflect 的慢调用直接认定为 v5 当前阻塞根因 |
| 证据与可选质量门 | Artifact Package、产品验收及语义提交为 blocking；`verification.contract.quality_required: false` | 保留正式证据门；ContractQualityD 的启发式问题不是 v5 已启用质量门缺陷 |

仓库根 `zf.yaml` 仍是 PRD v3 兼容默认入口，不作为本设计的演进基线。v5 PRD 的配置也不能直接外推为 Issue、Refactor、Research 或 legacy 的已启用策略。
配置加载证明展开后的策略，不证明某个部署进程已经加载它；运行验收还需核对 controller、配置摘要、Run、代码部署和实际消费者。

### 2.2 已有执行机制与目标缺口

以下为当前工作区代码及测试源码的静态核查，不把测试定义视为本次执行通过；涉及 v5 主路径、共享组件或兼容路径时分别标明。

| 已核实事实 | 代码／测试定位 | 对设计的约束 |
| --- | --- | --- |
| WRC 正常前沿 facade 已复用 Task Pipeline reconciler，并阻止 legacy 同时取得派发权 | [wrc_frontier_consumer](../../src/zf/runtime/wrc_frontier_consumer.py)、[workflow_frontier_owner](../../src/zf/runtime/workflow_frontier_owner.py) | 当前已有 owner fence，不应把现状概括为两个完全独立且无保护的 scheduler |
| RM 有独立 observe/decide/act/verify tick，以及多类受控动作分发 | [run_manager](../../src/zf/runtime/run_manager.py) | 需要审查并收敛共同机制；不是把所有 RM 代码复制进 WRC 类 |
| 在线 pane_dead／lease_expired／provider_stop 由 RM runtime 按既定合同协调恢复，WRC 校验回执后执行 redrive | [task_pipeline_recovery](../../src/zf/runtime/task_pipeline_recovery.py)、[fault matrix 测试](../../tests/test_task_pipeline_fault_matrix.py) | 迁移前保留当前责任与恢复资格；这不表示先调用 RM Agent，也不能绕过回执直接重派 |
| 首次 Impl 失败评审经 RM pending action 进入 OA，动作可标记 auto_decide | [execution_review](../../src/zf/runtime/orchestrator_agent_execution_review.py) | 经 RM runtime 不等于先调用 RM 模型；本入口主要处理已 settled 的当前 Impl |
| 验收评审的 replan 可以由 Kernel 直接请求 OA | [task_pipeline_acceptance](../../src/zf/runtime/task_pipeline_acceptance.py) | 不能强制所有失败或 OA 检查点串行通过 RM 队列 |
| OA 已有针对确切目标的返工决策，v5 复用 Task Pipeline 的现有消费者 | [decision_apply](../../src/zf/runtime/orchestrator_agent_decision_apply.py)、[task_pipeline_oa_rework](../../src/zf/runtime/task_pipeline_oa_rework.py) | 保留受控返工；不能用 legacy assign 重启已终结或阻塞的 operation |
| OA 的合同内获准返工可不重跑 Planner/Critic | [execution policy 测试](../../tests/test_orchestrator_agent_execution_policy.py)、[OA rework 测试](../../tests/test_task_pipeline_oa_rework.py) | 调整执行方法与修改合同分别处理，动作必须仍在授权范围内 |
| OA 委派中的角色偏好是软偏好；WRC 执行 capability、依赖和容量约束 | [orchestrator_agent_briefing](../../src/zf/runtime/orchestrator_agent_briefing.py) | OA 不能凭语义偏好直接抢占实例或改变 DAG |
| RM Agent 当前默认不直接修改业务／harness 代码，收到明确 repair task 后可作为修复 Worker 执行 | [run_manager_resident](../../src/zf/runtime/run_manager_resident.py) | 扩展运行修复能力需绑定角色、工具、scope 和验证合同，不能仅更改角色描述 |
| 案卷级 active_effect 会阻止后续同案卷动作；case ID 默认随 generation／target 改变 | [recovery_case](../../src/zf/runtime/recovery_case.py)、[对应测试](../../tests/test_recovery_case.py) | 后续交接和跨版本问题关联需补合同，见 §9 |

关联设计：[195 的 Worker 执行合同](195-oa-wrc-worker-runtime-borrowings.md)、[196 的人工决定](196-inbox-human-decision-center.md)、[198 的输入与恢复上下文](198-context-index-write-read-resume.md)。
通用运行中求助、完整统一执行底座及所有故障的无监工闭环仍是目标能力。

## 3. 职责划分：决策、执行、验证与兜底

| 角色／模块 | 当前步骤应承担的责任 | 不应承担的责任 |
| --- | --- | --- |
| Worker／Fix | 合同内实现、自检、局部修正；提交有效产物或准确失败／求助事实 | 自行改合同、扩大权限、伪造已读或宣布独立验收通过 |
| OA | 决定任务如何继续：修复方向、能力委派偏好、成果复用、验证范围、必要计划变更 | 直接操作 canonical Store、管理 lease，或逐次审批所有工具错误 |
| RM Agent／运行诊断角色 | 分析环境、Provider、控制面及 harness 问题；调用获准工具诊断和执行修复，或提出有范围的修复委派 | 重复决定产品范围、替代 OA 批准业务验收变化，或只观察而不交付本请求 |
| Planner | 根据获准方向生成局部计划、Task Map 和合同差量及依赖理由 | 自行批准变更，或用新任务 ID 清除旧失败历史 |
| WRC／统一执行 runtime | 机械准入、容量、派发、operation／attempt／lease、阶段推进、对账和幂等 | 硬编码数据库排障方案、项目语义或最佳执行策略 |
| RM runtime 的恢复生命周期职责 | 跟踪需恢复协调的案卷、请求、责任人、期限、消费者与效果；逐步复用统一执行入口 | 成为额外模型审批层，或要求所有正常续作先进入 RM 队列 |
| Verify／配置的评审角色 | 针对确切目标独立检查证据，区分产品、测试和环境问题 | 因修复者自报成功而通过，或悄悄删减验收 |
| Supervisor／watchdog | 发现无承接者、无应答、超期和无进展，触发受控接管 | 替代业务验收，或依赖被监督的慢调用才能运行 |
| 操作员／需求负责人 | 提供确需人的业务决定、凭据、外部资源或必要授权 | 代替日常 runtime 反复手动发送“继续” |

“环境修复者”“测试维护者”等是可由现有获准 Worker 承担的职责，不要求新增常驻 Agent。
RM runtime 与 WRC 都属于确定性 runtime；RM Agent 与 OA 才承担模型判断。`owner_route=run_manager` 可以表示协调入口，不能自动解释为必须调用 RM 模型。

每个当前步骤记录决策责任、实际执行者、验证责任和超时接管方，复用已有 operation／案卷及其关联工件。
请求发出不释放交接义务：接收方实际接手或形成合法等待前，原交接必须继续被跟踪；原 Agent 崩溃时由 runtime 保留义务。
环境修复与任务策略可以是同一问题下的不同步骤，分别有 owner 和结果；同一个决定不得由两个 Agent 无依据地重复分诊。

## 4. 统一执行底座的目标形态

本节是在 v5 已有单一正常前沿 owner 上收敛共同执行合同，不假定 WRC 与 RM 各自运行一套无保护的正常调度器。
图中的统一表示准入和生命周期合同一致，不要求合并所有循环、队列、进程或模块。

```text
                   Agent decision / execution methods
        +----------------------+-----------------------+
        |                      |                       |
        v                      v                       v
       OA                  RM Agent                Worker / Fix
 Task / recovery       Runtime diagnosis         Implementation
 strategy / delegation Environment repair        Local correction
        |                      |                       |
        +----------------------+-----------------------+
                               |
                    Typed intent / tool request
                               |
                               v
 +-------------------------------------------------------------+
 |                Unified execution runtime                    |
 |                                                             |
 | Admission / identity / permissions / currentness             |
 |                         |                                   |
 | Operation / attempt / lease / capacity / dispatch             |
 |                         |                                   |
 | Result admission / reconciliation / recovery tracking        |
 |                                                             |
 | Workflow progression       Recovery lifecycle               |
 | Internal responsibility    Internal responsibility           |
 +-------------------------+-----------------------------------+
                           |
                           v
             Provider / tools / controlled actions
                           |
                           v
              Evidence / independent verification
                           |
                  Continue / valid scoped wait

 Supervisor: observes outstanding obligations and execution
 Host supervision: recovers runtime lifecycle; respects stop intent
```

### 4.1 整合什么

- 各入口复用同一套身份、版本、权限、scope、幂等和执行前检查。Agent 建议、周期对账、操作员 resume 不能各有一套不同准入。
- 正常执行与恢复工作复用 operation／attempt、容量与 lease；每个执行对象只有一个有效执行者，恢复任务也需有调度机会。
- 事件唤醒和有界周期对账共同消费当前状态，结果接纳、迟到隔离及交接效果使用一致合同。
- 工作流推进与恢复案卷跟踪保留独立职责模块。本文不要求新增 canonical Store、替换 EventLog，或把所有代码合入一个大文件。

### 4.2 迁移约束

当前 owner fence、历史事件、operation-v2 与 legacy/shadow 资格继续有效；新入口不能成为第三个 scheduler。
先补齐最小恢复闭环所需的共同准入，再按实际副作用入口迁移请求和执行生命周期，最后退役被替代入口；不能在活跃 Run 中直接改 owner 配置完成迁移。
v5 的 WRC owner、Impl／Verify 池、独立验证、背压和串行集成作为保留基础；RM 协调恢复不等于接管正常派发。
历史案卷、等待、暂停、取消及既有成果须可重放；迁移与回退都必须验证同一 operation 不会被两个入口同时执行。
慢模型／工具调用异步运行，协调循环不持锁等待模型。统一执行权不意味着监督与执行必须处于同一个会阻塞的循环。
宿主监管只恢复服务生命周期，不直接修改业务状态；安装／启用监管属于单独部署资格，本文不承诺已经完成。

### 4.3 恢复工作的容量与调度保证

v5 已有 Impl／Verify 独立池、活动 Pipeline 上限、背压及返工排序，见 [task_pipeline_reconciler](../../src/zf/runtime/task_pipeline_reconciler.py)。
本节补恢复工作与这些资源的交互合同，不认定当前必然发生资源死锁，也不新增调度器或固定预留槽位。

| 场景 | 必须满足的约束 | 实施时须明确的选择 |
| --- | --- | --- |
| Worker 等待只读指导 | 保留任务、attempt 和等待依据；指导本身不启动第二写入者 | 哪些资源必须保留、哪些可以在安全检查点释放；不能仅把状态改成 waiting 就假定容量已释放 |
| 执行池满载，恢复需要修复 Worker | 获准恢复必须有可证明的执行机会，不能要求故障任务先成功才能释放恢复资源 | 优先复用现有队列排序和安全释放机制；只有不足时才设计显式恢复容量，并记录配置、权限及公平性 |
| 修复完成，Verify 池或集成队列拥塞 | 独立验证和集成依旧执行，不以恢复紧急为由跳门 | 修复验证与普通验证的排队规则、背压解除依据和饥饿检测 |
| 多个 Run 或连续恢复竞争资源 | 恢复不能无限占用正常任务容量；暂停、预算、角色及工作区约束仍有效 | 调度规则来自获准配置；Agent 可提出策略，runtime 执行已接纳的排序、容量及租约控制 |

验收须覆盖满载、等待求助、修复及独立 Verify 同时出现的场景，并记录实际派发和资源释放证据；仅看到恢复请求入队不算通过。
具体队列算法、容量参数和 backend 差异在实施文档确定。不得以本节为由扩大总容量、修改既有金额限额，或新增统一的累计执行时长限制。

### 4.4 执行入口迁移与退役记录

每次入口收敛先填写以下映射，再实施；表中列的是设计要求，不表示新入口、迁移或退役已经完成。

| 现有入口／职责 | 复用方向 | 切换与退役证据 | 回退边界 |
| --- | --- | --- | --- |
| WRC 正常前沿及 Task Pipeline | 保留现有 reconciler、owner fence、attempt 和集成合同 | 只退役已证明重复且无剩余消费者的分支；正常派发不增加 RM／OA 审批 | 不通过改活跃 generation 的 owner 回退 |
| RM pending action、恢复建议及操作员恢复 | 收敛到既有受控动作／operation 的共同准入，具体入口逐项核实 | 同一目标并发请求只产生一次有效执行；历史请求、权限及回执仍可解释 | 停止新路径接纳后先对账在途动作；旧入口不能盲目重放外部副作用 |
| 已知故障的 redrive | 保留恢复资格、回执及当前版本检查，共享去重和结果消费 | 重启、重复通知、迟到回执和暂停反例通过后才移除旧分支 | 未完成动作保留原身份；旧版本无法解释时显式等待或受控向前修复 |
| Legacy／其他 workflow 兼容入口 | 保留其明确适用范围，与 v5 主路径隔离 | 查清配置、CLI、Web、重放及测试消费者后才退役 | v5 切换成功不能证明所有兼容入口都可删除 |

实施记录至少包含旧入口、新的复用入口、适用 controller／配置、切换条件、在途操作处置、退役条件及回退证据。
发现重复副作用、越权、旧结果误接纳或违反暂停意图时停止扩展迁移；正常任务饥饿等性能判据按预先冻结的 canary 条件评估。
回退不删除历史事件、案卷或成果，也不自动撤销外部已发生的动作；无法证明兼容时保留明确阻塞和责任人。

## 5. 失败路由：依据待解决的问题选择负责人

Worker 上报事实，或由 Supervisor 发现未交付结果。Kernel 先核验身份、运行意图、现有结果及可能已发生的副作用，再按注册合同路由。
机械错误码可以确定性选择已批准动作；根因、业务含义与修复方案由 Agent 判断。错误文本相同不证明根因或恢复路线相同。

```text
Worker failure / help request / missing result
                       |
                       v
            Kernel fact admission + reconciliation
                       |
       +---------------+----------------+------------------+
       |               |                |                  |
       v               v                v                  v
 Local correction  Known mechanical  Execution strategy  Runtime diagnosis
 in contract       recovery policy   decision required   / repair required
       |               |                |                  |
       v               v                v                  v
 Worker / Fix     Runtime executor      OA              RM Agent
       |               |                |                  |
       |               |         Planner if needed   Tools / repair Worker
       |               |                |                  |
       +---------------+----------------+------------------+
                       |
              Admitted action / result
                       |
             Effect check + next handoff
```

第一条路径可在当前授权 attempt 中继续，不为每次参数或语法错误创建新 Task。
已识别的任务策略问题可以直接请求 OA，不增加 RM Agent 前置审批；未知运行问题请求 RM Agent／诊断角色。
证据不足时保持原因未知并指定诊断负责人，不默认归为产品失败；诊断后允许携带案卷与证据移交 OA，或由 OA 请求运行专家。
普通实现修复无需重规划。只有实质改变合同、依赖、验收责任或交付承诺时，才请求 Planner 差量及必要批准。

### 5.1 共享故障的关联、修复与逐任务恢复

多个 Worker 同时报告数据库、Provider 或环境错误时，既不能默认分别启动重复修复，也不能凭错误文本相同合并为一个根因。
当前 [recovery_case_id_from_payload](../../src/zf/runtime/recovery_case.py)包含任务／阶段等作用域；本节提出共享协调要求，不声称当前 Case 已实现跨任务故障聚合。

- Agent 根据已授权的资源身份、环境版本、时间窗口和故障证据提出关联；结构化已知资源故障可按明确策略关联。文本相似只作线索。
- Runtime 核验关联对象、授权与版本，通过既有案卷／工件引用保留各任务的问题身份；不合并权限、验收责任或失败预算。
- 对同一获准修复步骤及共享资源保持一个有效写入执行者；已有修复在途时，其他任务关联并等待结果，不重复启动同一副作用。互不冲突的诊断读取可并行。
- 修复者失效时先对账资源和副作用，按当前资格交接；不能因为新 Worker 接手就再次执行未知结果的安装、迁移或重启。
- 共享资源探针通过仅证明该资源恢复。各任务仍按自己的合同、输入和代码版本执行相关检查，再分别接续；不得批量伪造 Task／Verify 通过。
- 某任务仍失败、资源版本变化或关联假设被推翻时，保留旧证据并重新判断影响范围。关联跨 Run／项目时必须分别具有访问与动作授权。

示例验收：八个 Worker 依赖同一测试数据库，关联成立后只执行一次获准修复；数据库恢复后七个任务检查通过、一个因 schema 不兼容仍失败，后者不得被共同关闭。

## 6. Worker 生命周期问题与处理责任

本表是目标责任覆盖表，不是新增事件类型清单，也不表示所有入口已经实现。
分类至少包含发生阶段、现象、原因判断及可信程度、run/task/operation/attempt、版本、证据和副作用状态；混合问题保留各自义务。
“兜底”表示首选路径失败后的接管方向，不表示每次都必须升级或保证自动修好。

| # | 问题 | 首要处理与执行 | 验证条件／兜底 |
| --- | --- | --- | --- |
| 1 | 满足条件却长期未派发 | WRC 对账 ready 集合、容量、依赖与角色占用 | Supervisor 检测饥饿；未知调度原因交 RM Agent 诊断 |
| 2 | Provider／角色进程启动失败 | runtime 执行获准启动策略；RM Agent 诊断配置、认证或宿主问题 | 实际承接当前请求；缺凭据／宿主能力交操作员 |
| 3 | 断连、限流、暂时服务错误或进程退出 | 先核对结果／副作用，再按策略退避、隔离旧 attempt 并恢复 | 当前阶段有效进展；持续故障交诊断或明确外部等待 |
| 4 | 进程活着但当前请求无响应 | runtime 核对接收、期限、lease 和结果，受控接管 | Supervisor/watchdog 触发；空闲且无请求不能误报 |
| 5 | 有心跳或反复读页，却无有效推进 | RM Agent 检查上下文、工具及运行问题；执行方法问题转 OA | 有绑定本 operation 的进展证据；正常长调用不能仅因耗时被杀 |
| 6 | 工具参数、命令语法、局部调用错误 | 当前 Worker 依据反馈在合同内修正 | 原调用及必要自检通过；工具适配缺陷交 harness 修复者 |
| 7 | 数据库、网络、依赖、端口、磁盘等环境故障 | RM Agent 调用工具诊断，直接承担获准修复任务或委派修复 Worker | 原环境探针及应继续的检查通过；缺外部资源交操作员 |
| 8 | 输入缺失、ref 失效、工件损坏或来源不可访问 | 输入生产者／工件 owner 恢复固定版本或发布合法修订 | 输入被重新准入；需求本身缺失交 OA／需求负责人 |
| 9 | 会话丢失、压缩后上下文不足、恢复位置不明 | runtime 恢复持久化身份与输入；Worker 按 198 补齐必要正文 | 当前版本和恢复边界可证明；恢复工具故障交 RM Agent |
| 10 | Skill、工具、profile、简报与有效配置不一致 | 装配 owner 修复交付；方法问题由对应角色方法 owner 处理 | 实际交付版本及行为可验证；不能原地篡改旧简报 |
| 11 | 输出格式、字段、证据引用不合格 | Kernel 返回准确拒绝原因，原生产者定向补交 | 正确产物被准入并交下游；无人消费由 runtime／Supervisor 接管 |
| 12 | 编译失败、代码缺陷、功能未完成 | Impl／Fix 在合同内修复 | 确切提交独立 Verify；需要新策略或反复无进展时交 OA |
| 13 | 测试错误、flaky test、验证器故障 | 测试／验证工具维护者诊断修复并独立复核 | 不删减验收制造通过；测试与有效 AC 冲突交 OA |
| 14 | 需求、PRD、AC、测试语义矛盾 | OA 对账有效要求，Planner／生产者形成必要修订 | 版本和决定可追溯；无法确定的业务选择交需求负责人 |
| 15 | 拆分错误、循环依赖、关键前提不成立 | OA 选择执行策略，Planner 提出局部计划差量 | 经准入重新绑定受影响任务；无关工作不重跑 |
| 16 | allowed_paths／权限内无法履行合同 | OA 判断拆分或授权需求，提出受控调整 | 合法授权与合同生效；Worker 不自行扩大范围 |
| 17 | 凭据、人工批准、外部接口或业务决定待到位 | 指定外部 owner；runtime 保存作用域、唤醒条件与复查安排 | 真实决定／输入校验后续作；超时不自动放行 |
| 18 | 命中当前有效预算、调用或资源限额 | runtime 执行有效策略；Agent 可提出授权内替代方法 | 需要调整时交授权负责人；不按产品失败盲目返工、不新增全局上限 |
| 19 | 越权、错误工作区、身份／lease／代次不匹配 | Kernel 拒绝或隔离，保护当前成果；RM Agent 诊断来源 | 当前身份与权限恢复后才执行，不沿用失效授权 |
| 20 | 并行写冲突、candidate HEAD CAS 冲突、合并失败 | 集成／工作区 owner 受控整合；RM Agent 可协助排障 | 实际集成目标重新验证；模块边界冲突交 OA |
| 21 | Verify 时目标代码或环境发生漂移 | 验证环境／工作区 owner 隔离目标，失效相关证据 | 固定提交及可解释环境重验，旧证据不算通过 |
| 22 | 外部操作可能成功但响应丢失 | 副作用 owner 核对持久化身份与外部状态；必要时 RM Agent 诊断 | 确认后续作；无法核对保持 unknown，不盲重试 |
| 23 | 实现成功但结果提交、TaskRef、通知或交接失败 | WRC／交接 owner 对账已有成果，仅补失败步骤 | 下游实际接手；不默认重做 Impl，无法机械恢复时请求诊断 |
| 24 | 重复结果、旧 attempt 或旧计划回执迟到 | Kernel 去重并隔离过期绑定 | 仅准入当前有效结果；身份不明确时保留未知 |
| 25 | 暂停、取消、停止与在途执行竞态 | Kernel/WRC 按持久化意图隔离执行、对账副作用 | 不擅自重启；获准恢复后重新核验 lease 和版本 |
| 26 | EventLog、Store、sidecar 部分写入或损坏 | 对应存储 owner 执行受控恢复；RM Agent／harness 修复者排障 | 保留原始证据和可证明恢复边界；未知事实阻止相关准入 |
| 27 | 同一问题跨任务或计划版本反复出现 | 当前问题负责人检查已有尝试、成果与问题关联 | 运行原因交 RM Agent，执行策略交 OA；不能换 ID 清零 |
| 28 | 原因未知或报告互相矛盾 | 一个明确诊断负责人收集证据、验证假设，再移交对应 owner | runtime 跟踪期限；获准深诊断或明确缺失信息与外部 owner |

不能要求先读完不存在的来源，才能报告“输入不可获取”；准入输入故障不授予任务完成或放宽合同的权限。
同样的 `pytest failed` 可以是产品断言、数据库环境、测试工具或验收矛盾，不能仅由命令名决定责任。

### 6.1 恢复角色自身失败的接管

| 故障 | 接管方 | 必须保留的边界 |
| --- | --- | --- |
| Fix／Verify Worker 崩溃 | runtime 按策略恢复；必要时 RM Agent 诊断 | 保留适用成果并重验目标，不默认重跑全计划 |
| OA 不回应或产物持续不可准入 | runtime 跟踪请求，Supervisor/watchdog 触发受控恢复 | 超时不能视为批准；语义决定仍需有效负责人 |
| RM Agent／诊断角色不回应 | runtime + Supervisor/watchdog | 正常流程和无需新判断的恢复继续；诊断义务移交或明确等待 |
| RM runtime／WRC 慢调用阻塞 | 与被监督调用独立的监督路径 | 不依赖失效 Agent 自救；模型和工具异步执行 |
| 整个控制面进程失效 | 已配置的宿主监管 | 尊重 stop/pause/cancel，恢复唯一有效实例，不写业务 Store |
| 自动策略耗尽或缺少合法恢复条件 | 指定操作员／外部 owner | 保留问题、证据、已尝试方法和下一步，不无限重复同一指令 |

## 7. 数据库依赖与连接故障：Agent 处理，Runtime 承载

```text
Worker: database connection failed
                  |
                  v
Runtime: persist identity, evidence and recovery obligation
                  |
                  v
RM Agent: inspect config / logs / service / network / schema
                  |
        Hypothesis -> tools -> evidence
                  |
       +----------+------------------------------+
       |                                         |
       v                                         v
Repair within authorization             Task strategy must change
       |                                         |
       v                                         v
RM Agent in repair task / repair Worker           OA
       |                                fixture? defer? dependency?
       |                                         |
       |                               Planner if contract changes
       +-------------------+---------------------+
                           |
                Kernel admits action / result
                           |
           Environment probe + original failed check
                           |
                    Resume original work
```

RM Agent 通过日志、配置、连通性和服务状态判断 DNS、认证、服务启动、schema 或应用配置问题；获准工具执行读探针和必要修复。
runtime 可以执行已批准的“启动隔离测试数据库”动作，但不能仅凭 `connection refused` 自行决定修改 Compose、安装依赖或切换数据库。
OA 判断真实数据库是否为当前阶段硬依赖、合同是否允许 fixture、能否先完成其他独立工作；若改变合同或 DAG，交 Planner 正常修订。
RM Agent 不自行降低真实集成要求；OA 不必亲自承担全部环境排障。已有授权可直接履行，不新增每次工具调用的人工审批。
涉及缺失凭据、外部服务权限或超出授权的副作用时保留明确 owner 和等待，不虚构环境已恢复。

## 8. Worker 运行中求助与失败后返工

### 8.1 当前能力边界

当前 [pending_execution_reviews](../../src/zf/runtime/orchestrator_agent_execution_review.py) 跳过有活动 operation 的任务，主要消费当前已 settled 的失败／阻塞 Impl。
[OA execution policy](../../src/zf/runtime/orchestrator_agent_execution_policy.py) 与 [v5 复用的 Task Pipeline OA rework 消费者](../../src/zf/runtime/task_pipeline_oa_rework.py)也检查活动后继、当前 attempt、计划版本和决定是否已应用。
这些实现支持失败后的受控返工，不证明已具备通用的“Worker 执行中向 OA 求助”协议。

### 8.2 目标求助合同

- 局部可修复错误在当前合同内处理。无法选择下一步时提交绑定当前 attempt 的问题、失败证据、已尝试方法、已有成果和待回答问题；不因每个工具错误终结 Task。
- runtime 复用现有请求／operation 和 sidecar 保存责任、接收方、输入版本、预期输出、期限及超时出口；如确实不能表达再扩展合同，不新增第二份 Task schema。
- OA 接收执行策略问题，RM Agent 接收运行诊断／修复问题。混合问题拆成关联步骤，携带证据移交，不能先让两个模型各自从头诊断。
- 只读指导可以回到仍有效的 attempt，不自动启动第二个写入者。采用建议前核对 Worker 是否已自行解决、目标是否变化、回答是否仍适用。
- 重派、改合同或切换执行需要受控交接：对账现有结果及副作用，在允许的检查点处理旧 attempt，固定新的目标和授权后再派发。
- 请求方等待指导时仍有可恢复身份与等待依据；OA/RM Agent 超时由 runtime 接管请求，不默认批准、无限等待或无依据替换执行。

建议、暂停与新 attempt 是不同操作，不把一句自然语言“换个方法”直接解释为取消、扩权或重派。
目标对象的实际状态仍由 canonical owner 管理，Agent 对话或 UI 文本不能推进状态。

### 8.3 恢复请求与结果的最小合同

以下是逻辑信息项及现有承载位置，不是新增 schema，也不表示各入口已统一实现全部字段。
优先复用 [WorkflowOperationService](../../src/zf/runtime/workflow_operation.py)、[RecoveryCase](../../src/zf/runtime/recovery_case.py)、既有受控动作和 sidecar；只为无法表达的缺口提出兼容扩展。

| 信息项 | 已有载体／字段示例 | 必须表达的约束与待补交接 |
| --- | --- | --- |
| 问题与来源 | `recovery_case_id`、`workflow_run_id`、`task_id`、来源事件及 causation | 明确恢复哪个问题、谁提出请求；缺失绑定不能从普通聊天文本猜测，非任务问题声明合法资源／Run 作用域 |
| 执行身份 | `operation_id`、`parent_operation_id`、`active_attempt_id`、`lease_id`、`role_instance` | 区分原任务 attempt 与恢复步骤 attempt；重派沿用因果，不用新 ID 重置真实失败历史 |
| 输入与目标版本 | `request_ref`／`request_hash`、`task_map_generation`、Plan package ref/digest、确切目标提交 | 输入变更须形成合法修订；环境相关修复还须声明其资源及可核验版本／观察条件，不能仅绑定代码提交 |
| 责任与授权 | 已配置角色、受控动作 policy、合同 scope、获准决定工件 | 单步判断 owner、执行者、验证者及超时接管方明确；Agent 提案不授予自身额外权限，角色接手须有接纳依据 |
| 结果与交付类型 | 既有结果 ref、语义裁决及 sidecar 正文 | 区分指导、诊断、修复产物、验证结果和合法等待；结果接纳不自动等于故障消除，unknown 保留缺失证据 |
| 效果与下游承诺 | 既有动作 `effect_expected_events`／`effect_required_sequence` 及证据引用 | 事件名出现不足以证明效果；须绑定目标、版本和适用裁决，记录谁消费结果、如何确认接手 |
| 等待、计量与失败出口 | 既有 lease、期限、重试／预算政策及案卷事件 | 明确等待范围、复查条件、接管出口和计数类别；不默认批准、不无限重派，也不新增统一执行时限 |

实施切片必须给出“信息项 → 当前字段／工件 → 生产者 → 准入函数 → 消费者 → 缺失处理”的映射。
字段缺失时先判断它对当前操作是否必需；必要身份、授权和证据缺失阻止相关副作用，并由 runtime 保留诊断义务。
完整语义正文放入原子写入的 sidecar，事件保留合同要求的裁决、引用和因果；不得由 Agent 直接修改 canonical Store。

## 9. 案卷、恢复步骤与跨版本问题关联

### 9.1 未结案不能阻止合法的下一步骤

当前 [recovery_case](../../src/zf/runtime/recovery_case.py) 将 applied／diagnosing／verifying 视为 active，并阻止同案卷的新动作。
此前纯内存复核观察到：分诊回执无 effect 条件可使案卷 resolved；补上 effect 条件后，同案卷后续动作又会被 active_effect 拒绝。
这不是线上死锁的完整证明，但说明仅修改提前结案分支不足以闭合恢复。

目标合同应区分“重复执行当前步骤”和“合法进入下一步骤”：

| 对象 | 应表达的含义 |
| --- | --- |
| RecoveryCase | 原故障、关联证据、当前恢复责任和跨步骤历史；不等同一次派发 |
| WorkflowOperation／continuation | 本次诊断、修复、效果检查或交接，绑定前置结果与目标 |
| attempt／lease | 本步骤的有效执行身份；重复、旧代次和迟到结果被隔离 |
| 效果与交接证据 | 原故障如何消除、承诺的下游是否接手，或是否形成新的合法等待 |

沿用既有事件、operation 与案卷投影，明确前一步结果准入、责任释放、下一步资格及并发保护的对应关系。
同案卷合法 continuation 可继续，重复动作仍拒绝；等待仅在指定唤醒证据经校验后重新取得执行资格。
不能删除 active_effect 防护或增加一个动作名特例来绕过整个案卷准入。
结案审查覆盖所有路径：除 triage 回执外，`task_map.ready`、子任务完成或诊断完成本身也不必然证明原故障已消除。

```text
Case open
   -> Diagnosis operation completed       [case still open]
   -> Repair operation completed          [case still open]
   -> Fault-specific effect verified      [fault eliminated]
   -> Promised handoff / valid scoped wait [recovery delivery complete]
   -> Remaining Task acceptance gates      [Task completion is separate]
```

代码修复需确切提交的独立 Verify；环境恢复需原探针及应继续的检查；纯交接恢复需正确版本产物被下游接纳。
故障消除可以先记录，恢复交付仍需核对承诺的交接。新的独立故障另行关联，不能用普通进展事件直接冒充结案。

### 9.2 版本身份与问题延续分别表达

当前 case ID 包含 task、合同／计划版本和 target 等信息；[现有测试](../../tests/test_recovery_case.py)明确要求 generation 或目标提交变化时改变 case ID。
因此，“跨计划版本保留失败历史”不能只依赖当前 case ID，也不能直接从其身份计算中删除版本字段。

- 当前执行案卷继续绑定确切版本，用于准入、证据适用性、去重和迟到隔离。
- 通过现有案卷／工件关联记录哪些新案卷延续同一问题，支持累计真实失败、查看已尝试策略和发现无进展。
- 同一问题的语义关联由有证据的 Agent 判断提出，Kernel 校验引用、权限、版本和一致性后持久化；单纯错误文本相同不足以合并。
- 历史失败可作为判断依据，不自动授予旧授权或使旧通过证据适用于新提交；真正独立的新问题保持独立。
- 传输、协议、环境和语义失败分别计数，operation generation 只承担身份作用；规则变化、计划修订及故障复发保留因果，不靠换 ID 重置历史。

### 9.3 步骤推进、责任释放与迟到处理

下表描述业务里程碑，不新增 canonical 状态枚举。实施时映射到现有 operation、案卷投影、事件和受控动作；语义结论由对应 Agent／验证产生，状态变化由 runtime 接纳后执行。

| 当前里程碑 | 允许推进的依据 | 责任与执行资格 | 此时仍不能宣称 |
| --- | --- | --- | --- |
| 请求已接纳 | 身份、目标、授权和所需输入有效，接收责任已确定 | Runtime 保存交接义务；接收方实际承接或形成合法等待前不丢失原请求责任 | 请求已执行或问题已解决 |
| 诊断结果已接纳 | 当前目标的诊断依据有效，下一步骤及负责人明确 | 可以结束诊断 operation 并准入后继修复；Case 保持未结案，不让 case 级 active 状态无差别阻断合法后继 | 原故障已消除 |
| 修复结果已接纳 | 获准产物／动作结果可核验，副作用状态已记录 | 完成旧写入者的停止／结算对账后转交验证；不能仅靠 Agent 自报释放 lease 并启动第二写入者 | 原失败场景已恢复 |
| 效果验证通过 | 原故障对应的检查通过，独立验证或明确机械谓词的证据绑定仍有效 | 记录故障效果成立，保留尚未完成的下游交接义务 | 下游已经承接或产品已验收 |
| 下游交接完成 | 指定消费者接纳当前结果并实际承接，或进入有 owner、范围及唤醒条件的合法等待 | 关闭本次承诺的交接；尚有产品验收或其他故障时由对应对象继续跟踪 | 整个 Task／Run 必然完成 |

推进以当前步骤、版本和既有结果为前置条件，并保持幂等；重复确认不能再次执行动作。接收确认丢失时优先重放／对账现有承接事实，不能重新修复。
取消、暂停或合同变更到达时，由 runtime 隔离不再适用的执行资格；在途副作用不能假定已撤销。迟到结果可保留为诊断或副作用对账证据，但未经重新准入不能推进当前目标。
超时只触发声明的复查／接管，不视为默认成功。原 owner 崩溃时，runtime 保留义务并执行获准接管；无法安全交接时形成有证据的等待。

### 9.4 验证证据的适用与失效矩阵

每份证据应说明验收声明、验证方法／验证者、目标版本、结果与限制。Agent 判断方法是否足够及变化的语义影响；runtime 核验声明的绑定、撤销及有效条件。

| 变化或验证对象 | 必须绑定／核对什么 | 复用与失效边界 |
| --- | --- | --- |
| 代码、候选或依赖产物变化 | 确切提交／产物 digest、原合同与消费义务 | 不把旧目标的 passed 改写成新目标的通过；适用历史材料可引用，新的放行仍需当前目标的准入依据 |
| 需求、Task Map 或验收修订 | 正式需求／合同版本、受影响任务和验收声明 | Agent 提出影响范围，runtime 按获准修订失效相应绑定；无关成果可保留，不默认全量重跑或全部沿用 |
| 数据库、服务、配置或测试环境变化 | 资源身份、环境版本／观察条件和原失败探针 | 连通性只证明连通性；fixture 不能替代合同要求的真实集成。证据时效由适用合同声明，不设置通用固定 TTL |
| 修复同时修改测试或验证方法 | 验收条件、测试／方法版本、修改理由及独立评审依据 | 修复者不能通过删断言、换环境或降低标准自行获得通过；方法变化后的充分性需独立审查 |
| 权限、scope、角色或预算授权变化 | 当前有效授权及其对象、范围、版本 | 历史成功证据不继承旧执行权限；新副作用仍走当前授权，不让 Agent 自行豁免 |
| 共享资源修复影响多个任务 | 共同修复证据加各任务自己的输入、代码和验收绑定 | 资源恢复证据可共享，各任务的通过、失败和等待分别判定，不能批量闭合 |

证据缺失、冲突或无法确认适用性时保留 unknown，阻止依赖该结论的放行并指定处理责任；不把不确定自动归为产品失败，也不阻断无关合法工作。
报告存在、schema 正确、事件齐全和退出码为零只证明对应机械事实；产品充分性仍由已声明行为验证与独立评审承担。

## 10. 方法、协议与实现归属

| 内容 | 优先归属 |
| --- | --- |
| 数据库／环境排障、证据假设、开放式修复方法 | RM Agent／专业修复角色的 skills、prompt 和工具能力 |
| 执行策略、修复范围、专业能力委派、成果复用、需求与依赖判断 | OA／Planner 的方法及语义工件 |
| 局部实现、自检、准确错误反馈、何时提出求助 | Worker／Fix 方法 |
| 请求身份、权限、状态迁移、幂等、scope、租约、当前版本、持久化与派发 | 确定性 runtime 和现有 Store／Event／artifact owner |
| 修复行为是否满足目标、证据是否适用 | 独立 Verify／评审方法；runtime 校验绑定和完整性 |
| 无响应、无承接者、超期及恢复链活性 | Supervisor/watchdog 和有界对账；宿主故障由外部监管处理 |

RM Agent 获得修复能力需要实际装配工具、权限、输入和输出合同，不能仅写“你负责修复”。
升级 skill 也必须核对实际交付版本与行为；读过方法不等于正确执行。
统一 runtime 不消除既有受控动作、外部效果及安全边界，不给 Agent 增加直接写状态或默认修改生产环境的权限。

## 11. 实施切片与迁移顺序

以下是后续实施建议，不是本次已执行或已批准的 backlog 批次。199 的 R/A/B/C 保留为追踪编号，不机械拆成同数量开发任务。
按 v5 的真实恢复缺口排序；共同准入先服务于最小闭环，跨入口全面收敛随后进行。Legacy 缺陷与未启用门不混入 v5 主路径完成度。
第一批闭环实施前须明确 §8.3 最小合同、§9.3 转换与交接、§4.3 满载恢复策略和 §9.4 证据适用性；§5.1 共享协调与 §4.4 迁移记录按切片补齐，不要求先实现全系统聚合或全面迁移。
200 保留设计约束和验收示例；字段兼容扩展、具体函数改动、队列参数及任务拆分进入后续 `docs/impl/` 或获准任务，不在本文新增一套运行态定义。

| 顺序／切片 | 交付与依赖 | 必须证明的效果 |
| --- | --- | --- |
| 1. 恢复步骤、效果与交接闭环 | 明确单步 owner、continuation、暂停／权限／隔离及效果条件；接通请求、派发、诊断／修复、独立验证和下游，覆盖 A01/A02/A05 | 案卷未结束时合法下一步可执行，重复和失效动作被拒绝；关闭监工后无需手动“继续” |
| 2. 未知故障与 OA／RM 责任交接 | 基于首个闭环接入 v5 可达的未知故障；保留 unknown、缺失证据和诊断 owner，携带结果移交；关联跨版本失败历史 | 不用字符串补造根因，不重复分诊、不丢失责任；已有配置的恢复上限仍生效 |
| 3. 运行中求助与 attempt 交接 | 扩展已有 settled 后评审；绑定当前 attempt，区分只读指导、重派和合同变更；纳入中断、副作用及已有结果对账，关联 R02/R03、B01–B04 | 指导不产生第二写入者，旧回答不可应用，成果不无故重跑，外部副作用不重复 |
| 4. v5 硬约束执行核验 | 对实际声明的步骤、路径、工具、权限与预算逐字段核对 backend、hook 和受控动作消费者，复用既有门 | 提示、快照和硬限制分别表达；支持的约束能拒绝，不支持的能力不静默宣称已强制 |
| 5. 共同执行入口逐项收敛 | 在已验证闭环上逐入口收敛 Agent 建议、pending action、WRC 与操作员 resume，退役重复机制，保留兼容读取与 owner fence | 不接管正常前沿、不新增调度器；同一 operation 只有一个有效执行者，暂停和越权负例仍拒绝 |
| 专项／兼容治理 | Legacy writer 依赖、未启用 ContractQualityD 及其他 workflow 单独列项；C03 存储一致性和宿主监管单独验证；方法完善后开展规模资格验证 | 不把兼容路径缺陷或 mock 通过计作 v5 生产恢复结论；存储、宿主、规模各有独立证据 |

每个切片先对当前待部署版本复现，区分已修复、已有局部能力与真实缺口；不重做已有 fault matrix、reader 恢复或 dry-run 防护。
统一机制可以分阶段收敛，不把整个控制面重写作为修复队列问题的前提；共同安全合同必须先于新增自动执行。
保留 R01 的提交／合入／部署／进程加载／真实效果证据和 R15 的干净环境交付；未验证的临时措施不能悄悄成为长期依赖。

## 12. 验收与故障注入

沿用 199 的全部相关验收，本节补充职责收敛和统一执行的检查。场景均待实施／执行，本次文档更新不构成通过证据。

| step | verify: check |
| --- | --- |
| 从获准 v5 配置启动并核对实际 Run 快照 | WRC 持有正常前沿，Pipeline 为 blocking，PRD OA 检查点与批准配置一致；兼容根配置不混入基线 |
| 执行正常 Impl／Verify／集成并注入一次语义失败 | 正常派发不增加 OA 审批；已启用 semantic_failure 进入受控评审，保留独立 Verify、串行集成与冻结提交 |
| 同一工具调用分别注入参数错误、环境故障和合同矛盾 | 路由按证据与待决事项区分；不因命令名或错误文本相同统一重试 |
| Worker 在合同内修复普通错误 | 原 attempt 可正常交付，不为每个工具错误创建 OA/RM 模型审批或新 Task |
| 命中已批准机械恢复策略，RM Agent 不可用 | runtime 仍能执行该策略；不能扩大授权或跳过原有恢复资格 |
| Worker 反复修复无进展但合同未变 | OA 可提出有依据的目标化修复，不强制先改 PRD 或完整重跑 Planner/Critic |
| 在两个不同测试项目注入数据库配置／依赖错误 | RM Agent 根据实际材料使用获准工具或委派修复，探针证明恢复；runtime 无项目专属修复分支 |
| 数据库短期不可用，分别配置允许和不允许 fixture 的合同 | OA 按合同作不同决定，必要变更走正常准入；RM Agent 不擅自切换验收环境 |
| 执行中的 Worker 求助，分别返回只读指导、重派要求和过期回答 | 指导不自动创建第二写入者；重派先完成受控交接；过期回答不能应用 |
| Agent 建议、pending action 与操作员 resume 同时处理同一目标 | 共同准入效果等价，仅一次有效执行；来源差异不扩大权限 |
| 分诊 operation 完成，随后进入同案卷修复步骤 | 不提前结案；合法 continuation 可派发，重复旧步骤仍被隔离 |
| 同案卷收到普通 task_map.ready／子任务完成，而原故障未消除 | 普通进展不关闭故障；绑定原故障的效果与交接通过后才报告恢复交付完成 |
| 同一问题跨计划版本出现，再加入根因不同但错误文本相同的故障 | 保留前者失败关联，后者独立；旧授权及旧通过证据不自动继承 |
| 诊断处理器崩溃，或修复副作用成功后丢失回执 | 恢复处理器未完成步骤；外部结果先核对；不能重跑原 Worker 或盲目重复副作用 |
| 一个任务等待人工决定，另一个具有独立合法动作 | 独立工作可推进；真实 run 级暂停继续阻断其作用域 |
| OA／RM Agent 不回应，另有正常任务与已准入恢复 | 请求被跟踪并有实际接管或合法等待；无关工作继续，不仅产生告警 |
| 阻塞模型／工具，再单独阻塞或终止整个控制面 | 慢调用不阻断监督；宿主层恢复后仅一个有效实例，不能由同循环 watchdog 自证 |
| 新旧执行入口迁移、恢复与重复通知并发 | owner fence 保持，历史结果仍可解释，同一 operation 不被双重执行 |
| 暂停／取消后退出进程，再触发自动监管 | 不擅自重启任务；后续获准恢复仍核验版本、lease 与既有副作用 |
| 关闭监工 session 后完成最小故障恢复 | 测试驱动器只注入和采证，不手动继续、重派或改状态；原故障消除且下游接手或合法等待 |
| 恢复请求分别缺少必要身份、授权、输入版本或效果依据 | 只阻止需要该信息的副作用；保留可诊断请求与 owner，不从聊天猜测绑定或自批例外 |
| 诊断、修复、效果验证、交接逐步完成，并丢失一次接收确认 | 各里程碑按 §9.3 推进；重放只补交接，不提前结案、不重复修复、不启动第二写入者 |
| Impl／Verify 满载，部分 Worker 等待指导，恢复需要修复及独立 Verify | 记录资源占用和实际派发，恢复可推进且正常合法任务不被无限饿死；不靠扩容、越过预算或跳验证通过 |
| 八个任务因同一资源故障阻塞，修复后一个任务仍不兼容 | 关联与授权有依据，同一修复步骤仅一个有效写入者；共同探针不替代各任务检查，失败任务保持未关闭 |
| 修复在途发生取消或合同变化，随后旧结果迟到 | 隔离旧执行资格，核对可能已发生的副作用；旧结果可审计但不能推进新目标或重复动作 |
| 修复后改变代码、环境或测试断言，并尝试沿用旧通过证据 | 按 §9.4 核验目标与方法适用性；不改写旧 verdict，不由修复者自行降低验收；无关有效材料仍可复用 |
| 收敛执行入口后触发回退，同时存在在途 operation 和迟到通知 | 按 §4.4 保留原身份及效果证据；新旧入口不双重执行，无法兼容时明确等待，不删除账本或盲重放副作用 |

每项补齐实际 runtime/CLI/Web 调用入口与调用方测试。先通用隔离 fixture、影响范围测试和确定性 mock E2E，再运行 premerge sentinel；真实 Provider、宿主监管及数据库操作分别按显式资格层验证。
真实测试不得改生产项目环境；仿真状态和服务遵守 AGENTS.md 的临时环境规则。未跑的层级明确保留，mock 通过不升级为真实无人值守保证。

## 13. 指标与交付判据

- 分别记录请求建立、接收、实际派发、应答、修复、效果验证和下游接手时间；检测延迟与进展期限按有效配置及实测基线确定。
- 记录同一决定重复模型分诊次数、无当前 owner 的请求数、无依据重派、有效成果重复执行、重复副作用和违反暂停意图的自动启动。
- 记录 RM Agent 实际工具／修复交付与 OA 决策的准入、应用、拒绝原因；建议数量和 token 输出不能证明有效恢复。
- 记录迁移前后各入口的准入一致性、监督延迟、队列等待和实际交付；不承诺未经测量的提速或费用收益，也不新增项目总量／费用上限。
- 自动策略耗尽仍保留可检查的责任与出口；真实人工决定不算自主修复成功，无依据等待也不能冒充合法外部等待。

每个 canary 预先冻结配置期限、测量窗口、允许的等待和通过条件；采集执行次数、目标版本、事件及工件引用。
只有共同安全合同、实际调用链、对应效果、恢复交接和部署版本均有证据，才能签收对应切片；本文整体状态不随文档写完自动变为 implemented。

## 14. 与 199 的对照及本次交付范围

| 199 位置 | 本文补充 |
| --- | --- |
| §3 权威与职责、§7 角色交接 | §3–§4：OA 执行恢复编排、RM Agent 诊断并修复、runtime 共同机制及渐进迁移 |
| §4 恢复机制、§5 失败分类 | §5–§7：多入口路由、28 类生命周期问题、恢复者失败兜底、数据库完整示例 |
| §6 修复说明、A02、B04 | §9：案卷未结案时的合法 continuation、效果与交接、跨版本问题关联 |
| §9 上下文、A01、B05 | §8：当前 settled 入口与目标运行中求助合同，固定输入和过期回答处理 |
| §13 无监工推进、A05 | §4、§6.1、§12：独立监督、异步执行与宿主资格 |
| §14–§16 实施、验收、指标 | §11–§13：按可独立验证的闭环拆批，保持现有证据要求 |

本文补充目标设计，不改变当前代码权限或运行配置；发生解释冲突时，实际执行继续遵守 AGENTS.md、已准入合同与当前实现。
本文以 v5 为演进基线，§2 标明已有能力，§4 限定共同机制收敛范围，§11 与 §15 按实际路径组织缺口和验收。
199 正文保持不变。未修改 runtime、skills、任务状态或部署，没有启动真实恢复或 Provider 验证。

## 15. 双向边界复盘：判断越界与控制缺口

### 15.1 分类、证据与生效范围

本节依据 2026-11-06 对 `/home/min/workspace/public/zaofu` 的源码调用链、实际配置加载和前序内存探针复核。
完整模块及约束盘点见[本地复盘报告](../../reports/2026-11-06-1303-agent-runtime-boundary-review.md)。报告保存在忽略目录，其中根配置观察不替代本文 v5 基线；本节同时给出源码定位。
“已复现”指函数行为已复现并核对相关调用，不表示已经复现完整工作流事故，也不表示下述修复已实施。

| 方向 | 边界问题 | 应采用的修正方式 |
| --- | --- | --- |
| 本应代码／控制层强制执行 → 当前主要靠 Agent 遵守 | 把权限、次数、跨执行者放行等硬约束降为提示词，或尚未接入执行入口 | 明确硬／软性质，补机械执行、拒绝行为和证据；不能只增加一条 Agent 指令 |
| 本应 Agent 判断 → 当前由代码直接决定 | 用状态、自由文本、长度或项目经验替代根因、适用性、计划和质量判断 | 语义判断归 Agent／skill；runtime 接纳裁决并按权限、版本和证据执行 |
| Agent 判断与 runtime 执行之间缺少交接 | 方法要求已经存在，但判断产物没有成为机械放行的有效依据 | 复用既有合同、工件、事件和受控动作，补完整消费链；不新增第二份 canonical Task schema |

Agent 作出决定、Agent 调用工具、runtime 执行动作是不同责任。Agent 使用受控工具并不意味着控制权转移给 Agent；
runtime 执行外部命令也不意味着它应选择项目修复策略。判断归属不能仅按文件是否位于 `runtime/` 或操作结果是否可预测划分。

本节以 §2.1 的 v5 blocking Pipeline 和 WRC owner 为基线。问题分别标为 v5 已启用路径、共享组件待核对实际消费者、legacy 路径或未启用能力。
函数探针证明某分支行为，不能独自证明 v5 Run 会经过该分支；实施前须补事件生产者、准入入口、消费者和控制效果。
正常前沿 owner 是 runtime 所有权配置，不表示每次派发都先调用 Agent；不能把兼容根配置的 v3／RM owner 当成 v5 现状。

### 15.2 本应代码强制执行，当前降为提示或控制未落实

| 项目 | 应由代码承担的责任 | 当前证据与边界 | 后续处理 |
| --- | --- | --- | --- |
| 角色 `constraints.max_steps` | 若声明为硬上限，应按明确的计数口径限制执行，并产生停止／超限结果 | 字段消费链目前只见配置读取、[提示注入](../../src/zf/runtime/injection.py)和[策略快照](../../src/zf/core/workflow/runner_policy.py)；未找到该字段的计数和拦截消费者 | 明确 step 含义、适用 backend、恢复后如何累计；接入硬限制，或明确降为方法建议。其他预算、超时及 `max_turns` 控制不因此被否定 |
| 角色 `constraints.blocked_paths` | 对声明的禁写范围进行机械拒绝，不能依赖 Agent 自觉 | 当前用于提示词、[配置允许／禁止冲突检查](../../src/zf/core/config/tool_closure.py)及快照；未找到该字段的运行时写入拦截 | 明确路径匹配及优先级并接入适用写入口。现有 Task scope hook 是另一套控制，不能自动当作此字段已执行的证明 |
| Codex 角色 `allowed_tools` | 若对外声明为工具权限边界，应由 provider 或受控工具入口拒绝未授权调用 | [tool closure](../../src/zf/core/config/tool_closure.py)明确说明 Codex 不支持该工具白名单并跳过相关形状检查；提示词仍可展示工具列表 | 原生白名单未落实已确认；其他入口是否提供等价约束仍待核实。不能承诺仅凭字段存在就获得硬限制，也不能据此否定已有受控动作授权 |
| 跨 Agent 依赖的裁决交接 | 将被接纳的依赖判断绑定当前合同、产物及结果，再执行放行 | [Task Map skill](../../skills/zf-plan-task-map-contract/SKILL.md)要求消费者义务和交接；v5 [dependency selector](../../src/zf/runtime/task_pipeline_runtime_selection.py)只接受 `done`，legacy writer 另有终态放行问题 | v5 需核实既有合同／Verify／集成结果是否覆盖下游义务；不能仅因调度消费 `done` 就认定缺失语义裁决，也不要求每条边重新调用模型 |

前三项首先说明“字段不等于已执行的硬约束”。如果字段本来只表示建议，应在配置语义、briefing 和界面中明确；
如果承诺强制执行，则需要真实执行入口和拒绝测试，不能以配置可解析、提示词已包含或策略快照已保存作为完成证据。
这些字段缺口先核对 v5 实际声明及承诺；不把未启用字段当作当前失效的硬门。v5 已显式关闭累计执行时长、token 和 usage-sample 上限，仍保留活性检查及有效金额限额；本文不因 `max_steps` 缺少消费者而新增固定步骤、时长或 token 限制。

### 15.3 本应 Agent 判断，当前被代码直接决定

| 项目 | 应由 Agent 判断的内容 | 当前代码行为与证据 | 路径／生效范围 | 应收敛的边界 |
| --- | --- | --- | --- | --- |
| 取消／替代后，产物或能力依赖是否满足 | 下游是否仍需要该能力；是否已有有效替代；是否应删边或申请豁免 | [writer dependency gate](../../src/zf/runtime/writer_fanout_dependency_failure.py)将 `done / cancelled / superseded` 都视为满足；[writer slot reconciler](../../src/zf/runtime/writer_slot_reconciler.py)消费该门 | **Legacy writer 已复现；v5 主 Pipeline 未复现此缺陷**，后两种状态保持 `dependency_blocked` | Legacy 单独修复；保留 v5 已有阻断。产物依赖的替代、删边或豁免仍需有效依据 |
| 未知失败的根因与返工责任 | 区分需求、规划、实现、环境、测试或集成原因，选择负责人 | [rework triage](../../src/zf/runtime/rework_triage.py)可将 `dev.failed` 的 `failure_class: unknown` 转为 `product_issue → dev → dispatch_rework`，同时增加返工计数；探针已复现 | **共享分类函数**；须核对 v5 对应事件是否经过此分支和返工消费者 | 保留 unknown 和缺失证据，指定诊断 owner；不以 v5 存在 OA 入口就认定旧分类已被绕开 |
| 自由错误文本的实际含义 | 区分实际故障、预期断言、日志引用及普通说明 | triage 可因 `connection refused` 判为环境故障；[provider_stop](../../src/zf/runtime/provider_stop.py)对 `author metadata saved` 加终态标记仍返回 `auth_error → suspend` | **共享 triage／Provider 组件**；分结构化错误和文本 fallback 核对 v5 实际调用 | 已知结构化错误可走明确策略；自由文本仅作为线索，不能无依据替代诊断 |
| 集成冲突是否需要重规划 | 冲突是基线变化、局部修改重叠还是任务边界错误 | [rework triage](../../src/zf/runtime/rework_triage.py)将 `candidate_integration_conflict` 映射为 `design_issue → planner → request_replan`；使用 v5 配置调用仍复现，虽 YAML 的 `integration.failed` 路由为 `dev-lane-0` | **v5 配置下的共享函数已复现**；完整事件到执行效果仍需调用方验证 | 路由诊断请求与认定根因分开；不凭 YAML 路由不同就宣称覆盖了显式分类分支 |
| 合同是否具体、可验收 | 需求、范围、验收及验证是否充分 | [ContractQualityD](../../src/zf/core/verification/discriminator.py)按长度、空格分词等判断；完整中文失败，同义英文可通过 | **v5 未启用的可选门**；不计入当前主路径缺陷 | 格式规则降为 lint，语义质量交 Planner／Critic；作为独立兼容治理项 |
| 安装命令是否适合当前任务 | 根据环境、工作区和任务目的选择方法 | [task_map._verification_level_errors](../../src/zf/runtime/task_map.py)拒绝部分 `pip install` 命令，断言其不能在隔离任务中成功，已接入 Task Map 校验 | **共享 Task Map 准入组件**；按 v5 实际 Plan 输入和准入链复现 | 项目适用性归 skill／Agent 或明确环境合同；代码核验授权及能力，不把局部经验当通用禁令 |
| 验证前应采用何种环境准备方案 | 选择依赖、工具链及准备方法 | [ContractD preflight](../../src/zf/core/verification/discriminator.py)按命令字符串和文件状态隐式安装依赖或准备特定 Web 环境 | **共享验证组件、条件分支**；须核对 v5 当前 gate、命令及授权来源 | 安装可由 runtime 执行；方案来源显式进入配置或 Agent 产物，不能一概认定所有准备动作越界 |

上表的函数行为、配置启用与实际运行效果是不同证据层级，不能把七项都归为 v5 已生效缺陷。最后一项也不能概括成“runtime 不得安装依赖”。
v5 内存对照使用 [terminal_dependency_ids](../../src/zf/runtime/task_pipeline_runtime_selection.py) 和 [Task Pipeline reconciler](../../src/zf/runtime/task_pipeline_reconciler.py)：`done` 可到 `impl_ready`，`cancelled / superseded` 均保持 `dependency_blocked`；这是依赖门复核，不是完整工作流验收。
同样，固定错误码到有界退避的已授权策略可以保留；不是要求每个错误、每条依赖或每个调度 tick 都调用模型。

### 15.4 应继续保留在代码中的控制

| 职责 | 当前已查到的实现 | 不应作出的误判 |
| --- | --- | --- |
| OA 决策的身份、输入摘要和版本绑定 | [orchestrator_agent_admission](../../src/zf/runtime/orchestrator_agent_admission.py) | Agent 生成决策，不等于可以自行认定身份／版本有效 |
| 动作授权、当前目标和执行资格 | [orchestrator_agent_execution_policy](../../src/zf/runtime/orchestrator_agent_execution_policy.py) | 不能把权限例外交给 Agent 自批，也不能因存在提示约束就否定受控动作门 |
| 已授权预算上限与执行停止 | [workflow_budget_guard](../../src/zf/runtime/workflow_budget_guard.py) | `max_steps` 字段的缺口不代表整个系统没有预算控制 |
| 当前任务与 operation 的写入范围 | [hook_recv._evaluate_allowed_paths_guard](../../src/zf/cli/hook_recv.py) | 此门消费 Task contract scope 和 operation scope；不等同角色 `blocked_paths`。未定义 scope 或无法解析写目标等覆盖条件仍需明确 |
| 前沿 owner、重复派发与当前版本结果接纳 | [workflow_frontier_owner](../../src/zf/runtime/workflow_frontier_owner.py)、[candidate_success_currentness](../../src/zf/runtime/candidate_success_currentness.py) | runtime 根据已准入依据调度、隔离旧结果是必要职责，不应迁移为模型每次重新判断 |

Provider 默认采用可信本地执行基线，见 [provider_permissions](../../src/zf/runtime/provider_permissions.py)。
控制面授权、hook、事后结果准入与 OS 沙箱不是同一层保证；任何一层存在都不能自动证明另一层的隔离覆盖。

### 15.5 尚未理清的交接与证据

| 待核实事项 | 已有事实 | 还需要证明什么 |
| --- | --- | --- |
| `done` 对下游消费义务的含义 | 调度读取终态，Plan 方法另有输入／输出与依赖要求 | 下游义务 → 上游合同 → 适用验证 → 当前结果绑定 → 派发的完整链。若该链成立，复用 `done` 可以合理 |
| RM Agent 与 OA 的未知故障交接 | RM 有诊断／修复职责；[OA execution review](../../src/zf/runtime/orchestrator_agent_execution_review.py)主要处理符合条件的当前 settled Impl 语义阻塞 | 运行中求助、数据库故障和未知原因的首次接收、单步责任、超时出口及向 OA 移交路径；不能以一个 settled 入口宣称通用求助已实现 |
| Verify／Judge 的充分性 | 有身份、版本、引用及结构准入 | 当前证据是否覆盖验收行为与跨任务效果。有效报告、矩阵完整或测试退出码为零不自动证明产品完成 |
| Profile／复杂度与 Autoresearch 策略 | 有关键词、文件数等启发式；[gate_policy 的已查调用](../../src/zf/cli/workflow.py)用于审计输出；[review_gate](../../src/zf/autoresearch/review_gate.py)有 review 路由策略 | 逐调用区分推荐、明确授权的保守策略与实际语义裁决；不能仅因使用启发式就认定越界，也不能据投影断言执行门被跳过 |
| 约束在不同 backend 的执行一致性 | 配置、提示、provider、hook 和受控动作各有部分机制 | 对每项硬约束列清支持入口、拒绝行为、例外及未覆盖路径；配置字段存在不是跨 backend 强制执行证明 |

以“数据库模型支持多租户隔离”为例：Agent 或明确声明的行为验证建立满足依据；runtime 核验当前合同、提交和证据绑定后放行。
绑定仍有效时复用已接纳裁决；输入／合同变化时检查失效条件，并由 Agent 判断语义影响，或执行适用的明确验证。
因此不能笼统说“runtime 无法判断任何依赖”，也不能说“上游 done 就天然满足所有下游”。

### 15.6 整改顺序与补充验收

后续实施应分别建立“语义决定归位”“硬约束执行”和“裁决交接”切片，不能统一归为优化 skills 或增加 runtime 分支。
这是实施建议，不表示本轮批准、创建或完成了 backlog。

1. 按 §11 先闭合 v5 的恢复步骤、效果与下游交接，再补未知故障及 OA／RM 单步责任交接；共享 triage 必须核对 v5 的事件生产者和控制消费者。
2. 补运行中求助与旧 attempt 的受控交接；贯通既有 Agent 裁决、合同和证据，不新增第二写入者或每轮模型审批。
3. 对 v5 实际声明的步骤、路径和工具约束核验硬／软性质与 backend 支持，补真实执行缺口；不能以通用字段治理名义新增未经授权的执行上限。
4. 基于已验证闭环逐入口收敛共同准入和生命周期，保留 WRC 正常前沿、独立 Verify、串行集成及当前版本准入。
5. Legacy writer 取消／替代问题、未启用 ContractQualityD 和其他 workflow 单独治理；项目环境适配按 v5 实际入口复现后归位，不混入主路径完成度。

每项记录约束来源、判断 owner、执行入口、所需证据、失效条件和失败去向。Agent 的 replan 或工件不能自行增加权限、预算或豁免验收。
以下为补充验收场景，后续针对相应实现执行或回归；既有 v5 能力只做回归，不重复实现，不能把文档列出视为通过。

| step | verify: check |
| --- | --- |
| 分别在 v5 主 Pipeline 与 legacy writer 中取消／替代被依赖任务 | v5 已有 `dependency_blocked` 行为保持；legacy 缺陷单独验收。没有有效变更依据不派发，获准后按当前绑定推进 |
| 上游结果已通过，但改变合同、目标提交或消费义务 | 过期依据不被复用；已有且仍适用的裁决不触发每轮重复模型判断 |
| 从 v5 实际可达入口提交 unknown 失败、含 `connection refused` 的预期断言、含 `author` 的普通说明 | 记录事件生产、准入和消费链；不凭文本断定根因或启动错误恢复，结构化已知故障仍按已授权策略处理 |
| 注入可局部合并与确属任务边界问题的两类冲突 | 诊断依据与处理决定分开；只有获准的计划变更才进入重规划 |
| 在显式启用的质量门治理场景输入同义中文与英文合同 | 不因空格分词给出不同语义结论，结构缺失仍报告；不为此测试改变 v5 默认配置或声称已验证 v5 产品质量 |
| 分别声明允许和不适用安装准备的任务环境 | 方案按合同与能力选择；runtime 执行获准准备并记录效果，不由命令类别统一决定适用性 |
| 对声明支持硬限制的 backend 配置步骤上限、禁写路径和工具范围 | 在实际执行入口证明计数／拒绝；同时验证恢复计数、路径匹配及未授权调用，不能仅检查 prompt 文本 |
| 切换 backend 或进入无法强制某约束的路径 | 明确暴露能力差异并遵守获准降级策略，不把提示约束或权限快照标作已强制执行 |
| Agent 提出越权、超预算、旧版本或无绑定证据的动作 | runtime 拒绝；Agent 自报理解规则、完成任务或需要例外不能替代机械准入 |

本轮文档核验覆盖 v5 配置加载、文本结构、引用和变更范围，不构成上述运行行为验收。200 保持 candidate；199 的内容、编号与原有证据不变。
