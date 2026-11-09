# ZaoFu Worker 失败恢复与角色交付闭环

> 状态: partial
> 实施与覆盖：[199 实施记录](../impl/199-worker-recovery-implementation.md)。正文保留设计基线；软件交付与真实运行验收分别统计。
>
> 日期: 2026-11-06（UTC）。基于 LS、LS-v1 实际失败与源码复盘形成的设计方案。
> 汇总 R01–R15、A01–A05、B01–B06、C01–C05 及脱离监工 session 的持续推进要求。
> 同日全面复核及本地 LangGraph／Deep Agents 源码对照：限定 ZaoFu 范围，明确 WRC／RM／OA 分工、处理器恢复与验证边界。
> 本文仅记录方案；不表示下述改进已实施、部署或通过真实运行验收。

## 1. 目标与适用范围

建立 Worker 失败后的恢复闭环：Worker 向 Kernel 上报事实，WRC 负责确定性对账、
已准入恢复的执行和任务阶段推进；Run Manager runtime 协调需要恢复决策的运行异常，
请求动作并跟踪效果。需要语义判断时走已配置 OA／Planner 检查点，需要运行诊断时
按需调用 RM agent／诊断角色。Impl/Fix 修复、Verify 独立验证，状态由 Kernel 管理。

每次失败有责任人、再次派发有依据、恢复结束有证据；不保证模型一定修好，但禁止无依据重复派发并保留明确出口。

持续推进的核心要求是：未完成且未被明确暂停或取消的工作，始终有当前责任人和
可验证的下一步。允许排队、恢复与真实外部等待；没有执行者、恢复动作或合法等待
依据的停滞必须被发现并处理。详细合同见 §13，不以模型持续输出替代任务推进。

本设计覆盖失败分类、修复派发、角色输入输出、等待与超时、断点恢复和验收，
并补充计划可执行性、执行代际切换、修复部署生效及多 Agent 运行隔离。
保持单一 Task `contract`，扩展已有 operation、恢复案卷与工件，不新增并行状态机。

与现有设计的关系：

- [195：OA/WRC Worker Runtime](195-oa-wrc-worker-runtime-borrowings.md) 提供执行身份、租约与唤醒边界。
- [196：Inbox 人工决策中心](196-inbox-human-decision-center.md) 提供人工决定与受控动作边界。
- [198：上下文索引、写后读取与恢复闭环](198-context-index-write-read-resume.md) 提供固定版本输入、任务切片与恢复上下文。
- 状态所有权遵循 [AGENTS.md](../../AGENTS.md) 及当前代码、测试。

### 1.1 范围裁定：只交付 ZaoFu 的通用改进

LS／LS-v1 是故障来源和可选真实验证项目，项目业务修复不属于本文实施范围。
“属于 ZaoFu”包括 runtime、CLI/Web、通用 skills／prompts、测试与发布方法，不等于全部写进 Python。

| 改动归属 | 对应条目 | ZaoFu 交付边界 |
| --- | --- | --- |
| Kernel／WRC／RM 与机械准入 | R02–R04、R06、R10–R12；A01/A02/A04/A05；B01–B04；C02/C03/C05 | 身份、阶段、调度、恢复、权限、幂等、存储和投影；不代替业务判断 |
| 通用 Planner／OA／Verify 方法 | R05/R07/R08、A03 | 阶段责任、依赖理由、需求矛盾和证据适用性；扩展已有 skills／prompt，不硬编码项目结论 |
| 装配、交付与诊断 | R01/R09、B05/B06、C01/C04 | 可追溯版本、请求义务、有效配置、实际输入及性能证据；复用现有 owner |
| 跨任务协调、隔离及发布验证 | R13–R15、§13 | 通用关联／隔离能力、参数化负载和无监工验收；真实环境由获准验证部署提供 |

不在本文派发的工作：补齐 FMS 页面／母版／菜单，确定 Q02 等业务接口，修改 LS-v1
Task Map，配置其数据库／Compose／Keycloak／LDAP，决定认证接入时间或部署该产品。
这些由 LS-v1 自己的需求、计划和任务负责；本文只用它们验证通用机制是否正确。
16 路（或其他 Impl／Verify 配比）、fixture 和取消费用上限均为项目配置／授权样例，
不得变成 ZaoFu 全项目固定策略；保留其他项目合法配置的限额、真实依赖和验证义务。

实施项统一记录“当前代码确认缺口／已有局部能力待闭环／历史修复待回归／待验证假设”。
本文编号不是 31 个必然需要新增代码的任务；无当前缺口时只补缺失证据，不重写既有能力。

## 2. 证据基线与当前实现

源码核查基线为 ZaoFu `cff2a8d1`。运行证据来自 LS-v1 2026-11-05 至
2026-11-06 的事件与监工记录，其中 Run Manager 快照时间为
`2026-11-06T02:01:57Z`。源码基线不代表常驻控制面已经加载相同版本；
下列快照也不作为阅读本文时的实时状态。

现场配置的 `project.state_dir` 为 `.zf-ls-v1`；通用实现必须从 `zf.yaml`
解析状态目录，不得把该路径写成默认值。

| 环节 | 已核实实现或现场事实 | 缺口与影响 |
| --- | --- | --- |
| 首次失败评审 | `pending_execution_reviews` 在策略启用时，可以为当前已结算、未通过的 Impl 生成 OA 评审动作 | 能生成动作不代表已经派发；不能把问题概括为缺少 OA 入口 |
| 恢复动作队列 | 快照有 4 项动作，preflight 为 passed、recovery admission 为 admitted，但 `next_operation=null` | FMS-02 的 OA 分诊与 FMS-03 的重规划未继续派发 |
| 人工等待 | 旧 FMS-03 orphaned 升级 `evt-5c7606e013c0` 仍为 run 级等待，结构化 `task_id` 为空 | 旧任务被替代后等待未完成对账；本次队列下阻止下一动作选择 |
| 动作选择 | `build_run_continuation_projection` 取队首动作；存在阻塞人工决定时通常不生成 next operation，仅保留窄范围历史 gate 物化例外 | 需要按作用域判断可执行性，并处理队首阻塞 |
| RM 活性 | 机械 tick 继续运行；resident 最后一次观察为 `evt-31936b803c63`，投影仍为 observing | 进程存活、投影更新不证明已有请求正在被处理；静默也不能一律判故障 |
| 返工耗尽 | Task Pipeline 比较 operation generation 与返工上限，耗尽事件推导 failure_count/retry_count | 代次不等于同一根因的实际语义失败次数 |
| OA 超时 | `run_manager_rework_triage` 已有超时与不可用回退 | 还需验证请求排队、未派发阶段及回退动作自身的调度闭环 |
| 上下文消费 | 已有任务切片和 `plan_input_selection`；本轮核查的 27 项计划任务未声明选择 | 能力存在不代表该计划使用；恢复后仍出现约 135 万字符来源索引的全量分页读取 |

FMS-02 曾收到 OA 的具体指导，不能描述为 OA 从未介入。其后续失败分别涉及
页面未完成、CLI 未绑定原始聊天却被要求读取，以及剩余验收缺口，不能混作同一原因。
FMS-03 的正式负责人、接口与版本待决是真实约束，但应区分正式联调门槛与
允许使用草案或 fixture 的独立开发；调整依赖需要新计划准入。

证据定位：

- LS-v1 监工记录：`/home/min/workspace/zaofu/ls-v1/docs/records/2026-11-05-ls-v1-start-monitor.md`，重点为 01:42、01:55 UTC 复盘段。
- FMS-02 OA 指导：`evt-6eb8ac5c62a9`；最终失败：`evt-a1d635a3f3bb`；返工耗尽：`evt-9008ea547fd0`。
- FMS-03-GAP-CONTRACT 失败：`evt-5fe7e2b98b70`；分诊：`evt-3801f9a176f1`。
- §13.4 的 LS 历史证据：`/home/min/workspace/zaofu-echo/ls/docs/records/2026-10-31-ls-supervision-recovery.md`，定位取消采样上限、身份／设计复扫互相覆盖、有效预算简报及 hook 内存段。
- 满盘与快照证据：`/home/min/workspace/zaofu-echo/ls/docs/records/2026-11-03-ls-full-disk-plan-base-block.md`，定位截断拼接事件及在线复制段。
- 现场记录属于外部项目证据，本文只保留定位信息；不复制完整转录、令牌或敏感正文。

第二轮复盘继续使用同一源码基线与上述监工记录。§10 的“已发生”表示历史现场
出现过，部分问题已有隔离修复；是否合入、部署和真实验证必须逐项对账。
“已有基础”不等于当前流程已生效，“待验证”也不表示已经观测到生产故障。

补充证据定位：

- 2026-11-05 02:47–02:50：FMS-16 原稿要求保留安装历史，计划写成删除；03:23–03:28：未批准候选使旧执行输入失效。
- 04:44–04:59：新代际曾未携带既有代码；05:01：Autoresearch 禁用而旧请求未终结；07:35：TaskRef 恢复与重复 Impl 派发竞态。
- 05:40：Web 与控制面代码基线不同；05:43：基础 fixture 与最终真实验收共用命令 ID 的含义冲突。
- 15:48–15:58：操作员误将 shadow revise 绑定为正式重规划授权；16:04：方法已更新但当前任务未启用输入选择。
- 当前源码内存复现：两个任务分别持有 `compose.yaml`、`Dockerfile` 时，`plan_digest_checklist` 同时返回 `root_owner_unique=false`、`scope_no_overlap=true`。这不证明同文件写冲突，也不证明该摘要就是实际准入阻塞源。

## 3. 权威与职责边界

1. Worker 通过受控结果或事件向 Kernel 报告，不建立直接修改状态的 Worker → OA 第二通道。
2. Kernel 校验身份、版本、结构、证据、lease、幂等性与状态迁移；完整语义工件通过受控 writer 原子持久化并绑定 ref/digest。
3. Run Manager runtime 负责恢复动作的选取、请求和跟踪，实际执行经 Kernel／受控动作入口；RM agent 提供按需诊断建议，其回复不是所有确定性恢复的串行前提。
4. OA、Planner、诊断角色负责原因判断、任务拆分、相关性和修复方法；OA/Planner 也承担计划可执行性、原始需求矛盾及跨任务依赖评审，按具体变化触发；不能直接改 canonical Store。
5. Supervisor 观察 Worker、恢复队列、消费者可用性与投影新鲜度，现有 watchdog 监督恢复链本身。RM 故障不能只能等待 RM 自我诊断。
6. `shadow` 意见不能自动变成正式批准或重规划授权。真实人工 gate 不能因超时而自动放行。
7. 成功派发、收到产物、结果准入、修复通过与任务完成分别记录。Worker 自检不能替代独立 Verify。
8. 操作员／发布负责人对被授权的修复落地、部署和真实验证负责；手工操作也需目标类型检查、影响预览和结果对账。监工记录与诊断不能代替恢复执行或部署生效。

### 3.1 当前路由与目标职责

当前 Python [WorkflowRuntimeCoordinator](../../src/zf/runtime/orchestrator.py)（WRC）
承担 Kernel 调度职责，`Orchestrator` 是兼容别名；配置中的 `orchestrator` role agent
是 OA，二者必须区分。正常工作流由 Kernel 推进，不以 RM agent 的回复为前置条件。
配置启用的计划采用、run plan、阶段屏障和最终收口等 OA 检查点可由 Kernel 直接请求；
例如[计划采用入口](../../src/zf/runtime/orchestrator_agent_plan_adoption.py)，无需先经过 RM。

| 范围 | 路由与判断责任 | 执行与状态责任 |
| --- | --- | --- |
| 正常 Impl → Verify → 后续阶段 | Kernel 按准入计划、策略、依赖和资源调度 | Kernel／WRC |
| 重启对账、有效结果补交和已准入阶段续作 | WRC 按现有恢复资格与冻结合同重放／对账 | WRC／WorkflowOperation 及对应 Store owner |
| 需要恢复决策的运行故障 | RM runtime 核对现状、策略与恢复前提，生成受控动作请求 | WRC／受控动作入口；不等于必须调用 RM agent |
| 未知运行／基础设施故障 | RM agent 或获准诊断角色提供假设、证据和建议 | RM runtime 接收建议，经共同准入路径请求执行 |
| 产品语义、合同、依赖与验收冲突 | OA 判断，必要时请求 Planner 局部修改 | Kernel 准入语义工件与计划变更后执行 |
| 已知且合同内可处理的实现缺陷 | 复用有效失败证据和修复方案，按获准返工策略派发 Fix | Kernel 派发 Fix → 独立 Verify；需要新判断时才升级 |

当前 resident RM agent 由 [CLI start](../../src/zf/cli/start.py) 经 [spawn_resident_run_manager](../../src/zf/runtime/run_manager_resident.py) 启动；runtime 消费其观察／建议，常驻观察不能证明逐请求闭环。
[当前失败评审入口](../../src/zf/runtime/orchestrator_agent_execution_review.py) 已生成 `auto_decide` 动作，并非每次都要 RM 模型分诊；待补强的是队列作用域、实际派发与效果跟踪。

保留逻辑职责，RM agent 只在未知运行／基础设施／harness 故障需要判断时按需介入，
请求绑定输入、预期产物和期限；可复用 provider session，不要求每次新建或持续推理。
已知机械恢复由现有策略推进，产品／合同问题交已配置 OA／Planner；不新增 RM 模型审批前置层。
RM agent 不可用时，正常流程和无需新判断的恢复继续；确需诊断时显式等待或移交。

Worker、Supervisor、操作员和计划检查点均可提供事实或请求，统一机械准入、身份、
权限、租约与去重；不要求所有入口串行经过同一 RM 队列。每个当前异常判断请求有一个
明确责任方，混合问题移交时携带原案卷、原因、证据与待回答问题；多个专家可协作，
但不让 RM agent 与 OA 无依据地重复分诊或互相等待。复用故障矩阵和动作注册，不另造 router。

### 3.2 WRC 是任务恢复的执行主体

WRC 不是 OA，也不是只管首次派发。当前实际链路为
[`run_once()`](../../src/zf/runtime/orchestrator.py) → [`_dispatch_ready()`](../../src/zf/runtime/orchestrator_dispatch.py)
→ [`prepare_task_pipeline_dispatch`／`reconcile_task_pipeline_runtime`](../../src/zf/runtime/task_pipeline_runtime.py)，
覆盖阶段重算、恢复 redrive、TaskRef 交接及后续派发；[周期扫描](../../src/zf/runtime/orchestrator_periodic_sweep.py)
还连接结果补交和生命周期检查。[attempt 对账](../../src/zf/runtime/task_attempt_runtime.py)由 WRC 的 orphan 检查调用。

| 场景 | 当前决策／准入责任 | WRC／确定性执行责任 |
| --- | --- | --- |
| WRC 重启、遗漏通知、有效结果尚未交接 | Kernel replay／结果准入 | 对账已持久化结果和阶段，继续合法 ready 集合；不重新完成已准入工作 |
| Task Pipeline pane_dead／lease_expired／provider_stop | 在线路径由 RM 决定恢复 | 校验恢复回执后，对原 operation 准入新 attempt；不能自行越过恢复决策 |
| 操作员受控 workflow resume | 现有 resume 入口按检查点与权限准入 | 通过 WorkflowOperation 恢复资格检查再续作；不得等同无条件自动重派 |
| 已准入语义失败／返工 | 当前 stage verdict 与返工策略；必要时 OA／Planner | 创建正确 operation generation，派发 Impl／Verify；不自行修改任务合同 |
| 迟到结果、取消／暂停、版本变化 | Kernel 身份／生命周期 gate | 拒绝失效结果，尊重运行意图，保护有效成果与租约 |
| candidate HEAD 的 CAS 冲突 | RM 协调诊断，candidate integrator 持有实际写入权 | 等待有效集成结果再推进；WRC 不强制覆盖候选分支 |

责任矩阵已有 [task_pipeline_recovery](../../src/zf/runtime/task_pipeline_recovery.py) 及
[fault matrix 测试](../../tests/test_task_pipeline_fault_matrix.py)；另有
[受控 workflow resume](../../src/zf/runtime/workflow_resume_apply.py) 与 [恢复测试](../../tests/test_workflow_operation_resume.py)。
Task Pipeline operation-v2 与 legacy/shadow 路径不能混用；逐入口核验，不重新建立恢复控制器。
本轮补强重点是 RM 与 WRC 的交接效果、已有结果优先、重启重放及并发互斥；尤其要验证
在线 RM 与操作员 resume 同时处理同一 attempt 时只有一次有效 redrive。测试存在不代表此竞态已覆盖。

## 4. 当前恢复机制与目标机制

### 4.1 当前路径及本次断点

```text
Worker failed / blocked
          |
          v
Kernel: admit result + settle operation
          |
          v
Existing recovery / rework policy
          |
          +---- known retry -----------> Worker
          |
          +---- semantic / contract / exhausted
                         |
                         v
                 RM pending action
                         |
                         v
                 Select next action
                         |
            +------------+-------------+
            |                          |
       Gate allows                Old run gate
            |                          |
            v                          v
      OA / Discovery          next_operation = null
            |                          |
            v                          v
      Repair / Plan delta       QUEUED, NOT DISPATCHED
            |
            v
      Kernel admission
            |
            v
      Worker -> Verify
```

图中仅描述本次失败恢复路径，不包含正常工作流或 Kernel 直接发起的 OA 检查点。
这不表示所有恢复都经过 OA，也不表示现有系统没有超时或恢复案卷。
只加强 OA 提示词无法修复请求未被派发的问题。

### 4.2 改进后的路径

```text
Worker report                  Supervisor detects crash/stall
      |                                      |
      +------------------+-------------------+
                         |
                         v
       WRC / Kernel: admit facts + reconcile current state
                         |
       +-----------------+--------------------+
       |                 |                    |
       v                 v                    v
 Replay / admitted   Recovery decision    Semantic decision
 stage continuation     required             required
       |                 |                    |
       |                 v                    v
       |             RM runtime -------> OA / Planner
       |           policy + tracking     configured checkpoint
       |                 |                    |
       |            approved action      admitted repair / delta
       +-----------------+--------------------+
                         |
                         v
       WRC / controlled owner: recheck identity + lease + gate
                         |
                         v
        Resume stage / Fix / Verify / scoped wait
                         |
                         v
           Fault-specific effect verification
           Code repair: independent Verify
                         |
             +-----------+-----------+
             |                       |
             v                       v
      Failure eliminated       Failure persists
             |                       |
             v                       v
       Close recovery case     Update diagnosis
       Continue task flow      Preserve case history

 Supervisor / watchdog observes every handoff above.
 Request delivery is not proof of fault resolution.
```

图中 RM 的运行异常分支不接管 WRC 已具备的确定性续作；原案卷仍关联需跟踪的故障。
外部等待只有在指定事件到达并经过校验后才能进入下游。已存在且仍适用的分诊结论
可以复用，不要求 RM、OA、Planner 对同一问题重复诊断。已知的合同内实现缺陷可走
Fix → Verify；未知运行故障先请求 RM agent／诊断角色形成方案，再回到受控动作准入。
普通实现修复不必重规划；真正影响合同、依赖或验收责任时，才生成并审查相应计划差量。

## 5. 失败分类与恢复条件

分类同时记录原因与发生阶段。Verify 环境故障不能直接计作产品实现失败。
已知机械错误码可确定性路由；根因、业务含义和修复方法由 Agent 判断并提供证据。
未知原因进入诊断，避免凭文本关键词猜测并直接重试。

| 类型 | 例子 | 处理者与恢复方式 | 再次执行前的条件 |
| --- | --- | --- | --- |
| Provider／传输中断 | 断连、进程退出、限流 | RM 调用现有恢复动作，按策略退避重派 | 旧 attempt 隔离，session、工作区与 lease 有效 |
| 外部执行结果未知 | 远端可能已受理或执行，但成功响应丢失 | RM runtime 请求核对原操作，必要时诊断；按确认结果继续、等待或重试 | 外部状态及副作用归属已核实；未知期间不得把缺少本地回执当成未执行，详见 B01 |
| 环境／工具故障 | 数据库未就绪、依赖缺失、磁盘不足 | 环境修复 Worker，RM 跟踪效果 | 原故障探针通过 |
| 输入／协议错误 | 工件格式错误、引用失效、不存在的原始聊天被列为必读 | 输入生产者或 harness 修复者定向处理 | 必需输入可解析且版本一致；仍缺失的内容明确 |
| 实现缺陷／任务未完成 | 测试失败、页面骨架未补齐 | 已有有效方案时按策略派发 Impl/Fix；需要产品语义判断时交 OA | 有失败证据、修复范围与验证方法 |
| 任务合同／计划问题 | allowed_paths 内无法验收、循环依赖、阶段要求错位 | OA 判断，必要时 Planner 局部修改 | 新合同及计划通过准入，受影响任务重新绑定 |
| 真实人工／外部依赖 | 正式负责人、业务决定或外部凭据待提供 | 建立有责任人的作用域等待 | 指定决定或外部证据到达并校验 |
| 未知／证据冲突 | 无法复现、报告与日志矛盾 | 运行故障交 RM agent／诊断角色；产品语义交 OA，必要时获准 Autoresearch 深诊断 | 可验证假设，或明确的缺失信息与下一责任人 |
| 恢复角色自身故障 | RM/OA 请求无应答、诊断调用卡住 | Supervisor／runtime 按请求事实触发受控恢复，不等待失效 agent 批准 | 隔离旧 attempt，明确替代消费者或等待出口；无关工作继续 |

必须允许报告“输入本身不可获取”这类有证据的失败，不能要求先读完无法取得的
材料才能提交故障事实。该准入仅承认输入故障，不授予完成、跳过验证或放宽合同的权限。

### 5.1 最小恢复范围与验证对象

| 异常 | 恢复位置 | 对应故障消除的证据 |
| --- | --- | --- |
| Provider／传输中断 | 隔离旧 attempt 后恢复原 operation 检查点，资格允许时复用 session | 新 attempt 实际承接原阶段并产生有效进展；进程启动本身不足以完成业务任务 |
| 数据库、依赖或磁盘故障 | 修复环境后继续原检查点 | 原环境探针通过；代码任务继续履行独立 Verify |
| 产物格式、ref 或 TaskRef 交接失败 | 修复失效输入／交接，保留仍适用的实现提交 | 正确版本的产物被准入并交付下游；不默认重做 Impl 或完整 plan/critic |
| 产品代码缺陷 | 针对失败范围派发 Fix | 确切提交上的原失败用例与影响范围回归通过 |
| 合同或计划无法执行 | 准入局部计划差量，重新绑定受影响任务 | 新合同可执行、必要评审通过、旧成果适用性明确；无关任务不重跑 |

现有 WorkflowOperation 记录一次请求、调用或执行的生命周期；RecoveryCase 关联
原始故障及跨动作恢复历史。某次操作完成可以发生在故障消除之前：发出 OA 分诊请求
只完成交接动作，案卷仍等待诊断、修复和对应效果证据。沿用已有事件与投影表达这些
阶段，不另建第三套 canonical 状态机。故障案卷关闭也不等于 Task 满足全部验收条件。

## 6. 再次派发前的修复说明

扩展现有恢复 proposal／工件，提供以下信息；这里是语义内容要求，不新建 Task schema：

| 内容 | 要求 |
| --- | --- |
| 失败对象 | run、task、阶段、operation、attempt、代码提交、合同与输入版本 |
| 失败证据 | 失败命令、日志、验收缺口或环境探针；不可复现时明确标记 |
| 原因判断 | 区分已证实原因、待验证假设及其证据 |
| 本次变化 | 修改什么、保留哪些成果、责任角色、允许修改路径、已尝试策略 |
| 验收方法 | 原失败探针、相关回归和必要集成验证；绑定目标提交与有效环境 |
| 后续出口 | 验证失败后重新诊断、局部改计划或转有责任人的等待 |

分开检查两个时点：

1. **派发修复任务前**：修复方案可检验，输入和权限足够，工作区与租约可用。不能要求此时已经修好代码。
2. **恢复原阶段前**：阻塞前提已经改变，例如环境探针通过、输入重新准入、合同修订生效。

同一根因下，若代码、输入、环境、合同和修复策略均未变化，不再重复派发相同指令。
瞬时故障允许有依据的退避重试；未知故障允许针对假设进行诊断，不伪装成已知修复。

计数沿用现有 attempt ledger 与 recovery case，分别核算传输、协议和语义失败。
operation generation 保留身份作用，不能直接替代同根因失败计数；跨计划版本保留
问题关联，防止靠换 task ID 或重复 replan 重置无进展历史。

Verifier 针对修复后的确切提交检查原失败条件及影响范围回归。旧提交证据仅在
可证明适用时引用。原失败消除只关闭对应恢复案卷；Task 完成仍需满足其完整合同。
发出 triage/discovery 请求可以证明交接动作完成，不能据此认定业务故障已消除。

## 7. 角色输入、输出与交接

每个已接收请求必须有当前责任人、预期产物、下一接收方以及超时出口。
无待办时允许 idle；有待办时不能只用进程存活表示正常。

| 角色 | 输入 | 必须输出 | 下一消费者 |
| --- | --- | --- | --- |
| WRC／Kernel | 有效合同、operation／attempt／lease、已准入结果与恢复决策 | 阶段对账、受控重派或明确拒绝／等待；实际派发与交接事实 | 当前阶段 Worker、下一阶段或需介入的恢复入口 |
| Impl／Fix | 固定版本合同、必要上下文、失败证据、修复说明 | 候选提交及证据，或结构化失败／阻塞原因 | Kernel 准入后交 Verify 或恢复路由 |
| Verify | 确切提交、验收条款、原失败探针 | 独立结论与证据，区分产品失败和验证环境故障 | Kernel 判定下一步 |
| OA | 当前失败、相关历史、合同、已尝试方案，或待审计划的可执行性与需求矛盾证据 | 可执行修复、计划调整、诊断或等待建议；标明正式决策或 shadow | 受控动作准入与执行 |
| Planner／Discovery | 原始需求、当前计划问题、现有成果与受影响范围 | 可准入的计划差量、需求映射、阶段验收责任和依赖理由；无法修改时说明原因 | 计划准入及相关任务绑定 |
| RM agent | 绑定请求的运行故障案卷、候选动作与约束 | 带依据的结构化建议，或明确需要谁提供什么信息 | RM runtime 校验并经共同准入路径请求执行 |
| RM runtime | 需要异常协调的事实、策略、队列、消费者能力、容量与 gate | 动作请求、明确等待或失败记录；跟踪消费者接收、修复效果及 WRC 实际续作 | WRC／受控动作入口及其执行角色 |
| Supervisor／watchdog | 排队时长、lease、应答、进展、消费者与投影新鲜度 | 可定位的超时／失联／能力不可用事实与恢复请求 | 现有恢复入口；不代替业务验收 |
| 操作员／发布负责人 | 授权范围、修复提交、验证结果、进程版本与临时缓解记录 | 类型与影响预检、合入/部署/加载回执、真实验证结果及临时措施退出记录 | 受控发布/恢复入口与独立验收 |

交接应区分请求建立、资源预留、provider 应答、执行中、产物收到、结果准入、
效果验证。以上是观察点，不要求另起一套状态枚举；映射到现有 operation 和事件。
内部 reserve 成功不等于 provider 已接收请求。

错误产物需要把准确的拒绝原因送回生产者并有界修复。修正 Planner 输出格式时，
不无条件重新读取全项目并重跑完整 plan/critic；涉及需求或合同变化时仍执行必要评审。

## 8. 等待、超时与公平调度

### 8.1 等待合同

等待至少记录责任人、缺失项、阻塞作用域、来源证据、唤醒事件、下次复查时间和
未满足时的出口。外部响应没有保证时，如实保持等待并按策略提醒，不因时间经过而放行。

- 任务级等待只阻止相关任务、动作与依赖；真正的 run 级暂停继续有效。
- 旧任务被 superseded 不自动取消关联业务决定；需核查因果关系，经受控动作关闭、迁移或缩小旧等待作用域。
- 缺少身份的历史 gate 进入明确的对账路径，不靠删除事件、改投影或猜测 task_id 恢复。
- 已声明可并行的无关工作继续推进；只读诊断是否可执行也由授权和 gate 语义决定。

### 8.2 调度与监督

1. 在有效 gate 和容量约束下扫描可执行动作，避免一个不可执行队首阻塞无关动作。
2. 按现有 recovery case、operation 与 lease 去重；重派前隔离旧 attempt，拒绝其迟到写入。
3. 为恢复判断、OA 和 Verify 保留调度机会，防止全部容量被 Impl 占用；容量不足必须显示原因。
4. 排队、provider 应答、执行无进展、产物未准入分别计时。请求尚未派发时也必须可诊断。
5. 心跳、重复读页、无关任务完成不自动延长本案卷的无进展期限。长工具调用可以用绑定本 operation 的执行证据解释阶段耗时。
6. RM 的监督复用已有 Supervisor/watchdog，避免故障恢复依赖同一个已失效的 agent。慢诊断通过已有 operation 服务异步执行；监督路径不能被所监督的调用阻塞，当前同 tick 路径的缺口见 A05。

排队、provider 应答和阶段进展分别使用现有配置可表达的期限；缺项先补配置合同，
按角色、环境和实测耗时校准，不把 LS 的观察间隔直接提升为全项目默认 SLA。
期限到达触发核对／诊断，不自动放行 gate 或终止正常长调用；明确暂停与真实外部等待
不算正在执行超时。持续故障按策略退避并保留责任，避免诊断和重启相互循环。

## 9. 断点恢复与上下文完整性

复用已有 WorkflowOperationService 和恢复案卷，从事件与 canonical Store 恢复
当前责任人、已完成交接和下一动作；投影可重建，不能成为第二份状态真相。

- 控制面重启后对账请求、租约、产物和已发生副作用；身份与幂等键防止重复派发或重复应用。
- 事件读取包含日志轮转后的历史分区，不能因为当前文件没有旧事件就认定交接不存在。
- 保留已有提交、有效证据和恢复检查点；只重做失效或未完成的阶段。
- 按 198 固定任务输入版本，实际产出并消费 `plan_input_selection`，交付共享约束、任务切片、失败证据与必要变化。
- 语义覆盖由生产者与评审证明；runtime 校验绑定、完整性与权限。缺少选择时明确回退及成本，不偷偷缩减必读。
- 原始来源、全局矩阵与完整证据继续保留；Candidate/Goal 的全局验收范围不因任务切片缩减。
- session 仅在身份、合同、工作区和恢复资格允许时复用；旧 session 或历史已读回执不证明当前记忆完整。
- 沿用 198 §8：同 attempt 同版本继续 cursor；新消费者或上下文保留未知时补齐其恢复必读。不得把本节解释为每次恢复重读整个项目。

## 10. 第二轮复盘的 15 项补充设计

R01–R15 是本文的设计追踪编号，不是运行时 Task ID 或新事件类型。每项保留
证据性质、责任和目标行为；具体修复应先对当前待部署基线复现，避免重做已解决问题。

### 10.1 R01：修复代码的部署与生效验收（P0）

**证据：已发生，已有部分基础。** Web、控制面及操作员进程曾运行不同基线；
隔离测试通过不能证明常驻控制面已加载修复。Web 已有启动版本信息，不能描述为完全无版本观测。

操作员／发布负责人按交付范围记录“已提交 → 已合入 → 已部署 → 进程已加载 →
真实验证”，绑定提交、进程启动身份、配置与指令版本。RM 跟踪修复对故障的实际效果。
每个环节分别标记未完成、失败或证据不足，不能用 Web 刷新代替控制面加载证明。
复用 [Web health 信息](../../src/zf/web/server.py) 和
[现有重启执行器](../../src/zf/runtime/source_repair_restart_actuator.py)，不另建发布状态真相。

### 10.2 R02：候选计划与执行计划隔离（P0）

**证据：历史事故，已有局部修复。** 未批准的新候选曾使旧包被视为 superseded，
旧 Worker 读取随即失效；新代际也曾从未包含既有实现的基线启动。

Kernel 区分产物准入、计划批准与执行启用。发布候选不撤销当前执行依据；正式切换
要确定受影响任务、有效既有成果和工作区基线，在安全检查点迁移并拒绝失效 attempt
的迟到写入。无关任务继续使用合法绑定。兼容性由语义评审给出，身份和切换由机械规则校验。
复用 [计划包准入](../../src/zf/runtime/plan_artifact_package_runtime.py) 与
[执行代际上下文](../../src/zf/runtime/task_pipeline_contexts.py)，不直接改写旧包或投影。

### 10.3 R03：成功实现之后的交接恢复（P0）

**证据：已发生，已有专门入口。** FMS-01 的 TaskRef 恢复成功前，新 Impl 已被派发，
修复后仍继续重复读取。代码、交接证据与最终合同是否满足必须分别判断。

RM 识别失败发生在实现、结果协议、TaskRef、集成还是发布。原结果仍适用时仅恢复
失败环节；合同变化或实现缺口仍需重新实施与验证。交接修复和替代 Impl 派发在执行前
对同一任务/operation 对账，防止竞态。复用 [TaskRef 修复入口](../../src/zf/runtime/task_ref_repair_operation.py)，
交接成功后重新计算下一阶段，而不是无条件回到 Impl。

### 10.4 R04：消费者不可用时的请求收尾（P0）

**证据：已发生。** Autoresearch 已禁用，但历史请求没有终结结果，仍占用 inflight。
现有归并依据事件生命周期，已有特殊回退并不能证明禁用后的所有路径已闭环。

RM 在请求前检查路由是否注册、配置是否启用及消费者可用性。禁用、移除或启动失败
须产生符合实际情况的不可用、拒绝或取消结果，并释放对应的占用；保留历史请求和原因。
替代路线必须在已有授权内。不能为了清空队列伪造诊断成功，也不能擅自开启被禁用服务。
真实执行已开始的请求，应先对账其进程和结果，再决定终结或迁移。

### 10.5 R05：分阶段验收责任与证据含义（P0）

**证据：已发生。** E2E-01 曾同时关联基础 fixture 检查和最终真实认证、授权、浏览器验收。

ZaoFu 的通用 [执行准备方法](../../skills/zf-plan-execution-readiness/SKILL.md)与 Verify 方法为验收条款对账阶段、环境、命令、负责人、目标提交及可观察证据。
不同验收含义不能依靠同名命令相互替代。开发自检可以独立通过，后置的真实集成义务
必须保留明确 owner 和依赖；不能全部前置阻塞开发，也不能因“稍后集成”而遗失。
Kernel 校验绑定和证据结构，证据是否证明目标行为由独立语义验证判断；fixture 是否获准由项目合同决定，本文不替 LS-v1 修改认证集成计划。

### 10.6 R06：审核摘要与真实准入规则一致（P0）

**证据：当前源码可复现摘要含义混淆。** 两个不同根文件会同时出现“根 owner 不唯一”
与“路径无重叠”。这不是同文件冲突的证明；根 owner 政策若独立存在，也不能直接删除。

CLI/Web 审核视图区分实际冲突、政策限制和提示，给出规则来源、作用域、证据与
实际准入结果。机械判定优先复用准入方的结构化结果，避免摘要自行形成第二套 gate。
复用 [plan_digest](../../src/zf/runtime/plan_digest.py) 与
[plan review 消费者](../../src/zf/cli/plan_approval.py)；澄清语义后再决定合并哪些检查。

### 10.7 R07：任务可执行性与关键路径评审（P1）

**证据：已发生，不能直接归因于并发配置。** 配置 16 条 lane 后初始仅 1 项 ready，
FMS-01 完成后直接可执行 3 项，大量任务继续依赖 FMS-02。

ZaoFu Planner/OA 方法在批准前报告依赖理由、关键路径和配置容量下的可执行范围，区分开发、
装配与最终验收依赖。在项目合同允许时提出稳定接口或 fixture 拆分，核对共享文件归属。
运行中由具体无进展或依赖变化触发局部评审；不为填满 lane 直接删除依赖或降低最终验收。

### 10.8 R08：原始需求矛盾与语义反转检查（P1）

**证据：已发生。** FMS-16 原稿要求保留安装历史，计划却写为删除，测试又要求保留。

扩展 ZaoFu [需求对账方法](../../skills/zf-requirement-reconciliation/SKILL.md)，由 Planner/OA 对原稿、PRD、AC 与测试进行语义对账，重点检查删除/保留、允许/禁止、
开发/生产等相反要求，形成引用原文和明确结论的评审证据。来源链接与覆盖行数只是定位基础。
批准后的合法变更保留版本和决定，不反复按旧要求误报；领域判断留在方法与评审工件中。

### 10.9 R09：Skill 实际消费与产物符合性（P1）

**证据：已有摘要能力，实际生效仍需核验。** 方法多次更新，但当前计划仍未声明
`plan_input_selection`，也未证明所有角色使用了修订版本。

沿用 [简报中的指导摘要](../../src/zf/runtime/injection.py)，关联配置、实际交付版本和
可获得的消费证据；没有消费证据时显示未知。生产者工件与独立评审再证明方法是否落实。
读过 skill 不证明遵循方法；不能靠继续叠加同义提示词或虚报已读补齐证据。

### 10.10 R10：暂停、停止与恢复的明确语义（P1）

**证据：需要明确操作合同。** 目标 blocked 后仍有已派发 Worker 读取，这本身不证明
停止机制错误；停止新派发、终止在途执行和暂停监工的目的不同。

控制动作明确选择停止监控、停止新派发、等待在途任务、检查点暂停或终止执行。
操作前展示影响，操作后列出仍运行的角色、已保留工作、lease 与恢复入口。
已执行副作用和未准入结果要对账，不能只杀进程或直接改成 idle。这里描述行为，不新增未经实现的 CLI 命令。

### 10.11 R11：评审触发与操作权限矩阵（P1）

**证据：已发生，已有局部防护。** 操作员曾把 shadow revise 错绑为正式 return_to_plan，
随后自动重试沿用错误绑定，恢复未能启动。

对格式修复、局部代码修复、合同调整及全图变更分别规定评审主体和影响范围。
受控入口提前核验引用类型、授权、目标版本与动作影响；正式权限不能由 shadow 推导。
复用仍有效的评审，失效部分重新评审；不能每次都完整重跑 plan/critic，也不能按优化名义绕过必要批准。

### 10.12 R12：进度投影与执行事实对账（P1）

**证据：已发生。** 看板 in_progress 曾包含已经没有活动 operation 的阻塞任务，
历史取消任务也影响完成数的理解。

投影展示计划版本、有效任务总数、实际活动 attempt、执行阶段、阻塞原因和快照时间。
区分读取、实施、验证、集成；计划变化解释分母变化。用同一事件范围和版本对账相关视图，
数据滞后或来源不一致明确显示。修复 canonical 状态须走其受控 owner，不能靠手改投影掩盖不一致。

### 10.13 R13：公共故障的跨任务协调（P2）

**证据性质：已有 incident 投影，协调效果待验证。** 多任务受同一数据库、Provider
或输入包影响是需压测的场景，不宣称已观察到完整的多任务故障风暴。

Supervisor/RM 根据共同资源和原因证据关联故障，选定一个修复责任方，其他任务保留
自己的恢复和验收条件。修复公共原因后分别重验受影响任务；不因错误文本相同就合并问题。
复用 [incident 投影](../../src/zf/runtime/run_incident_projection.py) 与恢复案卷关联，避免新增状态权威。

### 10.14 R14：配置容量下的资源与证据隔离（P0 基础／P2 压测）

**证据性质：待压测。** 当前低并行主要受依赖图限制，不能据此认定资源瓶颈；
验证期间脚本被修改的 Worker 报告也不足以证明完整的跨 Agent 冲突原因。

WRC/Kernel 与验证环境维护者核对 worktree、Compose 项目、数据库、端口和证据目录
的运行/attempt 归属。Verify 使用固定提交及可解释的环境，防止边修改边验证造成证据漂移。
分别压测正常执行、集中失败和控制面重启，观察 Provider 限流、CPU、内存、磁盘与恢复队列。
归属与写入隔离属于 P0；高负载压测属于 P2，16 路只是 LS-v1 的测试样例。
资源约束统一走配置和已有调度，不固定 lane 数或把吞吐测试变成费用／任务总量上限。

### 10.15 R15：临时缓解退出与可复现交付（P2）

**证据：已发生。** 曾使用本地 Git exclude 和手工接回提交恢复任务；设计文档等
新文件还可能受仓库忽略规则影响，只有本地文件而未进入交付版本。

操作员／发布负责人记录临时措施的作用域、原因、负责人、退出条件和恢复方法。
ZaoFu 通用修复落地后，在干净 worktree 核验无需隐藏的本地补丁；交付前对账代码、设计文档、
skill 源与分发副本、配置和证据引用。退出缓解、删除 worktree 前证明成果可追溯且无活动执行。
不得通过删除未交付工作、忽略所有脏文件或扩大排除规则来制造干净状态。

## 11. 第三轮路由核查与五项发现

本轮沿用 `cff2a8d1`，依据源码、既有测试和两个纯内存构造事件复现。
未向真实 LS 注入故障，未执行真实 Provider 验证，也未据此认定五项都是现场阻塞根因。
A01–A05 是本文的架构核查编号，补充 R01–R15，不是新的 runtime Task ID 或状态 schema。

### 11.1 A01：resident 活性缺少逐请求义务（P0）

**核实程度：代码确认，投影行为已纯内存复现；真实请求超时链路待验证。**
[run_manager](../../src/zf/runtime/run_manager.py) 的
`build_resident_agent_projection` 找不到最新 prompt 之后的应答时，会回退到任意
历史 observation/recommendation，以避免日志轮转造成误报；同时固定 `stalled=False`。
而 `_pending_resident_agent_actions` 只消费 `status=stalled`，该生产者不会产出此状态。

构造“30 分钟前 spawn、25 分钟前 observation、20 分钟前新 prompt 未应答”的事件，
结果仍为 `observing`、`stalled=false`，该 pending-action 函数返回空列表。
[现有 resident 测试](../../tests/test_run_manager_resident.py) 的
`test_run_manager_projection_treats_resident_silence_as_normal_wait` 和
`test_run_manager_projection_uses_archived_observation_when_prompt_is_newer`
明确保护空闲静默及历史观察回退，因此不能简单改成“长时间没有 observation 就故障”。

改进应绑定当前 request、输入版本、接收方、预期输出和期限；历史观察只证明过去
响应过，不能履行新请求义务。只有确有未履行请求时才触发排队／应答超时。
已有其他 reprompt、死 pane 重启和 triage 回退入口仍然存在，本发现不等于所有唤醒失效。

### 11.2 A02：分诊动作回执可能提前关闭故障案卷（P0）

**核实程度：请求生成与消费路径已定位，案卷投影行为已纯内存复现；真实闭环影响待验证。**
[rework_triage](../../src/zf/runtime/run_manager_rework_triage.py) 的 `_request_action`
对 `orchestrator-rework-triage` 只要求 `TRIAGE_REQUESTED` 下游事件，未声明 effect 条件。
[run_manager](../../src/zf/runtime/run_manager.py) 的
`_execute_orchestrator_triage_request` 发出请求和 applied，再调用 `_post_verify_action`；
后者可因请求已发出而记录 `run.manager.action.verify.passed`。

[recovery_case](../../src/zf/runtime/recovery_case.py) 的
`build_recovery_case_projection` 对没有 effect 条件、且不属于特定例外的
verify.passed 将案卷置为 `resolved`。构造同案卷的 planned → applied → verify.passed，
动作是 `orchestrator-rework-triage`、期望事件仅为分诊请求，投影即为 resolved，
无需产品修复证据。`semantic-replan-request` 等已有例外，不能说所有恢复都如此。

发送请求作为动作成功是合理的；它不能证明关联的原始故障消除。应按 §5.1 区分
交接完成与故障消除，并把后续诊断、修复、效果证据关联到同一问题。
复用现有 effect 跟踪和案卷，验证环境／传输修复时也不能一律要求业务代码测试。

### 11.3 A03：RM 的等待方法缺少证据适用性约束（P1）

**核实程度：提示词确认；模型是否因此作出错误决定尚未复现。**
[resident briefing](../../src/zf/runtime/run_manager_resident.py) 的 Decision Discipline
要求任一条件命中就建议 wait：任务存在“不论新旧”的完成证据、fanout 兄弟 child
仍在运行、同 checkpoint 曾出现 `workflow.resume.gate_unroutable`。
这些规则意在抑制重复恢复，但没有充分限定当前 run/task、合同、提交和失败身份。
[对应测试](../../tests/test_run_manager_resident.py) 的
`test_resident_briefing_contains_wait_semantics_rules` 主要检查上述措辞存在。

旧 test.passed 不自动否定新合同或新提交上的失败；兄弟任务推进不证明当前 child
仍正常；配置或依赖修复后，旧 unroutable 结论需要重验。应先在 skill／prompt 中
明确适用性核对方法，runtime 检查可机械验证的版本绑定与动作前提；补充真实行为
场景，而非继续用字符串存在证明方法正确。正常 fanout 等待仍应保留。

### 11.4 A04：恢复建议与 pending action 的准入路径需要对齐（P0）

**核实程度：分支差异确认；尚未证明存在最终权限绕过。**
[run_manager](../../src/zf/runtime/run_manager.py) 在选择 continuation 之前调用
`_consume_agent_recommendations`，其 controlled_action 分支经过 policy 判断后
可直接调用 `_execute_controlled_run_action`；常规 pending action 路径则在执行前
显式调用 `recovery_action_admission`。建议路径已有 policy/preflight 等检查，
不能仅凭调用顺序就宣称绕过 Kernel 授权或证明 `auto_execute=false` 无效。

应验证同一动作从不同入口进入时，对 gate、作用域、版本新鲜度、案卷归属、lease、
幂等键和已有执行的检查结果等价；把共同机械准入收敛到可复用入口，避免某一分支
只拥有部分防护。Worker／Supervisor／操作员均可提交意图，最终副作用仍由原有 owner
执行。无需把正常工作流或 Kernel 直接 OA 检查点迁入 RM 恢复队列。

### 11.5 A05：watchdog 与慢诊断共用同步 tick 路径（P0）

**核实程度：调用链确认；实际 LS 是否因此停滞尚未验证。**
[EventWatcher](../../src/zf/runtime/watcher.py) 在轮询循环同步调用 `on_tick`；
[CLI start](../../src/zf/cli/start.py) 的回调调用
[run_standard_tick_services](../../src/zf/runtime/tick_services.py)，其中先运行
watchdog，再运行 RM。RM 在 tick 锁内可经 `_maybe_invoke_run_manager_reflect`
同步调用模型，配置 timeout 默认 180 秒。该诊断路径是否执行受配置和请求影响，
不能据此断言现场已经启用 reflect，或把全部历史长等待归因于它。

如果这次回调阻塞，处于同一轮询路径的 watchdog 无法在阻塞期间继续观察；
[现有 watchdog](../../src/zf/runtime/run_manager_watchdog.py) 能检查旧时间戳或死 pane，
并不证明它与被监督调用有独立执行能力。

将慢模型／工具调用交给已有异步 operation 执行，tick 只做有界检查、请求和结果消费，
避免持有协调锁等待模型。watchdog 必须在被监督调用超时期间仍能运行；整个控制面
进程失效则需要明确的外部进程监督边界，不能等待 RM agent 自救。这里是设计要求，
并不表示当前已经部署独立进程守护；需用阻塞注入与宿主测试证明。

## 12. 外部调研对照：LangGraph 与 Deep Agents

### 12.1 来源、核查范围与采纳边界

调研输入为 `/tmp/orchestrator_retrieval_recovery_summary.md`（2026-11-06），本节保留独立可读的结论与来源。
核查基线为 ZaoFu `cff2a8d1`、Deep Agents `70dfbd597ae3d1ac348f6c46892464868f24b070`（SDK `0.7.34`）。
前轮 LangGraph 结论仅来自官方文档；同日后续已核对本地源码 `5847733bcb186c26b32da5cc822eaf8a89deffeb`（库 `1.2.20`）。
本轮阅读两库的重试／错误处理、检查点恢复、子任务通知和相关测试；未执行这些测试或真实故障注入，来源提交不代表 ZaoFu 已集成该能力。

采纳分层恢复、局部检索和持久化交接方法，按以下边界适配 ZaoFu：

1. LangGraph runtime 执行配置的重试与 error_handler；Deep Agents 父 Agent 判断失败后续方案。ZaoFu 沿用 WRC／RM runtime 与恢复案卷，OA 判断产品语义、RM agent 按需诊断运行故障，不增加第二份状态真相。
2. 单次超时、退避和无进展诊断保留局部责任，不恢复已取消的总量／费用上限；无关且获准的工作继续。
3. Worker 在合同内检索、自检；独立 Verify 与最终准入保留，实质变更才请求相应计划调整。
4. 复用工具和诊断角色；可选 Recovery Agent／`CompiledSubAgent` 的存在不证明恢复必然被触发，仍须 runtime 消费者与调用方测试。

### 12.2 六项可借鉴机制与现有实现对照

B01–B06 是本节的设计对照编号，不是新增 Task ID；“已有基础”不表示全部入口已覆盖。

| 编号／优先级 | 外部机制与证据 | ZaoFu 已有基础 | 补强要求与关联 |
| --- | --- | --- | --- |
| B01／P0：结果未知与副作用核对 | LangGraph 区分确认丢失和确认失败，要求稳定外部幂等键及状态核对，见下方容错来源 | [WorkflowOperation](../../src/zf/runtime/workflow_operation.py) 有稳定 continuation 幂等键；[Channel 受控入口](../../src/zf/runtime/control_actions_channel_msg.py) 已拒绝 unknown delivery 下的新 synthesis | 核对各执行入口是否具备相同保护；幂等身份在副作用前可恢复，确认远端是否受理／完成后再决定动作。本地去重不自动等于外部 exactly-once，关联 A04 |
| B02／P0：事件通知与有界对账配合 | Deep Agents 异步角色在父级空闲时尝试唤醒；父级运行期间主动查询，并识别旧 run 通知，见下方异步源码 | [wake_patterns](../../src/zf/runtime/wake_patterns.py) 和 [periodic sweep](../../src/zf/runtime/orchestrator_periodic_sweep.py) 已有事件唤醒与周期恢复 | 检查通知遗漏、父级重启和迟到结果；每次交接同时有通知与对账出口，父级人工暂停仍有效。慢查询不能阻塞 watchdog，关联 A01/A05 |
| B03／P0：并行成果分别持久化 | LangGraph `_reapply_writes_to_succeeded_nodes` 在检查点恢复时复用成功节点的 pending writes，见下方循环源码 | [reader 结果恢复](../../src/zf/runtime/orchestrator_fanout.py) 及[结果先于恢复的测试](../../tests/test_reader_result_before_recovery.py) 已覆盖部分场景 | 对 Impl、Verify、OA 等入口分别核验；保留已准入且输入／目标版本仍适用的成果，仅恢复失败或未完成步骤，关联 R02/R03/R14 |
| B04／P0：恢复处理自身可断点继续 | LangGraph 先持久化失败及处理器归属；`_resume_error_handlers_if_applicable` 恢复处理器，相关测试断言原失败节点仅执行一次 | [RecoveryCase](../../src/zf/runtime/recovery_case.py)、WorkflowOperation 与事件已提供关联基础 | 先持久化失败、请求和当前处理器步骤；诊断／修复角色中断后沿原案卷恢复未完成步骤，已完成子步骤／副作用先对账；只有有效决定要求重试原阶段时才重派，不清零历史或重复分诊，关联 A01/A02 |
| B05／P0 基础／P1 方法：任务义务独立于聊天历史 | Deep Agents 使用独立 `async_tasks` 状态字段保存 task/thread/run 身份及状态，见下方异步源码 | Task、operation 及[attempt 输入索引](../../src/zf/runtime/artifact_input_index.py) 已独立于 provider 对话 | P0 保证请求身份、责任与期限可恢复；P1 优化诊断上下文重建。当前请求、待交付产物及已尝试方案来自事件、Store 与必要 sidecar，不能由摘要或会话替代，关联 198、A01 |
| B06／P1：方法与策略在执行角色生效 | Deep Agents 默认 isolated 子 Agent 不继承主 Agent 自定义 middleware，fork 模式另有继承规则，见下方装配源码 | [worker envelope](../../src/zf/runtime/worker_execution_envelope.py) 绑定执行 profile、skills、身份及结果要求 | 核对实际 Worker 的工具权限、错误反馈和局部修复方法；区分“配置已更新”“本次已交付”“行为已验证”，关联 R09 |

B01 的自动对账覆盖 ZaoFu 控制且可绑定身份的副作用。Worker 任意 shell／第三方业务操作
不能据此承诺 exactly-once；缺少幂等键或查询接口时保留 unknown 并交获准诊断／人工处理。

外部来源按职责绑定；GitHub 链接固定核查提交，官方文档内容可能随版本变化：

- [LangGraph：重试](https://github.com/langchain-ai/langgraph/blob/5847733bcb186c26b32da5cc822eaf8a89deffeb/libs/langgraph/langgraph/pregel/_retry.py)与[执行器](https://github.com/langchain-ai/langgraph/blob/5847733bcb186c26b32da5cc822eaf8a89deffeb/libs/langgraph/langgraph/pregel/_runner.py)：运行时执行匹配策略，失败后路由已配置处理器，无需默认管理模型审批。
- [LangGraph：检查点循环](https://github.com/langchain-ai/langgraph/blob/5847733bcb186c26b32da5cc822eaf8a89deffeb/libs/langgraph/langgraph/pregel/_loop.py)及[恢复测试](https://github.com/langchain-ai/langgraph/blob/5847733bcb186c26b32da5cc822eaf8a89deffeb/libs/langgraph/tests/test_retry.py)：B03/B04；`test_error_handler_resumes_after_crash` 验证处理器续作，不重跑原失败节点。
- 前轮官方文档：[Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)支持 B01 的外部幂等／确认丢失边界；[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)说明 pending writes。检查点不保证外部副作用只发生一次。
- [Deep Agents：异步子任务](https://github.com/langchain-ai/deepagents/blob/70dfbd597ae3d1ac348f6c46892464868f24b070/libs/deepagents/deepagents/middleware/async_subagents.py)：B02/B05 的状态、通知、查询与旧 run 过滤；`_notification_body` 让父 Agent 决定后续。[官方运行条件](https://docs.langchain.com/oss/python/deepagents/async-subagents)说明服务依赖。
- [Deep Agents：角色装配](https://github.com/langchain-ai/deepagents/blob/70dfbd597ae3d1ac348f6c46892464868f24b070/libs/deepagents/deepagents/graph.py)：调用 `create_agent` 并装配中间件，B06 区分 isolated／fork 方法继承；[官方子 Agent 说明](https://docs.langchain.com/oss/python/deepagents/subagents)解释适用范围。
- [Deep Agents：异步子任务测试](https://github.com/langchain-ai/deepagents/blob/70dfbd597ae3d1ac348f6c46892464868f24b070/libs/deepagents/tests/unit_tests/test_async_subagents.py)：覆盖失败唤醒、暂停保护、最终答复后再检查，以及旧 run 通知不能完成新任务。

### 12.3 明确恢复请求的含义与局部检索范围

复用 provider session 只是一项执行选择，不能据此决定恢复哪一步。下列含义应映射到
已有 action／operation 及其准入条件，不新增并行任务 schema：

| 请求含义 | 必须说明的目标 | 保留与失效判断 |
| --- | --- | --- |
| 继续未完成工作 | 原 operation、当前检查点及下一未完成步骤 | 保留适用成果；恢复资格、lease 和工作区重新核验 |
| 重试失败调用 | 具体调用、失败类别、前提变化或瞬时故障依据 | 结果未知时先核对副作用；传输重试与语义返工分开计数 |
| 修复并重新验证 | 原失败条件、修复范围和目标提交 | 仅重新验证失效证据及影响范围，不默认全量 replan/critic |
| 应用已准入计划变更 | 新计划版本、受影响任务与采用决定 | 变更影响范围内重新绑定；无关任务及仍适用成果继续 |

历史重放用于复核或隔离实验，必须明确副作用策略；它不能伪装成原地恢复。
人工中断的继续还需绑定正确请求及真实回答，不能由恢复角色自行补造批准。

执行 Agent 缺 schema、接口说明或失败日志时，可在原合同和授权范围内先检索并形成
新证据；若发现需求、来源能力或依赖不成立，再交 OA 判断。常规检索优先使用现有工具，
不要求每次读取都经过 RM/OA 的模型判断。失败上报仍保留“输入不可获得”的合法出口。

局部检索不会自动证明完整覆盖。198 的固定来源、必需输入、需求／验收映射、正文
读取证据及失效规则继续适用；索引命中、摘要和旧已读回执不能替代当前必要正文。
Deep Agents 的 `fork` 也使用经过摘要处理的有效会话，参见固定版本
[子 Agent 上下文源码](https://github.com/langchain-ai/deepagents/blob/70dfbd597ae3d1ac348f6c46892464868f24b070/libs/deepagents/deepagents/middleware/subagents.py)
的 `_fork_messages`；隔离上下文和历史归档本身不保证语义信息完整。

### 12.4 借鉴源码的验证限制

LangGraph 的处理器自身失败仍可使 run 失败；再次 invoke／resume 时才能继续处理器，检查点不等于后台自动接管。
其 [TimeoutPolicy](https://github.com/langchain-ai/langgraph/blob/5847733bcb186c26b32da5cc822eaf8a89deffeb/libs/langgraph/langgraph/types.py)依赖协作取消；阻塞事件循环的调用仍可能阻止超时触发，不能替代 A05 的独立监督。
Deep Agents `_build_start_tool` 先启动远端、后登记状态，未知结果仍须核对；`_awake_parent` 会因父级忙／暂停或通知失败而不投递，活动父级通过 `_apull` 补查。
上述静态观察不等于框架整体故障，也不证明无人介入就必然完成；ZaoFu 仍须验证持久化义务、异步诊断、周期对账和宿主监督。

## 13. 脱离监工 session 的持续推进（P0）

### 13.1 未完成工作的推进责任

本轮监工复盘提出的目标，是把外部会话反复检查、发现无人执行、恢复交接、确认
下游继续的工作落实到既有 runtime。当前代码尚不能据此宣称已具备完整保证。
范围为当前获准运行中的有效任务和角色请求；历史失败 attempt、已替代任务和
明确暂停／取消的工作需按各自生命周期解释，不能全部重新派发。

| 当前情况 | 必须持续满足的条件 | 超期或条件失效后的责任 |
| --- | --- | --- |
| 已具备执行条件 | 有持久化待办、目标消费者、容量／权限判断及调度期限 | runtime 检查未派发原因，恢复对应调度交接；不以排队状态永久掩盖饥饿 |
| 正在执行 | 有绑定当前身份的有效 attempt、执行载体及阶段进展证据 | 核对载体、lease、结果和长调用状态；确认失效后隔离旧 attempt 并受控恢复 |
| 失败后恢复 | 有原案卷、恢复责任人、当前请求、预期输出和超时出口 | 恢复角色无应答时由 Supervisor/runtime 接手；不等待该失效 Agent 批准 |
| 等待依赖或外部输入 | 有原因、阻塞范围、owner、唤醒条件及复查安排 | 条件到达后校验并继续；未满足则保持明确等待，无关工作继续 |

以上是既有状态的检查视角，不新增 Task schema 或第二状态机。每个当前请求只能
有一条有效执行责任链，允许不同角色提供证据并明确移交。若四类情况均不成立，
应形成控制面故障事实，并进入既有恢复入口，不能仅修改 UI 为“进行中”。

“始终有推进责任”不意味着所有任务时刻占用 lane，也不承诺所有业务故障都可自动
修好。人工 gate、外部依赖、容量与暂停必须如实表达；可执行工作在配置期限内被
调度或暴露明确原因。长调用的有效执行证据可以解释耗时，心跳或重复读页不能证明交付。

### 13.2 已核实基础与缺口

本节沿用 `cff2a8d1` 的静态核查和历史监工事实，不新增对 LS 实时状态的判断：

- [recovery_case_file](../../src/zf/runtime/recovery_case_file.py) 已检测
  `wip_without_carrier`、`ready_but_starved`、`queue_stuck`；检测存在不能证明每次
  都接入恢复执行及效果验收，应逐个追踪消费者。
- WRC 的 attempt 对账和恢复落点见 §3.2；已有局部能力不能证明全部交接闭环。
- A01 的 resident 历史观察不能履行新请求；A05 的 watchdog/RM 同步 tick 路径
  不能证明慢调用期间监督仍然运行。
- [watchdog 超时测试](../../tests/test_run_manager_watchdog.py) 的
  `test_watchdog_emits_unhealthy_for_tick_started_timeout` 证明发出 unhealthy，
  未覆盖控制面恢复后业务继续。这是验证范围差异，不表示完全没有其他恢复路径。

### 13.3 五项实施要求

1. **角色退出必须完成责任交接。** Worker、RM、OA 一轮结束或进程退出时，runtime
   核对绑定请求的结果：有效结果经准入交下游；未履行请求记录原因并进入定向恢复；
   真实暂停或依赖保存等待合同。provider turn 结束不等于 operation 或 Task 完成。
   先对账可能已经落盘的结果及外部副作用，再决定重派，不默认重跑 plan/critic。
2. **持续对账未完成工作。** 事件通知及时触发，确定性的有界周期扫描补偿遗漏；
   无新消息、监工会话退出或控制面重启后仍从 canonical 状态重建责任和下一步。
   阶段交接中的崩溃窗口通过可重放事件及现有幂等动作补齐，不靠 Agent 记忆。
3. **监督路径与慢调用分离。** 模型、工具和深诊断异步执行；协调循环只做有界
   检查、请求与结果消费。整个控制面失效交部署环境的进程监管恢复；监管只处理
   服务生命周期，不判断产品是否完成、不直接修改业务 Store。不得形成双调度者。
4. **策略耗尽后仍有明确出口。** 瞬时故障退避，持续故障诊断，无效方案调整，合同
   问题交 OA，外部输入不足进入有责任人的等待。耗尽当前策略不能丢弃未完成义务，
   也不能无限重复同一指令；保留原案卷、已尝试方案与失败分类，不增加项目总量／费用上限。
5. **恢复验收延伸到应继续的交接。** 发出请求、执行动作、原故障消除分别记录。
   只有对应效果通过，且该恢复承诺继续的下游已接手或进入新的合法等待，才报告
   恢复交付完成。新发生的独立故障单独关联处理；Task 完成仍需满足全部合同。

周期、超时和自动恢复策略统一由 `zf.yaml` 表达，路径遵守 `project.state_dir`。
ZaoFu 定义监管接口、运行意图及资格验证；具体宿主服务的安装／启用属于独立部署工作。
部署环境监管应遵守持久化的运行意图：人工停止、暂停和取消优先于自动恢复，不能
因进程退出而擅自重启工作。恢复前重新核对唯一运行实例、租约和有效版本；连续崩溃
需退避并留下明确诊断责任，防止重启风暴。本节未安装或启用任何新的后台服务。

```text
Host process supervision
          |
          +--> Respect stop / pause / cancel intent
          |
          v
Kernel / Supervisor: bounded reconciliation
          |
          +--> Ready work ------> Dispatch within policy
          +--> Active attempt --> Check current progress / result
          +--> Recovery request -> Check owner / output / deadline
          +--> Scoped wait ----> Check wake condition
          +--> None applies ---> Record fault + controlled recovery
          |
          v
Async Worker / RM / OA / diagnosis
          |
          v
Admit result --> Verify effect --> Continue / explicit wait
          ^                              |
          +------ Events + reconciliation+

The monitoring session is not an execution dependency.
```

### 13.4 C01–C05：LS／LS-v1 监工补充的 P0/P1 修复要求

以下编号仅用于设计追踪，不是运行时 Task ID。证据定位见 §2；历史事故不等于
当前 HEAD 仍有缺陷。先复现或确认已有修复，再补齐入口回归，不重做已经满足的能力。

**C01／P0：有效配置、简报与执行合同一致（补强 R01/R05/R09、B06）。**

- 历史证据：LS operation 上限已为 0，简报仍写 60，导致 Planner 连续失败；记录确认 `6fe9f86f` 修复。当前 [reader budget 简报测试](../../tests/test_reader_budget_briefing.py)覆盖派发与重试，此部分先回归；LS-v1 的 CLI 聊天要求则需单独核对。
- runtime 从同一有效配置与受控修订生成执行说明和机械门禁，绑定 operation、合同、输入及配置版本；配置文件、冻结快照、修订值与本次实际交付可区分，不另造配置真相。
- 简报覆盖当前身份、阶段、工作区／提交、合同与验收、输入读取、skills／权限、输出协议、返工反馈及续作要求；按角色裁剪，大正文仍由固定引用和受控读取交付。与本次执行有关的限制必须和实际门禁一致。
- 不可变旧简报不原地改写；变化通过受控更新／重新绑定交付并保留历史。输入缺失按任务实际所需判断，不伪造聊天来源；fixture 开发与最终真实集成的语义边界由 OA／Planner 方法对账，runtime 校验已准入版本及责任绑定。

**C02／P0：不同任务的恢复互不覆盖（补强 R02/R13/R14、B03）。**

- 历史证据：LS 设计复扫 `evt-4c81a26847ed` 使身份复扫被 `superseded_by_latest_fanout` 取消（`evt-2f4ecbdf4247`），其后又遇到角色占用与 transport 失效。
- Kernel 的去重、替代和取消须核对 run、工作单元／任务、阶段、请求或案卷及有效代次；共享角色名称或“最新请求”不足以证明替代关系。具体键复用现有身份合同。
- 同一请求重复到达只履行一次；不同任务独立保留请求、结果和恢复出口。共享角色容量不足时排队；合并全局诊断须显式保留每个输入义务及结果映射，不能用新请求静默取消旧责任。
- 合法替代记录原因与受影响范围，旧 attempt 迟到结果不得覆盖新执行；公共故障可共享修复责任，但每个受影响任务仍独立重验。

**C03／P0：存储损坏后的可核验恢复（补强 §9、B01/B04）。**

- 历史证据：LS 满盘使 resident 退出，账本出现 3 行截断拼接；在线复制成功及文件前缀摘要相同，均不能证明跨文件一致或没有事件丢失。
- EventWriter/EventLog、Store 与必要 sidecar 的 owner 负责部分写入、中断及重放边界；保留原始字节与诊断，核对可验证事件、状态、产物引用和外部副作用，不绕过 canonical 写入入口。
- 无法确认的事实必须标为未知，阻止依赖该事实的派发或完成准入；不得静默跳过损坏记录后声称完整恢复，也不凭完整前缀猜测丢失内容。恢复动作需保留可追溯依据。
- 快照／恢复演练应证明一致性检查点或可重放边界，覆盖 ZaoFu 归档、必要 sidecar、引用及工作区成果；业务数据库备份不在本文范围。在线复制只能作为取证副本；用隔离文件系统／写入替身注入故障，不填满真实项目磁盘。

**C04／P1：控制面性能退化与无有效推进检测（补强 A05、§13、198）。**

- 历史证据：LS hook／单源读取曾出现约 2 GB 内存样本，局部优化后仍发现其他全量读取路径；LS-v1 三个 Worker 曾反复读取约 135 万字符索引。样本不是严格性能基准，也不证明某个热点是全部停滞的根因。
- Supervisor/runtime 观察事件消费延迟、tick／hook／读取耗时、RSS、重复读取量及阶段进展；角色方法解释实现／验证产出。心跳或输出仍持续而长期无有效推进时，进入有依据的诊断，不能只延长等待。
- 用递增历史规模、日志轮转及约定并发构造回归；增量索引／缓存须与完整可信重放对账，保留身份、签名、因果和读取证据，不能通过丢弃历史或必需正文换取速度。
- 阈值由 `zf.yaml` 与实测基线确定，触发诊断及既有恢复策略，不增加项目任务总量／费用上限；慢调用按当前阶段证据判断，不凭固定时长直接杀掉有效工作。

**C05／P0：操作预演无副作用，执行重新核验（补强 R10/R11、A04）。**

- 历史证据：LS 整组和单角色 `restart --dry-run` 曾产生真实副作用；当前 [restart 实现](../../src/zf/cli/restart.py)和[测试](../../tests/test_cli_restart.py)已有对应防护，先回归而非重写。LS-v1 的 shadow 权限错绑另按 R11/A04 验证。
- CLI／ControlledActionService 对整组、单角色及恢复入口共用机械预检；dry-run 只返回计划与诊断，不启动／停止角色、不修改业务 canonical 状态、不追加动作已应用或已批准的事实。
- 预演展示目标身份、版本、权限依据、影响范围与保留成果。实际执行重新核验这些前提；前提变化导致原动作失效时拒绝并要求重新预检，不能把曾经预演通过当作当前授权。
- shadow 意见只能作为诊断证据；重试不得沿用已知错误权限绑定。已有授权仍有效时直接按受控路径执行，不新增逐次人工确认或第二套状态机。

## 14. 实施分批与现有代码落点

以下是实施建议顺序，不表示已批准或已完成代码变更。

| 批次 | 内容 | 主要复用入口 |
| --- | --- | --- |
| P0-A 恢复基础合同 | 失败类别与真实计数、请求身份与持久化责任、最小恢复位置；R10/R11/R14 的暂停／权限／隔离基础，B05 的义务持久化 | [task_pipeline_reconciler](../../src/zf/runtime/task_pipeline_reconciler.py)、[semantic_exhaustion](../../src/zf/runtime/task_pipeline_semantic_exhaustion.py)、[recovery_case](../../src/zf/runtime/recovery_case.py)、§3.2 的 WRC／attempt／operation 入口 |
| P0-B 实际恢复与交付闭环 | gate 作用域、旧等待、动作选择、排队／应答超时；R01–R06、A01/A02/A04/A05、B01–B04、C01/C02/C03/C05；原失败探针与下游效果验收 | [run_continuation](../../src/zf/runtime/run_continuation.py)、[run_manager](../../src/zf/runtime/run_manager.py)、[watchdog](../../src/zf/runtime/run_manager_watchdog.py)、[tick_services](../../src/zf/runtime/tick_services.py)，并经 WRC 的实际阶段派发验证 |
| P1 方法质量与诊断效率 | R07–R12 的完整方法／交互、A03、B05 的上下文方法、B06、C04；198 的任务选择实际交付与覆盖检查 | 现有通用 skills／角色方法、[task_plan_consumption](../../src/zf/runtime/task_plan_consumption.py)、[task_scoped_plan_inputs](../../src/zf/runtime/task_scoped_plan_inputs.py)、CLI/Web |
| P2 规模验证与运维完善 | R13 的公共故障协调；R14 参数化负载／故障风暴；R15 临时缓解退出方法与干净环境复验 | 现有 WRC、incident、隔离验证环境与 ZaoFu 发布流程 |

批次按依赖排序；安全基础不能因所属条目还含 P1/P2 优化而后置。失败分类、真实计数、
暂停、义务持久化和作用域隔离先于自动恢复闭环；输入覆盖先于按需加载收益验收。
每个实施项绑定 owner、真实调用入口、当前复现、影响测试及效果证据；复用已有配置／
事件／Store，仅在证明不能表达时扩展。项目业务验收与 ZaoFu 修复验收分开记录。

本轮源码复核确认队首选择、代次推导计数、A01/A02/A05 的实现基础仍与前述描述一致；
A03 是方法风险，A04 是入口等价性待验证，不能宣称已证实模型误判或权限绕过。
C01 的有效预算简报、C05 的 restart 预演及 WRC 的 fault matrix 已有实现／测试；
这些先补入口与竞态回归，不能整项当作“从未实现”。本轮未运行真实故障注入。

§13 的持续推进是 P0 的端到端目标：将 A01/A05、B02/B04 与既有对账入口接通，
并以关闭监工 session 后的恢复验收判断是否完成。不能仅增加提示词、告警或重复
监督角色就标记交付；外部进程监管的部署资格与 runtime 内部恢复资格分别验证。

共同基础复用 [WorkflowOperationService](../../src/zf/runtime/workflow_operation.py)
的身份和生命周期，以及[当前失败 OA 评审入口](../../src/zf/runtime/orchestrator_agent_execution_review.py)。
角色语义方法优先进入对应 skill／prompt；机械规则、证据检查和状态迁移保留在 runtime。

每批仍遵守仓库发布／清理规则；R15 的方法完善后置不允许提前丢弃未交付成果。

首次 Impl 评审、合同重规划和耗尽分诊有不同消费者，不能仅凭输出相似合并；复用案卷与准入，覆盖历史兼容和操作员入口。

## 15. 验收与真实运行验证

| 场景／步骤 | verify: 验收条件 |
| --- | --- |
| WRC：重启时已有有效结果或遗漏通知，RM agent 不可用 | WRC 对账并继续正确阶段；不依赖模型判断重做已准入成果，不越过仍需 RM 恢复决定的在线故障 |
| WRC：operation-v2 lease 过期，随后 RM 发出有效恢复回执 | 未准入前保持隔离；WRC 对原 operation 创建正确新 attempt 并实际派发；故障案卷按效果结案 |
| WRC：同一 attempt 同时收到 RM 恢复、操作员 resume 和重复通知 | 共同身份／当前状态检查只允许一次有效 redrive；旧结果被隔离，暂停／取消仍优先；覆盖真实调用入口 |
| 范围：在无 FMS 命名、无认证／模板业务的最小项目中复现恢复场景 | 相同通用配置与合同生效；测试不依赖 LS 路径、固定 16 lanes 或业务 Task ID；与 LS-v1 产品验收分开 |
| 对账被替代任务的旧人工等待 | 旧事件保留；无关且获准的恢复动作可派发，真实 run 级暂停仍阻断应阻断动作 |
| 队首动作不可执行，后面存在独立动作 | 显示队首原因，独立动作在容量和授权允许时执行，无越权绕过 gate |
| RM／OA 进程存活但不响应已派发请求 | 输出绑定请求的超时事实并进入受控恢复；没有待办的 idle 不误报 |
| OA 请求始终未派发或 Impl 占满容量 | 排队阶段可观察、可诊断，恢复角色获得配置允许的调度机会 |
| Planner 返回格式错误产物 | 拒绝原因送回生产者；定向修复后重新准入，不无条件重跑完整计划 |
| 分别注入传输、输入、业务失败 | 路由和计数区分正确；输入故障可以如实上报，不能据此声明完成 |
| 同一问题连续失败或跨计划代际出现 | 保留关联与已尝试方案；没有新依据时不盲目重派，不靠新 ID 清零 |
| 派发或结果接收期间重启，包含日志轮转 | 当前阶段恢复，无重复副作用；旧 attempt 迟到结果被正确隔离 |
| 修复 Worker 报告完成 | 原失败探针与必要回归针对确切提交通过后才关闭案卷；任务仍遵守完整验收 |
| 恢复时使用任务切片，随后变更来源或损坏引用 | 来源和验收映射完整；变化正确失效与补读，无法恢复时明确失败，不虚报已读 |

补充设计逐项验收；以下均为待执行场景，不是本次文档更新已通过的测试：

| 编号／步骤 | verify: 验收条件 |
| --- | --- |
| R01：在 Web、控制面及操作员进程使用不同修订，再按授权部署目标修复 | 各进程版本可区分；合入、部署、实际加载和真实验证分别有回执，未更新的进程不被报告为已生效 |
| R02：发布未批准候选，再批准并启用仅影响部分任务的新计划 | 候选不撤销旧执行；受影响任务在检查点迁移并保留适用成果，无关任务继续；失效 attempt 无法提交当前结果 |
| R03：在实现完成后注入 TaskRef/结果交接失败，同时触发恢复调度 | 交接修复与新 Impl 派发正确互斥；原成果仍适用时进入后续阶段，真实实现缺口不会被当作纯元数据问题 |
| R04：请求前禁用消费者，以及请求后移除或使消费者启动失败 | 有明确的不可用/拒绝/取消或迁移结果；历史请求可追溯，inflight 不永久占用；不伪造成功或擅自启用服务 |
| R05：为同一命令 ID 提供 fixture 和真实集成两种环境证据 | 阶段、目标和环境含义不混用；开发验收可独立完成，最终真实验收仍有明确 owner 和未完成义务 |
| R06：分别输入不同根文件、同文件真实冲突及显式根 owner 政策 | 摘要准确标记提示/政策/实际冲突，绑定实际准入规则与证据，不把不同根文件误报为同文件冲突 |
| R07：审查含集中依赖和可用 fixture 的任务图 | 给出 ready 数量、关键路径、依赖理由与共享文件责任；合法拆分经计划准入生效，最终集成义务保留 |
| R08：注入“保留历史/删除历史”等原稿、AC、测试矛盾及合法已批准变更 | 语义审核定位来源并形成有依据的结论；矛盾不得靠覆盖行数通过，合法新版本不被旧要求反复误报 |
| R09：更新 skill 后保留一个旧会话，再检查新旧生产者产物 | 实际交付版本可区分，未知消费状态如实显示；已读不自动等于方法落实，产物符合性由独立检查证明 |
| R10：分别执行停止监工、停止新派发、等待在途任务、检查点暂停与终止 | 每种行为与预览一致，回执列出仍运行角色及保留成果；恢复不重复副作用、不接受旧 lease 写入 |
| R11：提交 shadow revise 作为正式权限，并分别提交格式修复与合同改动 | 错误权限在副作用前被拒绝；格式修复不无条件重跑全计划，合同变更执行必要评审并绑定新版本 |
| R12：让 Task、operation 和 UI 快照处于不同更新时间，并包含被替代任务 | 展示版本和新鲜度，解释有效任务分母与实际活动；对账经正确 owner 完成，不手改投影伪造进度 |
| R13：让多个任务遭遇同一公共故障，再加入一项文本相似但根因不同的故障 | 共同证据支持一个修复责任方，其他问题保持独立；公共故障恢复后每个任务单独重验 |
| R14：按配置容量运行实施、Verify、集中故障与控制面重启，含多种 lane 配比 | 工作区、服务和证据归属隔离；验证目标稳定；16 路只作获准样例，记录资源上限与队列延迟，不把 mock 结果当真实 Provider 吞吐资格 |
| R15：退出本地排除/手工恢复缓解，在干净 worktree 重放修复 | 无隐藏本地依赖；代码、文档、skill 和证据可追溯；活动或未交付 worktree 不被清理 |

第三轮路由与异常恢复验收同样待实施，不以 §11 的内存复现代替：

| 编号／步骤 | verify: 验收条件 |
| --- | --- |
| A01：先有旧 observation，再派发新诊断请求且不回应；另设无待办空闲对照 | 旧观察不履行新请求义务；新请求超时有 owner 与出口，空闲不误报；日志轮转不丢失请求与回应关联 |
| A02：OA 分诊请求已发出，随后分别注入诊断失败和修复成功 | 请求 operation 可结束；原故障未消除时案卷不因交接成功而 resolved，之后凭绑定该故障的效果证据结案 |
| A03：旧通过证据后出现新失败、兄弟任务正常而当前 child 死亡、修复旧 unroutable 的前提 | 分别核对当前证据；不因任一历史条件永久 wait，也不重复修复当前已完成或正常等待的任务 |
| A04：同一动作从 agent 建议、pending action 与操作员入口进入，并同时上报同一故障 | 已有权限和作用域一致时准入结论等价；过期、重复和越权请求在副作用前被拒绝，同案卷只有一条有效执行责任链 |
| A05：阻塞诊断模型／工具调用，同时送入正常事件、请求超时和停止信号 | 事件消费与监督仍可运行；慢调用有界终结，锁不无限占用；控制面进程级故障另用显式宿主层验证，不靠同循环 watchdog 自证 |
| 路由独立性：使 RM agent 不可用，分别提交已知运行故障与已识别产品问题，再使 OA 某请求超时 | 机械恢复按现有策略执行，产品问题交已配置 OA；不等待新增 RM 模型分诊，不被无关队列阻塞；诊断超时有责任出口，真实 gate 仍有效 |
| 判断移交：未知运行故障诊断后发现合同冲突，同时重复投递原请求 | 保留同案卷因果与证据，明确移交 OA 的待回答问题；当前判断责任唯一，不重复分诊；获准决定仍经共同准入交 WRC 执行 |
| 最小恢复：分别注入传输、环境、纯交接及合同内代码缺陷 | 恢复对应未完成步骤；保留适用成果，各自验证故障效果；代码仍经独立 Verify，无合同变化时不默认全量 replan/critic |

外部调研补充四个优先场景。以下均待执行，先在隔离的确定性假服务中控制故障窗口，
再按明确层级验证真实入口；表内的恢复结论不能由历史测试存在推定：

| 编号／步骤 | verify: 验收条件 |
| --- | --- |
| B01：让外部操作成功，再丢弃成功响应并重启本地控制面 | 原操作身份可恢复；先核对外部状态，副作用没有重复发生；无法查询时保留未知状态及责任人，不报告失败后盲目重派 |
| B03：多个任务并行，仅一个失败；在部分成果落盘后重启 | 已准入且版本仍适用的成果不重跑；失败／未完成步骤单独恢复，下游只消费有效成果；另用输入版本变化验证正确失效 |
| B02：遗漏子任务完成通知，随后重启父级控制面，再送达重复或旧 run 通知 | 对账后进入正确下游且只交接一次；旧结果不覆盖当前 attempt；父级处于真实人工暂停时不擅自继续 |
| B05：压缩或重建 RM/OA 会话，同时保留未完成请求与有效交付记录 | 当前请求、owner、待交付产物、期限及已尝试方案完整重建；必要正文按固定版本补读，不依赖历史聊天记忆，也不凭摘要声明完成 |

另外两项随对应实现批次验证：

| 编号／步骤 | verify: 验收条件 |
| --- | --- |
| B04：失败事实落盘后令诊断／修复处理器崩溃，再恢复；另在修复动作回执前中断 | 恢复原处理器的未完成步骤，绑定原案卷／失败证据；用执行次数及事件证明原 Worker 不因处理器崩溃被重跑；已完成子步骤／副作用先对账，后续确有重试决定才派发原阶段；不清零计数或提前结案 |
| B06：主角色已配置方法，子角色仍使用旧配置；再向实际子角色交付新版本 | 能区分角色实际能力与方法版本；局部可修复错误获得反馈并完成修复，超范围问题正确升级；独立 Verify 和受控权限继续有效 |

关闭监工 session 的 P0 验收：测试驱动器只注入故障并采集证据，不代替 runtime
发送继续提示、手动重派或修正状态。以下六项待执行，默认先用隔离 mock，进程级故障
再走显式宿主验证；记录检测、接管、再次执行和下游交接的时间及事件引用。

| 场景／步骤 | verify: 验收条件 |
| --- | --- |
| 无监工：Worker 在未交付有效结果时结束一轮或退出进程 | 绑定请求保持未完成；runtime 自行核对并恢复对应步骤；已有有效结果不重做，旧 attempt 迟到结果不覆盖新执行 |
| 无监工：OA 接单后退出或不回应 | 请求超时后有实际接管、定向恢复或明确外部等待；无关工作继续；不只新增一条告警 |
| 无监工：RM agent 退出 | 正常调度和无需新判断的恢复继续；依赖诊断的请求获得恢复或有责任人的出口；不等待失效 RM agent 授权 |
| 无监工：诊断调用卡住，再分别终止或阻塞整个控制面 | 慢调用不阻断事件消费和监督；控制面故障由独立监管接管，恢复后只有一个有效执行实例，并从未完成处继续 |
| 无监工：遗漏完成通知，随后重启控制面 | 周期对账补回交接；有效成果不重跑，原恢复承诺的下游实际启动或进入可解释等待；对接 B02/B03 |
| 无监工：明确暂停、取消或停止后令进程退出 | 监管和恢复均尊重运行意图，不擅自重启或派发；后续获准恢复才继续，保留原成果与等待依据 |

C01–C05 的补充验收均待执行，历史修复与本次文档检查不作为通过证据：

| 编号／步骤 | verify: 验收条件 |
| --- | --- |
| C01：在运行中受控取消限制，分别检查旧／新 attempt，并加入 CLI 无原始聊天及 fixture 开发任务 | 有效配置、实际简报、查询和门禁一致；旧交付可追溯；缺失来源如实标注，已有必需输入可支撑任务时不误阻塞；最终真实集成义务保留 |
| C02：同时派发两个任务的恢复，再重复投递其中一个请求，并加入合法替代与迟到结果 | 独立任务不互相取消，同请求只执行一次；容量不足有明确队列出口；仅获准范围发生替代，迟到结果不覆盖新执行 |
| C03：在事件、Store、sidecar 写入及快照窗口分别注入空间不足、部分写入和进程中断 | 原始证据保留；一致性或可重放边界可证明，未知事实不被假定成功／失败；经受控恢复后必要产物与成果完整，外部副作用不重复；在线副本不冒充一致快照 |
| C04：扩大历史账本并轮转，在约定负载下重复读取和恢复；另设正常长调用对照 | 记录延迟分位、RSS、重复读取与阶段进展；监督不被慢路径长期阻塞，异常进入诊断且正常长调用不误杀；优化前后必要事实与输入覆盖一致，无新增费用／任务上限 |
| C05：对整组、单角色及恢复动作执行 dry-run，再在 apply 前改变目标版本或权限依据 | 预演没有真实进程／业务状态副作用；执行重新检查并拒绝失效前提、错误 shadow 权限；有效授权可正常执行，既有在途成果保留 |

验证顺序为：通用隔离 fixture 复现历史问题 → 影响范围单元测试与确定性 mock E2E →
premerge sentinel → 显式真实 Provider／宿主能力验证 → 单任务受控灰度与断点恢复。
单任务灰度通过后再做约定并发规模的真实隔离压测。测试副本及临时服务遵守仓库仿真
清理规则；不能以单元测试通过宣称真实链路已生效。测试按影响范围和明确层级执行，
不因本设计范围较广就为每个小批次默认运行全量测试。

## 16. 观察指标与完成条件

分别记录失败到案卷建立、案卷到派发、派发到应答、修复到验证的耗时，避免总时长掩盖
某个交接长期停滞。展示当前责任人、目标版本、预期输出、等待原因及下一复查时间。

重点观察无依据重复派发次数、没有责任人或超期未处置的请求数、案卷重开率、
人工介入次数、实际读取正文量与任务有效推进。当前没有收益基准，不承诺固定提速比例。

补充记录修复从提交到加载和真实验证的耗时、计划切换导致的重复工作、纯交接故障
触发的重复 Impl、无消费者请求滞留、临时缓解未退出项及共享故障重复修复。
并行度结合 ready 集合、关键路径、资源容量与已验证交付吞吐解释；不把心跳、读取页数、
lane 占用或提交数量直接当作业务完成度。角色与阶段超时阈值根据实测耗时校准。

第三轮补充观察：新请求被历史观察误判已回应的次数、交接完成到故障消除的间隔、
不同入口的准入结果差异，以及慢诊断期间事件消费和 watchdog 的延迟。
分别统计 operation 完成、故障案卷结案与业务 Task 完成，不能共用一个成功计数。

外部调研补充观察未知结果的核对时长、重复外部副作用次数、遗漏通知后的对账延迟、
恢复时重复执行的有效成果数，以及会话重建后遗漏的请求义务。指标按实际入口分组，
避免一个已覆盖的 Channel 或 reader 场景被当作全部恢复路径的资格证明。

持续推进另记录：未完成但没有有效责任链的数量及滞留时长、Agent 退出到接管的耗时、
监工手动唤醒／重派次数、控制面恢复后的重复执行，以及违反暂停意图的自动启动次数。
关闭监工后仍需人工补一次“继续”才能完成测试，即该场景未通过；持续 token 输出、
进程存活、发出 unhealthy 或恢复请求均不能单独证明通过。

C01–C05 另统计有效配置与交付不一致次数、跨任务误取消、存储恢复中未核实事实、
预演副作用／过期预检被执行，以及不同历史规模下的事件延迟分位、读取耗时和峰值 RSS。

完成本设计的实施验收，必须证明实际 runtime/CLI 入口已接入、调用方测试覆盖，且
获准的通用真实 canary 从失败进入修复、独立验证并继续下游；LS-v1 业务完成不作本设计关闭条件。修复落地还需对账 R01 的加载与效果、
R15 的交付可追溯性。新增字段、提示词或日志本身不算完成。
