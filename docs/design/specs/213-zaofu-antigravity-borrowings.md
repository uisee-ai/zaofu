# ZaoFu 规格草案 · 里程碑交接、独立验收与定时唤醒

**状态**　候选设计，供架构与实现评审。基线为 public/zaofu `e178f43c196c02317a95828ea45b10960aa63e42`。本文中的默认策略是提案，未经部署。研究依据和官方资料见[调研报告](antigravity-orchestration-review.zh-CN.md)。

**首个交付场景**　一个里程碑刚结束，下一位管理 Agent 没有上一会话历史。它应能指出已获准的目标、当前 commit、哪些验证有效、哪一步结果未知、下一步由谁负责。即使进程在交接中退出，也只允许一个有效后继执行。第二阶段才允许“等外部事件，超时提醒”，第三阶段才允许“无人值守地新开一次作业”。

## 1. 责任与落点

| 负责方 | 规范 | 已有接点 |
|---|---|---|
| Kernel/WRC | 以现有事件与 Store 确认业务状态、当前 request、attempt、租约、准入和受控外部效果。新读模型和 UI 均从这些事实派生。 | [操作归约](../../../src/zf/runtime/workflow_operation.py#L179) · [受控动作](../../../src/zf/runtime/control_actions.py#L116) |
| OA / 项目管理 Agent、RM | 管理者拟订计划、分工并解释证据；RM 接手故障分诊。需修改目标、重新批准、恢复人工作业或增加预算时走已有 owner 路由。 | [既有恢复职责](../199-worker-failure-recovery-and-role-handoff.md#L125) |
| Role / Worker | Role 持有 prompt、Skill、Provider、权限和执行身份；每个 attempt 绑定实际 actor、工作区及结果协议。可选双模型模式属于同一 Worker 的 Provider 适配或执行策略。 | [RoleConfig](../../../src/zf/core/config/schema.py#L335) · [工作区绑定](../../../src/zf/runtime/task_workspace_binding.py#L19) |
| Runner | 按获准的 operation 做 placement、挂载与执行，返回开始、输出、停止和事实回执。重连须按原 request 对账；任务重派、模型扩权、阶段推进由 Kernel 的既有路径决定。 | [操作服务](../../../src/zf/runtime/workflow_operation.py#L453) · [替换前停止确认](../../../src/zf/runtime/respawn_lease.py#L13) |
| Verify / Judge | 针对各自获批 profile 和精确 target 独立判断。Kernel 校验证据、身份与状态，保留 blocked、abstained 及其原因。 | [验收 profile](../../../src/zf/runtime/artifact_role_inputs.py#L9) · [集成结果校验](../../../src/zf/runtime/integration_acceptance_result.py#L66) |

## 2. P0 · 里程碑交接与中断恢复

### 2.1 冻结什么，谁来消费

以已有 task-handoff.v1 为基础。它已经包含 objective、contract_revision、remaining_acceptance、workspace、admitted_results、work_record、unknown_effects 和 next_action。现有 attach_task_handoff 按同 Run、同 Task 的前驱生成交接，并在后继 request 冻结前挂载；重放复用原工件。跨 Task 的里程碑需要显式引用获批成员集合及各自结果，不能直接当成已有的同 Task 交接。沿用其绑定与 receipt 校验，避免把新材料补进旧 request。大正文用不可变 ref/digest。消费者可由短索引打开原文；必读项的交付资格沿用该 request 的策略。[交接体](../../../src/zf/runtime/task_handoff.py#L20) · [接收与准入](../../../src/zf/runtime/task_handoff_receipt.py#L21) · [工作记录](../../../src/zf/runtime/work_record.py#L138)

| 工件中的最小事实 | 来源与绑定 |
|---|---|
| 目标、未决义务、允许变更范围 | 已批准计划/合同版本、run、milestone 对应的阶段或任务集合、task map generation |
| 代码与执行事实 | 仓库身份、base、候选 target、任务 workspace generation、已经接纳的 operation/attempt、结果 ref/digest；外部效果带来源及权威回执 |
| 草稿和问题 | 经现有授权发布的工作记录，标明作者、时点和绑定。明确区分“已获准结果”“仅有草稿”“已开始但效果未知” |
| 下一责任人 | 期望 role、待验证义务、对应来源及前置条件；短交接本身无执行授权、无业务完成权 |

### 2.2 交接协议

1. 当前 owner 提交阶段结果。Kernel 核查原 request/target，记录已接纳的判词。只有获批转移才能触发下阶段；blocked 指向相应恢复责任人。
2. 用当前状态生成不可变交接体；正文和摘要可验证后才发布引用。崩溃只留下无引用正文时，重启从 canonical 输入重新生成或复用同摘要，不能从孤立文件认定已切换 owner。
3. 使用现有 continuation reservation，绑定 run、continuation key、预期 generation、package digest、pending action、预算和有效期。重复请求读取原预留；两位竞争者至多一位取得当下执行权。[预留入口](../../../src/zf/runtime/workflow_operation.py#L764)
4. 交接给 fresh successor 可以创建新 Provider 会话。发送前重核目标、角色、租约和是否已有 admitted 结果。Worker 读取绑定的工件并提交消费回执；派发后丢应答时按原 request 查询。旧 Worker 仍在写时先确认其停止或在受控执行面隔离旧权限。[接收判定](../../../src/zf/runtime/task_handoff_receipt.py#L110) · [替换屏障](../../../src/zf/runtime/respawn_lease.py#L13)
5. 若原生会话仍有效且政策允许，可继续原 attempt。若只有会话不可恢复，但执行权仍有效，则重新交付原合同、工作记录和缺口。旧合同或目标已被替代时走受控新绑定。模型读到旧 session、看见旧板帖、持有旧目录，都不足以激活已封存 attempt。

### 2.3 对不同中断采取什么动作

| 已知事实 | 行为与预期 |
|---|---|
| 原结果已准入，但下一步尚未派发 | 从原 verdict 和稳定 continuation 继续。成功、阻断、弃权分别进入已有路径，避免再要求原 Worker 提交一遍。 |
| 缺应答，外部副作用结果未知 | 先查原执行标识的宿主回执、外部系统或仓库事实。可证明未执行或外部支持同幂等键时才允许在原预算内重试；否则分诊，记录责任人。 |
| 明确的瞬态基础设施故障 | RM 依据现有恢复权限发起同合同的新 attempt；保留旧失败、尝试序号与次数上限。配额耗尽进入等待，只有确认恢复或新的授权触发才重新评估。 |
| 实现与条款冲突 | 原作者按固定反馈返工，再由独立角色复验。涉及验收标准、权限或合同修订时须经原批准流程，重新验证哪些旧证据仍适用。 |
| 已消费 wait，随后出现同候选的新权威失败 | 以新 failure 的因果事件、当前 generation 和受影响 scope 请求一次诊断。同一输入不重复唤醒；仍在途的诊断不抢占；诊断许可不含宿主重跑许可。 |

## 3. P0 · 工作区与可计算的验收

**解开 Role 与产品目录的绑定。** 从 Impl→Task Verify→Candidate 试点。角色 home、Provider 状态和临时 scratch 可随角色保存。产品代码从 task/run/map/workspace generation 和精确 base 解析。旧在途派发继续使用原工作区，worker 重用时保留未提交草稿；新派发只从绑定定位，找不到时进入诊断。任务可在新 Runner 上接手，先查候选对象、未发布改动和未知效果。验收与集成完成后再按已有回收条件清理。并行计划应写明写入范围、共享接口及唯一集成人；文件重叠先串行或拆约，集成冲突保留各提交并派定向返工。[目录计划](../../../src/zf/runtime/task_workspaces.py#L81) · [回收门](../../../src/zf/runtime/task_workspaces.py#L245) · [集成身份](../../../src/zf/runtime/candidate_integration.py#L25)

**按义务定义 readiness。** 批准计划时为每条验收义务指定适用阶段、精确目标、独立性要求、证据类型、验证方式、允许的捷径和失败 owner。Critic 审逻辑/接口，Challenger 提供经批准的边界与失败用例，Auditor 核对执行来源、范围、是否跳过/模拟，最终 Judge 对 Goal 证据作语义判断。四项是职责，启用哪些角色、是否每个里程碑运行由合同冻结。已批准的依赖复用和测试替身按项目合同处理；无需普设 benchmark 的标准库限制。[集成验收](../../../src/zf/runtime/integration_acceptance_result.py#L66) · [Goal 闭环](../../../src/zf/runtime/goal_closure_identity.py#L189)

**判定。** 针对同一批准版本与 target，只有每个必需义务都有当前、合法、独立性符合要求的已准入 passed，所有强制 gate 满足，且无阻塞性未知效果/未决 finding 时，才显示“可提交下一门”。对空义务集须有获批的明确豁免；缺事实标为待证，准入了 blocked 仍是 blocked，豁免须引用批准版本。Readiness 为可回放的投影，正式推进仍走原阶段准入。展示每个缺口的义务、期望证据、已有事实和下一 owner。8×8 浏览器命令退出 0、媒体 provenance 不适用时，保留执行事实，媒体义务仍阻断；再次交接不会给同一命令新的执行许可。

## 4. P1 · 公共消息板与定向唤醒

以现有 Channel 入口、分派竞争控制和事件唤醒为基础实现可选的 Run 视图。板展示公开 brief、所有权、问题、结果摘要和证据引用。授权限定于 Run 和受众，敏感原文走已有工件权限。每个消息保存稳定来源 ID、run、发送者、接收者、因果事件与正文 ref；组和全体在发送时冻结成员集合。不会把私人消息、工具原始转录或模型内部推理复制到板。[消息入口与去重](../../../src/zf/runtime/channel_message_ingress.py#L182) · [分派 claim](../../../src/zf/runtime/channel_dispatch_claim.py#L30) · [唤醒配置](../../../src/zf/runtime/wake_patterns.py#L450)

对每个目标以“消息 ID＋接收者＋授权 generation”记录处理结果；投递可重复，接收端据同一键使重复投递不再产生业务效果。Viewer 的已读游标与 Worker 的已确认消费分别记录。合法提及只请求唤醒当前可工作的角色；owner 已换或任务封存时记录过期原因。Monitor 发现所有成员静默可请求 RM 诊断；有效 lease、长工具请求和财务用量分别核对。私聊补充若要改变任务范围，须由获准流程固化后重新派发。

## 5. P1/P2 · 调度合同

**新增语义。** P1 是当前 operation 的事件/超时等待；P2 是独立、可关闭、跨会话的作业定义。现有 wake 过滤和 continuation 可复用，现有 periodic sweep 负责对账，Feishu cron-template 输出供外部 crontab 使用的命令。在已检查源码中尚未定位涵盖下表全部语义的通用持久 cron 服务。先以原生事件和时钟驱动受控动作，Runner 只处理已获准请求。[既有对账](../../../src/zf/runtime/orchestrator_periodic_sweep.py#L10) · [Feishu cron-template](../../../src/zf/cli/feishu.py#L537) · [EventWatcher](../../../src/zf/runtime/watcher.py#L29) · [唤醒过滤](../../../src/zf/runtime/wake_patterns.py#L652) · [稳定预留](../../../src/zf/runtime/workflow_operation.py#L764)

| 项 | 本规格默认值或行为 |
|---|---|
| Trigger | 单次 deadline；特定来源/类型事件或 deadline 先到者；五字段 cron + 显式 IANA 时区；手工 Run Now 有独立 request ID。会话内任务默认随 operation 终止。独立作业须显式启用，并提供自包含目标、冻结授权及输入入口。 |
| 身份 | 定义标识、修订、owner、project scope、授权摘要、启停状态与生效时间。周期 occurrence 取“定义 ID、revision、计划 UTC 时刻”；单次等待用同一 wait ID 对事件/超时仲裁。运行次数按被接纳的逻辑触发计，与 retry 次数分开。 |
| 时间 | 存计划触发、实际认领、启动和完成时刻；不得把预估时间记成实际。默认关闭主动抖动，如开启则记录上界且不适用于硬 deadline。提案默认夏令时缺失的本地时刻跳过，重复的本地时刻只取较早 UTC。 |
| Missed trigger | 周期状态巡检默认将停机期间的多个到期合并为最新一次，记录跳过范围。时效性过期或定义已停用则跳过。只有显式授权的逐期任务才有限额补跑；一次性等待重启时核对到期前是否已有合格事件。 |
| Overlap | 同一定义默认至多一个在途，期间的到期合并保留最新一次；有外部写入的作业按受控资源进一步串行。需要并发的只读作业显式设限。 |
| 认领与恢复 | 持久记录逻辑触发再按稳定键预留 operation；崩溃重启复查同一 occurrence 与原 request。认领 lease 到期需要对账，旧 owner 未隔离或副作用未知时禁止再次执行写操作。 |
| Retry / backoff | 只有可归因的瞬态故障且后果可核验才按同 occurrence 新 attempt 重试。定义中冻结初始间隔、上限、最大次数、总 deadline；按指数退避并以剩余预算封顶。权限变更、产品失败和未知写入分别送 owner，不能由定时重试绕过。 |
| 取消、改版与权限 | 停用取消未派发 occurrence；已派发按原取消协议终止及对账。修改 cron、工作区、目的或授权产生新修订，生效界限防止新旧重复触发。无人值守时遇到待批权限则暂停，交 owner；试跑记录只生成待批准的最小授权建议。 |

**并发规则。** 消费触发事实时在同一作用域串行检查并认领。若 deadline 与事件均已到，优先选截止前已被 Kernel 接纳的匹配事件；等时用 canonical 日志序决定。只有一个获胜原因可生成 continuation。迟到的第二个记录为历史事实。外部投递失败时允许用同键恢复送达，不能宣称外部系统具备 exactly-once；外部不可查且未提供幂等接口的写操作停在“效果未知”。被合并的错过周期、人工触发和按原周期的 retry 在审计中可独立查询。

## 6. P2 · Worker 内的双模型试验

在单个角色的既有 attempt 内，Planner 输出只读步骤、交回条件，Executor 执行范围内操作，遇到两次相同验证失败、接口偏差或未决设计选择时回交。交回携带原目标、已执行步骤、真实回执、未决判断；模型适配不产生额外 Kernel 状态机。provider/session 不支持共享上下文时明确交付冻结输入，或退回已验证的单模型。若变更合同、所需权限或资源超出预算，提交受控请求。记录每次角色内模型选择、原因及耗时，在相同任务、权限和验收条件下比较成本、正确率、返工次数。独立 Verify 始终使用其原角色、输入资格与准入路径。

## 7. 验收矩阵

在独立状态目录中使用固定来源、目标 commit、可观察 effect 计数和可控时钟。先以确定性重放核机械判定，再对涉及真实上下文交付、Runner 停止和 Provider 身份的场景做定向实测。下列是应测条件。

| 场景 | 可审计的预期结果 |
|---|---|
| 前驱已准入，交接正文已写但引用未提交；或引用已提交、下一派发无应答 | 重启修复到同一来源和 digest，至多一个有效 successor；前驱执行计数不增加。 |
| 旧 session 恢复迟到，新后继已获准；或源 target/contract 已换 | 旧身份不能写入新 attempt；旧合法历史仍可回放。新后继拿到当前版本的依据。 |
| 只有草稿或副作用只有 started，外部回执不可查 | 保留草稿或未决项，指定 owner，执行计数保持原值。 |
| 并行任务改同文件，或改不同文件破坏共享接口 | 前者在授权/集成处串行或返工；后者被 Candidate 级检查发现。保留两边提交与精确失败目标。 |
| 回收 Role 时任务 workspace 有未发布改动；换 Runner 时某对象不可用 | 清理受阻、草稿可定位；恢复给出缺失对象与 owner，不把空目录当作原工作区。 |
| 同 commit 的媒体索引缺证、测试被跳过或仅有 mock 成功；以及同条件的有效正例 | 各缺口按义务阻断；合法完整媒体及真实结果能通过对应门。审核人数、命令 exit 0 和文件存在均不改变这一判定。 |
| 重复板帖、组成员在投递中更换、错 run 提及、旧回执延迟到达 | 合法原接收者最多一次有效唤醒，过期身份不消费；原文与每个接收状态可追溯。 |
| 合格事件先于 deadline，仅 timer 到期，二者同拍，及两者之间重启 | 每个 wait 恰有一个胜因，至多一个有效 continuation；过期目标不复活。 |
| 控制器停机跨多个周期，长作业与下一周期重叠，夏令时跳变，停用后重启 | 可从定义和事件列出预期 occurrence、跳过/合并原因和实际偏移；在途上限不超，停用作业无新增派发。 |
| Sidecar/Runner 重启、quota 暂停、人工解除、新诊断再次遇到同 failure | 进程重启不生成业务成功；原失败保留，合法恢复走原作用域，同一 failure 不重复诊断。 |
| 单 Worker 模型切换，Executor 需要额外写入权限，或试图自审结束任务 | 原角色权限不增加；需要扩权则停在受控请求，独立 Verify 仍需单独准入。 |

## 8. 放行、观察与回退

| 批次 | 第一条真实路径 | 放行证据与回退 |
|---|---|---|
| P0 | 同一已批准里程碑，先影子生成交接/义务视图，再选一个新派发让全新会话接手。 | 固定案例的结果、target、未决项、责任人、已完成命令计数与原路径一致；重启及竞争反例通过。停用新增视图后，仍可由原 briefing/CLI 与不可变回执恢复。 |
| P1 | 一个 bounded wait 和一个 Run 的定向 board mention。 | 核对触发数、有效唤醒数、重复拦截、延迟、无主等待。回退时停止创建新等待；已有等待按原绑定取消或完成。 |
| P2 | 从只读、有限次数的持久巡检开始；双模型另设对照试验。 | 验证时区、漏触发、停用、预算、外部写入未知反例；试跑授权经 owner 批准。收益按实际延迟、token、端到端质量报告，证据不足则维持原路径。 |

候选设计对既有在途 operation、历史资格或生产调度不产生动作。实施和运行验证应另行在目标仓库立项。
