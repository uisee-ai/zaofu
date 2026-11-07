# 199：失败恢复实施与覆盖记录

> 状态: partial
> 日期：2026-11-06 UTC；设计：[199](../design/199-worker-failure-recovery-and-role-handoff.md)；起点：`cff2a8d1`。

## 交付范围与计数

本轮在 `wip/codex-20261106-design199` 独立 worktree 实施，范围仅 ZaoFu。
未恢复 LS/LS-v1、部署主控制面或启动真实 Provider 压测。
实现提交与归档提交记录在本批 `tasks/done/2026-11-06-0832-199-*.md`。

199 的 31 个编号均已对账：**14 项达到本轮软件闭环，15 项部分覆盖，2 项未完成**。
“软件闭环”指当前代码／方法已交付、实际入口及对应确定性测试已核验，包含既有实现的回归；
不表示 §15 的所有真实环境、模型行为和故障风暴验收已通过，也不将其换算成工作量百分比。
“部分”有可用实现或方法，但仍有明确的软件或验证缺口；“未完成”不靠现有相邻能力抵扣。

本轮 10 个工作包按下述有界验收关闭；它们涵盖实现、回归和全量对账，**不是把 31 个编号全标 done**。
未完成的设计义务继续列在后续候选中，不静默删除、不伪装成等待模型响应。

## 实施后的骨架

```text
Worker 结果／退出／lease 到期
             |
             v
EventLog + Task/Attempt Store + 必需 sidecar
             |
       +-----+------------------------------+
       |                                    |
       v                                    v
WRC 确定性对账                         RM runtime 观察／选路
已有有效结果 -> 交接下一阶段            逐请求义务、作用域 gate、当前案卷
需在线恢复决定 -> 等待 RM               合法等待 -> 保留 owner 和恢复入口
       ^                                    |
       |                    +---------------+----------------+
       |                    |                                |
       |                    v                                v
       |           已知机械恢复动作                    OA 语义决策／RM 只读诊断
       |                    |                         固定输入、持久化 operation
       |                    |                         模型调用不占 RM tick 锁
       |                    +---------------+----------------+
       |                                    |
       |                                    v
       +---- 受控准入：当前状态／权限／范围／同 attempt 去重
                                            |
                                            v
                                请求回执 != 原故障消除
                                原任务效果证据 -> 案卷闭环
```

语义问题仍由 OA/Worker/独立 Verify 判断；WRC 执行机械派发；RM runtime 不成为第二套 Task 状态机。
事件唤醒和周期 tick 都能接回持久化请求。整体控制面进程死亡的自启动仍须宿主层资格验证。

## 已复现的问题与修复

### 2026-11-07：Verify 执行失败后的续接

LS-v1 网关已完成 Impl，但 Verify 因输入生命周期误报退出，准入结果是
`execution_status=failed / verdict=abstained`，没有产品失败判定。
修复输入绑定并重载控制面后，原 operation 已结算；现有 workflow resume 返回 no_action，
Impl 和 integration 的恢复入口也不适用。WRC 保留失败事实是正确行为，
缺口是 RM/OA 不能仅凭诊断或修复完成使已结算的 Verify 重新执行。

新增 `zf recover task-verify --operation <id> --reason <repair-evidence>` 默认预览，
显式 `--apply` 才通过 WorkflowOperationService 标记原 operation superseded，
由正常 WRC 派发下一代 Verify。准入复核已绑定摘要的失败结果、source event、
attempt/request 身份、当前 Task 合同、run、任务代次、精确 TaskRef 及无活跃 attempt。
产品 fail/pass、暂停/终止、目标变化、旧代次或缺损证据均拒绝。
原失败 sidecar、事件和已通过 Impl 保留；重复执行幂等。

普通任务重试 Verify 时，只有连续的受控 Verify 重试链可以复用较早 Impl 的自检；
继续验证自检与当前合同/目标完全一致，不能按“最近的成功”随意复用旧证据。
verify_only 任务沿用同一受控入口，无须伪造 Impl。
该入口是操作员恢复能力；尚不宣称 RM 已能自动证明补丁修复原故障并自动调用它。
实际部署效果另记 LS-v1 监工记录，不以测试通过冒充产品通过。

| 问题 | 本轮修复与实际入口 | 证据 |
| --- | --- | --- |
| 队首不可执行、单任务人工等待阻止独立任务 | continuation 扫描可执行候选；TaskStore 传递依赖闭包；未知／run 作用域仍关闭 | `test_run_continuation_scope.py`、`test_run_manager_scoped_selection.py` |
| 已处理诊断长期占据 next_operation，tick 去重后空转 | RM 保留未解决待办和效果案卷，默认选路与显式筛选均先复用执行器去重判定；已投递动作不再挡住后续请求，超时重试仍沿用既有资格 | `test_run_manager_seen_selection.py`，真实 projection/tick；LS-v1 监工 2026-11-07 复现 |
| 普通 RM 诊断无人承接，且成功提示后仍走诊断 fallback | run_manager_recovery 交现有 resident；绑定 request_id、响应事件和语义清单；run/task 上下文写入带摘要 sidecar；Codex 复用 Worker bracketed paste；已有请求形成有期限等待；送达后停止重复 fallback，原故障保留 effect pending | `test_run_manager_diagnosis_consumer.py`，实际 projection/tick，含旧 Autoresearch inflight、响应与效果分离 |
| resident 误读合同/owner 投影，已诊断语义失败不能移交 OA | context 明列 effective/projection revision、计划 owner/实际 assignment、含归档的直接依赖状态；绑定 recommendation 复用当前准入 Impl 的既有 OA 审核，沿同 request/case 继续，旧 operation/禁用 OA/人工 gate 仍拒绝 | `test_resident_semantic_handoff.py`、`test_recovery_context.py`；LS 网关 evt-65bfaf042279 的实际诊断误报 |
| 同请求诊断→OA 交接覆盖 required 上下文正文 | 恢复上下文复用不可变 JSON sidecar，每次事实快照按内容保存；新旧 descriptor 均可验，保留访问范围与原请求；不修饰已丢失的历史引用 | `test_recovery_context.py`、实际 `test_resident_semantic_handoff.py` RM tick；LS-v1 evt-d56d180e491d 覆盖复现 |
| gap 的结构化验证命令在增量适配中被 str，AC 引用 ID 丢失 | 保留已有命令别名的结构化目录及元数据，复用 Task Map 校验；不一致目录与未知引用继续拒绝，旧字符串仍兼容 | `test_gap_command_catalog.py`，真实 flow.gap_plan.ready → contract；LS-v1 evt-150d66427514 |
| 无关任务增量包发布使在飞 Verify 的输入被误报过期 | 输入 lifecycle 按当前 TaskStore 合同/run/dispatch 与运行 operation/attempt 证明原 generation 绑定，复用已准入包选择；取消、变更、撤销和冲突仍关闭；不发执行权限 | `test_task_input_generation_lifecycle.py`，实际 index/recover CLI；LS-v1 网关 evt-cc42179f3d50 |
| agent 建议入口缺少同等恢复门禁 | executor 复核暂停、人工 gate、当前案卷与 precondition；延期建议保留；无匹配审批显式拒绝 | `test_run_manager_action_admission.py`、`test_orchestrator_agent_execution_review.py` |
| 旧 observation 被误当新请求响应 | resident 请求绑定 ID、接收者、预期事件和期限；重发不重置期限；暂停时长不计入 | `test_resident_request_obligations.py`、实际 RM projection/tick |
| 分诊回执提前关闭故障 | request/advice 交接保留 effect pending；绑定同请求的正式建议可继续原案卷；兄弟任务完成不能结案 | `test_recovery_handoff_effects.py`、`test_run_manager_rework_triage.py` |
| 同步 reflect 阻塞 RM tick | 复用 WorkflowOperation 和不可变输入／结果；独立线程执行只读调用；超时和迟到结果竞争一次结算 | `test_run_manager_reflection.py`，通过真实 `run_manager_tick` 调用 |
| generic resume 把诊断 operation 当无 lease 的旧 Worker | reflection 由 RM 恢复，generic operation resume 输出明确 no_action；禁用消费者则取消请求并出回执 | 同上、`test_workflow_operation_resume.py` |
| 第 9 代 operation 被直接判语义耗尽 | 按已准入语义失败结果去重计数；环境／传输失败不算产品返工；代次继续用于身份 | `test_task_pipeline_failure_accounting.py`、`test_task_pipeline_impl_exhaustion.py` |
| RM 与重启同时批准同一失败 attempt | operation guard 串行化准入；当前同 attempt 的重复恢复只一次；再次中断、预算修订仍保留各自合法入口 | `test_workflow_redrive_race.py`、reader/synth budget 和 OA operation 回归 |
| 不同根文件 owner 被误报冲突 | plan digest 只报告同一路径多 owner，明确摘要不替代真实准入规则 | `test_plan_digest.py`、`test_operator_plan_preview.py` |
| 异步完成／恢复后没有事件唤醒 | 登记 `run.manager.reflect.completed` 与既有遗漏 `run.resumed`；保留周期对账 | wake coverage/surface、tick services |
| 旧提示词允许永久 wait | RM briefing 核对当前 child/证据/前提；OA、Worker、Planner 方法补齐可执行修复、依赖和输入责任 | 三份 canonical skill 与两种分发副本；skill provenance/readiness 测试 |

语义失败计数目前按当前 Task 的已准入失败累计，**尚未完成跨新 Task ID／跨计划代际的同根因计数继承**。
故障效果默认以绑定任务的正式完成作为保守证据，尚未交付所有修复类型的独立原失败探针。
reflection 的持久化保证只读诊断可重放，不承诺模型调用只发生一次；不协作退出的调用可能存活到进程退出，
但不会持续占用 tick 锁或产生无限并发调用。内置 Provider CLI 使用原有配置超时。

## 31 项对账

表内测试文件均位于 `tests/`；已执行层级和遗留失败见后文。

| 编号 | 本轮结论 | 实现／验证落点 | 尚缺条件 |
| --- | --- | --- | --- |
| R01 | 未完成 | 现有 `source_repair_restart_actuator` 仅证明控制面重启路径 | Web、CLI、常驻控制面各自加载修订的统一回执与真实部署 canary |
| R02 | 软件闭环 | plan approval identity、replan adoption redrive、Task Pipeline generations；`test_workflow_operation_resume.py` 等回归 | 真实运行的计划替换 canary |
| R03 | 软件闭环 | pending handoff reconciliation、TaskRef repair deadends、有效结果优先 | 真实工作区成果交接 canary |
| R04 | 部分 | OA unavailable/timeout 既有回归；新增 reflect 禁用消费者显式关闭 | 所有请求家族的消费者移除／启动失败迁移统一覆盖 |
| R05 | 部分 | Planner 方法明确 fixture/真实集成、命令目标与阶段责任 | 生产者工件和独立评审的场景评测 |
| R06 | 软件闭环 | plan digest 根路径冲突修复；真实准入规则保持独立 | 无本轮新增软件缺口；真实计划验收另计 |
| R07 | 部分 | readiness skill 要求 ready 数、关键路径、依赖理由与共享文件责任 | 复杂任务图的模型产物评测及准入后效果 |
| R08 | 部分 | 原始约束／矛盾对账方法与固定版本输入 | 定性语义反转、合法新版本的独立评测 |
| R09 | 部分 | skill provenance/readiness、三份分发一致性、worker envelope | 新旧会话实际行为符合性；不得把已交付当已遵循 |
| R10 | 部分 | pause lifecycle、RM 停派、请求暂停时钟、CLI restart dry-run | 五种停止意图的统一影响回执与宿主进程组合验收 |
| R11 | 软件闭环 | 现有正式 plan approval identity 与受控入口；新增当前 precondition/审批检查 | 真实操作员组合 canary |
| R12 | 部分 | RM projection 的 request/owner/age、真实失败计数；现有进度回归 | 跨 UI/Task/operation 版本与有效分母的一致性资格 |
| R13 | 未完成 | incident 投影已有基础，不等于公共故障协调已交付 | 共同资源证据、单一修复 owner、各受影响任务独立重验 |
| R14 | 部分 | 1/1、3/2、8/8、12/4 参数化调度与成果保留 | Compose/端口/数据库实机隔离、集中故障与资源压测 |
| R15 | 软件闭环 | 独立 worktree、精确路径提交、文档/skill/task 交付审计 | 部署侧历史临时措施退出须单独执行 |
| A01 | 软件闭环 | 当前请求义务、超时出口、重放、空闲对照、排除暂停时长 | 真正常驻 Provider 的超时 canary |
| A02 | 部分 | 交接与效果分离、同请求建议沿原案卷继续、任务作用域验证 | 每类修复的根因探针及超时/升级全链案卷继承 |
| A03 | 软件闭环 | RM/OA/Worker 方法删除历史通过、兄弟 busy、旧 unroutable 永久 wait 条件 | 模型行为符合性评测 |
| A04 | 软件闭环 | pending/agent/operator 共享 effect 前机械 gate；重复恢复、旧前提和未知审批测试 | 实机控制面并发操作 canary |
| A05 | 软件闭环 | 异步反思、固定输入、超时、迟到隔离、禁用与 generic resume 入口保护 | 进程级独立监督、真实 Provider 不协作退出资格 |
| B01 | 部分 | operation/result 优先、CAS、redrive guard 和已落盘结果重放 | 可查询外部副作用的响应丢失故障矩阵；任意 shell 不保证 exactly-once |
| B02 | 软件闭环 | durable completion 重放、wake 注册、周期对账、遗漏通知 mock 链路 | 宿主重启后无交互自启动资格 |
| B03 | 软件闭环 | 有效 Impl 直接进入 Verify，独立任务结果保留与旧 attempt 隔离 | 多 Provider 实机重启 canary |
| B04 | 部分 | reflect handler 请求/结果持久化；原案卷的 OA 建议续作 | 所有修复 handler 的中断窗口与跨阶段根因继承 |
| B05 | 部分 | Store/Event/sidecar 重建义务；198 task plan consumption/scoped input 回归 | 所有 RM/OA 诊断包的固定必需正文与已尝试方案完整性评测 |
| B06 | 部分 | canonical 方法、分发副本、实际 materialization/provenance 回归 | 各实际子角色错误反馈与修复行为符合性 |
| C01 | 软件闭环 | reader budget briefing、named budget recovery、配置 loader、固定 operation 预算 | 真实旧/新 Provider attempt 配置变更 canary |
| C02 | 软件闭环 | 独立任务 gate、跨任务 replan 保留、同 attempt 准入锁与去重 | 宿主并发故障重放 |
| C03 | 部分 | Store ENOSPC 原值保留、sidecar 失败不发布 operation、引用损坏显式失败、归档重验 | Event/Store/sidecar 多步崩溃窗口和在线一致快照的完整矩阵 |
| C04 | 部分 | 慢模型不阻塞 tick；现有 EventLog 缓存/轮转回归；确定性运行时间可复核 | 约定负载下 p50/p95、RSS、重复读取与有效进展的成对基准 |
| C05 | 软件闭环 | restart/operation dry-run、apply 前身份及 precondition 复核 | 实机进程无副作用对照与真实授权组合 |

## 本轮 10 个工作包

任务文件统一前缀 `2026-11-06-0832-199-`；完成后归档到 `tasks/done/`。

| 工作包 | 本轮验收范围 | 主要证据 |
| --- | --- | --- |
| 01 scoped-continuation | 候选扫描、任务/依赖门禁、入口机械准入 | continuation scope、scoped selection、action admission |
| 02 recovery-effects | 分诊/修复交接与故障效果分离 | recovery handoff、triage 实际 tick、case projection |
| 03 request-obligations | 当前 resident 请求、重放、期限与合法空闲 | resident obligations、RM resident/projection |
| 04 nonblocking-diagnosis | 异步诊断、持久化恢复、超时与迟到、消费者关闭 | reflection 实际 tick、generic resume |
| 05 semantic-accounting | 已准入实际失败计数，代次不计失败 | failure accounting、impl exhaustion、reconciler |
| 06 role-methods | 责任、局部修复、依赖/输入方法和三份交付 | instruction/readiness/provenance |
| 07 resume-regressions | WRC redrive 竞态、重复中断、结果保留 | redrive race、fault matrix、resume、reader/synth budget |
| 08 admission-regressions | 配置、摘要、权限、存储与 dry-run | plan digest、budget briefing、storage faults、CLI restart |
| 09 progress-delivery | 事实/等待对账、隔离配额、版本与剩余资格记录 | lane mix、RM progress/case、本文 R01/R12–R15/C04 缺口 |
| 10 design-coverage | 31 项对账、无人交互机械链路、影响闭包验证 | `e2e/test_design199_recovery_mock_e2e.py`、验证回执 |

## 验证回执

第一轮 `scripts/dev-verify.py run --base dev`：**14,462 passed、17 failed、20 skipped**。
在隔离 `cff2a8d1` 基线重跑 17 个失败节点：8 个原已失败、9 个基线通过。
本轮回归包括实际失败计数的新 fixture 约定、跨入口去重对合法再次中断的过度限制、
显式人工 token 被当旧裸通知和 wake 快照更新；已逐项修复，未放宽准入或隐藏失败。
与恢复相关的两个原有失败一并处理：登记 `run.resumed`；归档签名测试按当前回溯窗口生成日期。

针对性复验：恢复相关原失败组 **239 passed、7 skipped**；RM/分诊/准入 **230 passed**；
指令与技能交付 **47 passed、1 skipped**。上述为不同命令结果，不能相加宣称唯一测试数。

最终 `scripts/dev-verify.py run --base dev` 的确定性广域结果：**14,477 passed、6 failed、20 skipped**。
6 项均已在隔离 `cff2a8d1` 基线复现：

- `test_export_public.py` 四项：公开 checkout 缺少 `tools/export-public.sh`。
- `test_prompt_source_closure.py` 一项：缺少 `prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md`。
- `test_feishu_channel_progress_card.py::test_confirm_click_is_wired_to_real_controlled_action`：已有确认动作返回与测试预期不符。

这些失败未被跳过、改成通过或用复制未交付的本地文件掩盖。广域命令仍为失败退出，
不能报告“全套绿色”；本轮验证采用修复回归通过、无新增失败及基线隔离复现的证据。
该命令首层失败后不会自动执行后续层，故另外执行原计划的各层：

- serial（`test_memory_rotate_concurrency.py test_session_mutex.py test_state_locks.py`，`-m 'serial and not perf and not host and not real_provider'`）：**10 passed**。
- `bash scripts/dev-premerge-gate.sh`：**53 passed**，文档 currentness 通过。
- `bash scripts/run-flow-smoke.sh`：**10 passed**。
- 最后补充的诊断回执并发窗口及真实 RM caller 复验：**175 passed**；随后又纳入上述最终广域运行。
- `ruff check` 对本轮 Python 路径、三份 skill 字节一致性、`git diff --check`：通过。

原始运行记录保留在 `/tmp/zf-199-verification-20261106/`。
后续提交/合并不能把这些基线失败隐藏为绿色，真实部署也必须另行取得实际加载及效果回执。
本轮未运行真实 Provider／host 层，不将 mock dispatch 或模拟传输回执称为真实修复完成。

## 后续义务与触发条件

后续候选位于本 worktree 的 `backlogs/`，保持本地 proposed，不随本轮实现冒充已完成交付。
候选分组为：加载版本回执；公共故障协调；全 handler/跨代根因继承与原失败探针；
方法产物符合性；统一停止/进度回执；存储与性能矩阵；真实 Provider/宿主 canary。
各候选绑定上表未完成编号和可检查验收。真实 canary 在明确目标项目、Provider 与宿主策略后执行；
本记录本身不授权部署或修改 LS 业务合同。
