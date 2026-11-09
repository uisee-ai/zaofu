# 200：Agent 恢复职责与统一执行实施记录

> 状态: done

设计：[200](../design/200-agent-recovery-ownership-and-unified-runtime.md)；既有能力与剩余义务：[199 实施记录](199-worker-recovery-implementation.md)。
授权：2026-11-06 在 199 worktree 实施全部 200，并以真实 Codex 和 v5 YAML 验证、修复、退出。

## 基线与验收

本轮复用 `wip/codex-20261106-design199`，保留既有 199 实现。该分支与 dev 分叉，仅合入 dev 新增的边界规则。
199 正文保持原样。200 的六项补充已同步到本 worktree。实际运行以隔离项目批准的 `zf.yaml` 为唯一配置；v5 模板不替代运行快照。
不以“已生成 backlog”“测试已定义”或“请求已发出”作为实现完成。所有条目需实际调用方及效果证据；真实测试在确定性回归通过后启动。

## 批次与覆盖

本批八个任务按验证结果归档至 `tasks/done/2026-11-06-1610-200-*.md`。

| 包 | 200 范围 | 199 追踪 | 当前状态 |
| --- | --- | --- | --- |
| 01 恢复步骤、效果与交接 | §3/4/8.3/9.1/9.3/9.4 | A01/A02/A04/A05/B01/B02/B04/C02/C05 | done |
| 02 证据路由 | §5/6/7/15.3 | R04/R08/A03/B05/B06 | done |
| 03 运行中求助 | §4.3/6.1/8 | R02/R03/R14/B01/B03 | done |
| 04 共享故障与延续 | §5.1/9.2 | R13/R14/A02/B04/C02 | done |
| 05 边界与方法 | §10/15 | R05/R06/R07/R08/R09/B05/B06/C01 | done |
| 06 入口与加载回执 | §2/4.4/11/13 | R01/R10/R11/R12/R15/C05 | done |
| 07 存储、监督与容量 | §4.2/4.3/6.1/12/13 | R14/A05/B02/C03/C04 | done |
| 08 真实 v5 闭环与对账 | 全部验收和指标 | 全部 31 项 | done |

原 `backlogs/2026-11-06-0928-199-11..17` 已由本批次接续，标记 superseded，保留原始范围。
八个工作包的已批准验收均完成；软件实现、真实宿主和真实模型证据分别签收。
完整的 [199／200 逐项对账](../../reports/2026-11-06-200-recovery-validation/design-traceability.md) 保留未覆盖的实机资格边界，不能据 8/8 推导所有生产故障已验证。

## 问题与验证记录

复现、入口、修复及结果见下文；去密证据、精确提交产物和检查脚本见 [验证报告](../../reports/2026-11-06-200-recovery-validation/README.md)。
实现提交：`b1b39758 feat: bind agent recovery steps, guidance, and shared effects`；
真实验证修复：`115e450d fix: route worker assistance without waiting for periodic recovery`、
`768adb08 fix: retain resident ownership across controller restarts`。

## 执行入口迁移记录

| 旧入口 | 复用入口 | 范围与切换 | 在途与回退 |
| --- | --- | --- | --- |
| WRC 正常前沿 | 既有 reconciler/operation/owner fence | v5 保持原 owner，无切换 | 持久化 generation owner 不变 |
| RM pending / Agent 建议 / 操作员恢复 | `action_gate_reason` → `recovery_action_admission` → 既有受控执行器 | 共用准入，保留各动作自身权限 | 重放原请求；未知副作用不盲重试 |
| RM 建议的 autoresearch / reflect / repair 分支 | 建议消费者在分支前复核同一 action gate 和 operation precondition | 9 个原先失败的在途案卷/人工等待/旧前提反例修复后通过 | 暂不具备执行资格的建议保留 deferred，不把未执行标作已交付 |
| triage 动作名 continuation 特例 | `admitted_recovery_continuation` 基于前置结果的共同准入 | 完成正例、重复、旧版本及暂停负例后切换 | 旧事件兼容读取，不删除账本 |
| WRC known-fault redrive | 既有 `task_pipeline_recovery` 和 operation guard | 仅回归，恢复资格不变 | 禁止旁路恢复回执和 owner fence |

## 真实 canary 冻结条件

使用全新 `/tmp/zf-200-…/` 项目，真实 Codex；保留 v5 WRC、blocking Pipeline、独立 Verify、串行集成和有效预算。
任务必须经正式入口、真实 Scan/Plan/Impl/Verify/Judge 及 Kernel 终态，独立检查产物；诊断不得直接改写运行态制造成功。
故障注入与采证驱动不人工“继续”或重派；必要配置/授权缺失明确记录，不能用 mock 结果替换。
冻结具体配置、输入、验证脚本和采证窗口后才启动。证据归档后通过既有 stop 清理临时进程与状态。


## 当前实现与合同映射

| 信息/约束 | 字段与生产者 | 准入与实际消费者 | 缺失/失败处理 |
| --- | --- | --- | --- |
| 当前恢复步骤 | RM `effect.pending` 的 request/checkpoint、continuation_expected_events | recovery_step → recovery_case → RM executor | 旧结果、重复消费、身份冲突阻止新副作用；历史 correlation 可以是请求 ID，不猜成 Run |
| RM 诊断结果 | 固定 recovery_context_ref、resident.prompted、agent.recommendation | bind_recommendation_to_request → 既有策略/共同动作准入 | 响应角色、原请求及版本不匹配拒绝；观察不能代替指定交付 |
| 运行中求助 | `recover assistance request/read`、必需 sidecar、operation/attempt/lease | reactive/periodic 共用 RM reconcile；OA advisory dispatch；原 Worker read | 旧 attempt/失效 lease/过期答复拒绝；消费者不可用或超时产生 task scoped 等待 |
| 求助派发重放 | worker.assistance.dispatching、原 request_id | Orchestrator run_once 的 durable pending 扫描 | 忽略重复唤醒；已认领但发送效果未知不盲重发，保持超时接管义务 |
| 同资源修复 | `recover associate` 提交 Agent 的关联判断；resource/version/repair_step、成员与 owner | 原子 sidecar + kernel association event；RM 共同锁/准入 | 同资源未证明效果时拒绝另一修复；换步骤 ID 不绕过在途修复；各 Task 不批量关闭 |
| 问题跨版本延续 | problem_continuation + 原 admitted failure_operation_ids | 校验来源 case/run/generation；WRC 与 Pipeline 投影共同消费 | 只有真实语义失败计数；缺失/错误绑定拒绝，不改旧 operation 或继承旧权限 |
| 效果与交接 | effect_expected_events/required_sequence、显式 effect_bindings | RM effect reconciler → RecoveryCase 投影 | 普通进展/诊断完成不结案；显式绑定缺失/失效不通过；暂停不消耗效果期限 |
| 当前加载版本 | session/loop.started 的 runtime_load_ref | start 实际入口，原子必需 sidecar | 区分 source tree、内存消费者 code hash、有效 config、skill lock、角色/backend；不是模型遵从性证明 |
| 宿主恢复资格 | `recover host-check`；既有启动/停止/暂停/取消/终态事实 | 外部 systemd ExecCondition → CLI start 的 loop lock/process guard | 未授权初次启动、明确停止、暂停、终态、损坏账本拒绝重启；不写业务状态 |
| 损坏账本 | EventLog decoder 的 event.malformed + ledger_integrity=unknown | WRC owner facade / RM action admission / host-check | 与普通 producer 字段拒绝区分；保存原字节及诊断，停止依赖未知账本的副作用 |

### 方法与约束能力

canonical `skills/zf-yoke-dev-worker-role-context`、`zf-yoke-orchestrator-role-context`、
`zf-plan-execution-readiness` 已更新并同步两份分发副本；TaskAttempt briefing 给出带当前身份的求助命令。
RM 实际 resident briefing 要求固定请求交付、共享故障关联及副作用先对账，不以只读观察代替修复职责。

| 约束 | 性质与实际边界 |
| --- | --- |
| 已准入 Task contract scope / operation scope | 既有受控写入 hook 与结果准入；不等同任意 shell 的 OS 沙箱 |
| role.blocked_paths / max_steps | 当前是方法提示，briefing 明确其性质；不虚构 step 计数器或新增 v5 上限 |
| role.allowed_tools | Claude 与 Codex backend 支持不同；Codex briefing 明示没有等价原生工具白名单；controlled action 权限仍生效 |
| operation / run 预算 | 保留 v5 的既有 policy 与机械预算门；不把 token/time=0 改成新的全局上限 |
| 依赖与取消/替代 | v5 原 done 门保持；Legacy TaskStore/writer 不再将 cancelled/superseded 充作产物满足 |
| 合同质量 | 可选 ContractQualityD 仅作结构 lint，输出 semantic_quality=not_assessed；v5 仍不启用该可选门 |
| 环境准备 | 在正式 contract.verification 中明示命令；删除 ContractD 的关键词安装/跨工作区链接隐式操作；Task Map 不按 pip install 类别一律拒绝 |

### 满载与监督选择

保留 v5 Impl/Verify 8/8、Pipeline 16、背压 8/2。只读指导使用既有 OA/RM 会话，不占新 Impl 槽位。
需要新写入者时先结算旧 attempt，再经获准的 OA/操作员 rework 意图进入既有队列；不得直接释放活跃 lease。
测试证明满载时旧故障 attempt 结算后能在原容量内返工，无须等待其余七个任务先成功。
已有 `_bounded_rework_order` 交替普通/返工任务；独立 Verify 和串行集成门保持。

宿主监管由本轮隔离测试显式安装的 transient user-systemd unit 提供：Restart=on-failure、ExecCondition=host-check，
通过 CLI `start --control-plane-only` 保留原 tmux worker 会话。不是为正式 dev 安装服务，也不是第二业务调度器。
真实 SIGKILL 两次：第一次自动恢复且唯一 process guard；第二次在 pause 后不再启动。测试结束停止并移除临时 unit/session。
`stop --force` 补写既有 loop.shutdown_requested，避免宿主把操作员强停误判成意外崩溃。

## 本轮验证记录

- 第一轮广域：14,513 passed / 19 failed / 20 skipped；6 项沿用 199 记录的基线失败。
- 新增回归中，correlation=request_id 的旧分诊回执兼容、普通 producer 格式拒绝被误当全局损坏，已修复；相应恢复链 18 passed。
- 存储/签名/缓存/WRC 组：68 passed；CLI/attempt/briefing 等组先通过 110 项，满载 fixture 补显式获准 rework 后复验通过。
- 受控共享关联、加载/停止、容量和 host eligibility：18 passed。
- sentinel：53 passed；flow smoke：10 passed。
- 实际 systemd/tmux host 测试：1 passed（12.25s）；无真实模型调用，不充作 Provider 效果证据。
- 成对性能：各 5 次预热、30 次测量，4,064 事件、16 tasks，同 v5 配置；基线 2af8301f 的独立 source archive 与当前代码各在新进程运行。
  RM projection p50/p95：247.86/251.70ms → 251.19/254.15ms；峰值 RSS 68,936 → 56,720KiB；EventLog read_all 各 30 次。
  两端 pending_actions 均 0；符合预先冻结的 p95 ≤ 1.5×基线+20ms。此基准不代表真实 Provider 吞吐或所有故障风暴。
- 原始驱动及选定日志已归档到验证报告；其中 source 路径保留本次运行值，重新启动真实 Provider 前需重新建立隔离目录。
- 最后广域：`dev-verify.py run --base dev`，14,539 passed / 6 failed / 20 skipped（203.29s）。六项均为 199 已在隔离基线复现的失败：
  公开导出缺 `tools/export-public.sh` 的四项、缺内部 prompt 的一项，以及 Feishu 旧 fixture 未建立 PRD readiness 的一项。
  该广域命令仍非全绿；没有删除、跳过或修改这六项来制造通过。
- 后续自动层单独运行：serial 10 passed、sentinel 53 passed、flow smoke 10 passed；相关调用闭包 341 passed。
- 最后准入修复组 236 passed；共享恢复改为实际 SQLite 工具/探针 fixture 后相关 40 passed，七项恢复、一项 schema 不兼容保持失败。
- 58 个变更 Python 文件 `ruff check`、三份 skill 的两套镜像字节一致性、manual currentness、`git diff --check` 均通过。

## 真实 canary 冻结记录

临时项目：`/tmp/zf-200-real-20261106T170450Z-dm64b2x4`；基线 `9df5404`。
需求为标准库客户名册 CSV 导出，要求表头/顺序/转义/空列表/错误输入和至少 5 个非空 unittest。
额外检查 Impl 当前 attempt 的一次 OA 只读求助，业务 Verify 与 harness 证据分别核验。
v5 改动仅项目/状态/技能路径与会话绑定、适用 Python 静态命令、隔离 dev 的获准自动 ship；
保留 WRC、blocking Pipeline、独立 Verify、串行 CAS、frozen_exact_commit、resident RM 和预算。
正式入口为 workflow-request → 必要 config-apply → workflow-submit；不手写 Agent 结果或终态。
使用真实 Codex 0.163.0 / `gpt-6.1-sol`，最终业务终态、产物复验和退出清理已核验，详见下节。

### 真实试跑问题记录（按实际状态更新）

| 编号 | 观察与原因 | 修正及验证 | 当前结果 |
| --- | --- | --- | --- |
| REAL-01 | 第一轮 Web TestClient 的准备器读取宿主 WorkspaceRegistry，尝试加载 LS 配置；该配置因未知参数在 loader 阶段拒绝 | 验证驱动设置独立 `ZF_WORKSPACE_HOME`，保留原始错误；首轮经 CLI `simulation.done` 和 `stop --include-run-manager` 退出，再建干净项目 | 第二轮未出现跨项目加载；不是修复或运行 LS 业务 |
| REVIEW-01 | 最后入口审计发现 RM 建议的三个诊断/修复分支未经过受控动作的共同准入 | 先复现 9 个失败反例，再补当前 case/human/precondition 检查；相关组 236 passed，sentinel 53 passed | 用现有 source_repair_restart_actuator 重载隔离控制面；新 runtime-load 回执证明新消费者已加载，原 Worker 会话保留 |
| REAL-02 | 真实 Worker 17:35:43 建立求助，直到 17:38:44 的 RM 周期扫描才建立 OA 路由，首跳多等待约 180 秒 | 复现“只有 REQUESTED、尚无 RM sweep”的回归失败；主循环复用持久化 reconcile 并加共用锁，使 reactive/periodic 不重复发布路由和关闭回执 | 修复组 249 passed；原请求已由周期兜底派发，原延迟如实保留，不将后续代码重载当作该次延迟已消失 |
| REAL-03 | 业务完成后执行正常 stop，发现三次控制面重载留下的旧 Autoresearch sidecar；父进程持有的 flock 随父进程退出而释放，新启动覆盖仍活跃的旧 pidfile | 宿主反例先复现重启后 PID 改变；sidecar 继承 singleton lock，控制面退出不释放其活跃锁；跨进程停止精确匹配完整 resident 参数及 state dir，避免仅凭共享 worktree 路径识别进程 | 调用闭包 67 passed；真实 SIGKILL／systemd 重启保留同一 sidecar，正常 stop 检查整个进程组退出。旧版本遗留的三个进程按原启动事件和精确参数由操作员清理，不能宣称新代码自动找回了被覆盖的旧 pidfile |

第二轮项目 `/tmp/zf-200-real-20261106T171651Z-8qffn5an`，需求摘要与首轮相同；
正式 request `workflow-4ef709e12ce26efd`。Scan、Plan、Plan Critic、Impl、任务 Verify、8 路候选 Verify、Discovery 与 Judge 均已正式交付。
Kernel 于 18:20:22 UTC 产生 `run.goal.completed`（`evt-f6dac67e0122`）；候选 `6661dc6` 经受控交付形成隔离项目 dev 提交 `b9219864`。
外部冻结脚本分别对候选和实际交付提交执行 8 个 CLI 行为检查及 7 个 unittest，均通过。
运行期业务能力使用已加载的 `115e450d`；REAL-03 为收尾时发现的后续生命周期修复，另由真实宿主回归验证，不伪称完整业务重新跑过该修订。
本次操作员为部署已验证修复重载过控制面，不能把这一操作描述为无人干预的宿主自动恢复；
宿主自动恢复资格由前述独立 systemd 故障注入测试证明。

## 设计覆盖与证据层级

下表区分软件实现/确定性回归、真实宿主和真实模型结果；已有能力以回归保留，不重复实现。
200 §6 的问题分类是职责合同，不承诺每种未知业务故障都能自动修复；未知原因、缺权限和缺证据必须有当前 owner 与合法出口。

| 200 范围 | 实现及实际消费者 | 已有验证／本轮增加的验证 | 资格边界 |
| --- | --- | --- | --- |
| §1–3、§10 决策/执行分工 | Agent 方法、受控动作、WRC 唯一 owner；语义结论不直接写 Store | OA admission/execution policy、fault matrix、skill materialization 回归 | 模型实际遵从性按每次运行采证 |
| §4.1/4.2/4.4 共同准入及迁移 | RM pending、建议、operator 复用 case/human/currentness；保留 operation-v2 fence | `test_run_manager_action_admission`、`test_workflow_redrive_race`、`test_task_pipeline_oa_rework` | 不删除 legacy，也不迁移活跃 generation owner |
| §4.3 满载及公平性 | 既有分池/背压/返工交替；只读 OA/RM 不新增 Impl writer | `test_task_pipeline_capacity_recovery`、`test_task_pipeline_reconciler`、`test_worker_assistance` | 容量证明为确定性调度；单任务真实 canary 不代表 8+8 实机吞吐 |
| §5/6/7 失败与诊断 | v5 unknown → 固定输入 → 当前 RM 责任；typed known policy 保留 | `test_failure_diagnosis_routing`、`test_provider_stop`、`test_task_context_recovery` | 未用关键词猜测项目数据库修复方案；模型根因正确性须实际探针 |
| §5.1 共享故障 | 关联准入、required sidecar、共同动作锁与资源版本/步骤 fence | `test_recovery_association`：实际 SQLite 修复一次、八个消费探针、7/1 分别结算 | 修复工具及 Agent 关联决定是 fixture；不是八个真实模型的 DB 排障证明 |
| §6.1 监督与接管 | 独立 host-check + 已安装 transient systemd；请求 timeout/task 等待；异步 reflect | `test_design200_host_supervision`、`test_host_lifecycle`、resident obligations/reflection 回归 | 仅为隔离宿主安装；未给正式 dev 安装服务 |
| §8 当前 attempt 求助 | request/read CLI → RM reconcile → OA 实际派发或 resident → 当前 Worker 读取 | `test_worker_assistance`，真实求助、回答、读取和下游完成回执 | 不改合同、不转移 lease；重派建议仍需既有受控交接 |
| §8.3 最小请求合同 | 上文信息映射；固定问题、输入 digest、身份、owner、期限、结果类型 | immutable evidence、wrong responder、旧 attempt、迟到、消费者不可用回归 | 缺少当前必需事实拒绝；保留 legacy 的明确兼容读取 |
| §9.1/9.3 步骤与效果 | recovery_step 通用前置结果消费；RM effect reconcile；暂停时钟 | `test_recovery_step_admission`、handoff effects、triage caller | 诊断/普通进展不是结案；未知副作用不能盲重试 |
| §9.2 问题延续 | Agent 关联已准入失败 → 当前 Pipeline failure accounting/projection | association 真实 consumer 测试；跨任务/代次计数且不改旧 operation | 历史 pass 与旧权限不继承；无依据关联拒绝 |
| §9.4 证据适用 | 显式 effect_bindings；既有 exact-commit/contract/package admission | effect binding 负例、candidate currentness、scoped verify、acceptance replan 回归 | 报告/事件/schema 通过不等于产品验收充分 |
| §11/13 迁移及交付 | 每个新模块接入 CLI、RM tick 或 Orchestrator；startup code/config/skill 回执 | CLI start 测试、实际重载与 host 回执、成对性能 | 源码提交、加载、业务效果分别记录 |
| §12、§15 兼容与硬/软约束 | legacy cancelled 不满足产物依赖；质量 lint 去语言启发式；环境准备显式；方法约束声明 | discriminator、TaskMap、dependency、hook/预算和 skill 影响闭包 | 未支持字段不宣称强制执行，不提高 v5 配额 |

### 199 的后续对账方法

199 原实施记录的 14/15/2 是历史批次结论，本轮不改写它。
本轮新增加载回执（R01）、共享资源协调（R13）、运行中求助（R02/R03/R04）、
跨版本失败关联（B04）、存储拒绝和成对基准（C03/C04）、真实宿主（A05/B02/R10），
以及真实 v5 业务运行证据（R05/R09/R11/R15/B06）。
R07/R08 的复杂 DAG/语义矛盾评测、R14 的多 Provider/网络数据库集中故障、
任意外部 shell exactly-once、所有未来修复 handler 的全故障窗口，不由这一最小运行证明。
最终完成度应按本批八个验收包及上述证据层级分别报告，不能将一条 CSV 成功升级成 199/200 全部场景的生产保证。

## 最终效果与清理

- 从正式 invoke（17:16:54 UTC）到 `run.goal.completed`（18:20:22 UTC）为 **3,808.33 秒，约 63.5 分钟**。业务 Task 和根 Task 都为 done，19 个 operation 均 settled；真实 Impl 只启动一个 attempt。
- Plan/Critic → Impl → 任务 Verify → 串行集成 → 8 路候选 Verify → Discovery → Judge → 本地交付全链完成。最终候选 `6661dc6`、实际交付 `b9219864` 分别通过外部冻结脚本的 8 个行为检查与 7 个 unittest。
- Discovery 另运行 3 个独立测试／14 次 CLI 调用，零阻断缺口；Judge 关闭五条必需 claim，Kernel 决定最终完成。没有手写 passed、修改 canonical Store 或人工重派业务 Task。
- OA 求助保留一个当前 operation/attempt/lease；原回答与后续关闭、完成分别留证。操作员部署修复时重载过控制面，这条业务运行不是“全程无人工介入恢复”的证明。
- 收尾新增 sidecar 修复的调用闭包 67 passed、真实宿主回归 1 passed（14.22s）、sentinel 53 passed。广域 14,539/6/20 的结果发生在最终两项实跑修复之前；后两项按影响闭包复验，未再宣称全套通过。
- 小任务开销仍高：Impl 721s，任务 Verify 156s，最慢候选 Verify 590s，Discovery 952s，Judge 298s。固定 8 路候选验证与较大必需输入带来读取及压缩开销；本轮证明功能闭环，不签收吞吐／费用优化。
- usage 为订阅模式，397 条记录未定价；USD=0 不能证明零成本或实际金额预算覆盖。保留原 v5 限额，没有扩容、提高预算或删除必需证据换取成功。
- 已 `simulation.done` → `stop --include-run-manager`，并核对所有归属进程、tmux 会话和隔离目录退出。REAL-03 旧进程清理单独留证；三个早期／实际业务临时项目均删除，宿主测试的 transient unit 亦停止。
- 199 正文 SHA-256 仍为 `a5e8b17c629159db55436f71608bad324710d43e3ac3def00ed90acb870082eb`。199 开发 worktree 保留，仅本地提交；未合并到 ZaoFu dev，未 push。

## 后续部署：2026-11-06 LS-v1 恢复

上述未合并状态是 canary 收尾时的记录；199/200 后续已通过 `a856ad0b` 合入 dev。
LS-v1 的既有配置启用了 `planCriticEnabled: false`，依赖此前尚未进入 dev 的
`bc8c62b8` 功能；恢复部署保留该显式配置，补入原实现，避免改变运行合同。

另一恢复入口缺陷：历史 `human.escalate` 没有显式 decision_token 时，RM 投影
按事件身份生成 token，但 `human-decision-dismiss` 只查 payload token，导致
操作员无法通过该受控入口确认已经过时的等待。现在复用同一身份规则；历史格式
额外要求精确 `escalation_event_id`、owner `approval_ref` 和非空理由。
原因是否过时由操作员判断，runtime 不解析描述猜测业务状态，不自动批准计划。
动作只写 acknowledgement，由 RM 消费后关闭等待；不改旧事件、任务或验收结论。
回归覆盖缺绑定/授权/理由、错误事件、重复提交，以及实际 RM 消费后的等待清除。

恢复实跑还发现 Discovery 将旧执行代次的已准入计划包与最新待审核包比较，造成
`stale or missing plan_artifact_package_id`，角色启动前即被拒绝。Discovery 现在
与 Plan/任务执行保持一致，按声明的已准入 generation 查找完整包身份；未知代次、
错误 digest 继续拒绝。不采用尚未批准的新计划，不删除版本门。回归经过真实
`prepare_call_operation` 调用链，覆盖保留基线与错误绑定。
LS 的语义恢复使用 `global-rescan` 输出合同；同一 Discovery 事件的普通
`workflow-read` 和 `global-rescan` 两条路径均需保留该代次绑定，回归分别覆盖。
