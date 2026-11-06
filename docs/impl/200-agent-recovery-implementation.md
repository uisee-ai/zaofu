# 200：Agent 恢复职责与统一执行实施记录

> 状态: active

设计：[200](../design/200-agent-recovery-ownership-and-unified-runtime.md)；既有能力与剩余义务：[199 实施记录](199-worker-recovery-implementation.md)。
授权：2026-11-06 在 199 worktree 实施全部 200，并以真实 Codex 和 v5 YAML 验证、修复、退出。

## 基线与验收

本轮复用 `wip/codex-20261106-design199`，保留既有 199 实现。该分支与 dev 分叉，仅合入 dev 新增的边界规则。
199 正文保持原样。200 的六项补充已同步到本 worktree。实际运行以隔离项目批准的 `zf.yaml` 为唯一配置；v5 模板不替代运行快照。
不以“已生成 backlog”“测试已定义”或“请求已发出”作为实现完成。所有条目需实际调用方及效果证据；真实测试在确定性回归通过后启动。

## 批次与覆盖

任务均位于 `tasks/active/2026-11-06-1610-200-*.md`。

| 包 | 200 范围 | 199 追踪 | 当前状态 |
| --- | --- | --- | --- |
| 01 恢复步骤、效果与交接 | §3/4/8.3/9.1/9.3/9.4 | A01/A02/A04/A05/B01/B02/B04/C02/C05 | active |
| 02 证据路由 | §5/6/7/15.3 | R04/R08/A03/B05/B06 | active |
| 03 运行中求助 | §4.3/6.1/8 | R02/R03/R14/B01/B03 | active |
| 04 共享故障与延续 | §5.1/9.2 | R13/R14/A02/B04/C02 | active |
| 05 边界与方法 | §10/15 | R05/R06/R07/R08/R09/B05/B06/C01 | active |
| 06 入口与加载回执 | §2/4.4/11/13 | R01/R10/R11/R12/R15/C05 | active |
| 07 存储、监督与容量 | §4.2/4.3/6.1/12/13 | R14/A05/B02/C03/C04 | active |
| 08 真实 v5 闭环与对账 | 全部验收和指标 | 全部 31 项 | active |

原 `backlogs/2026-11-06-0928-199-11..17` 已由本批次接续，标记 superseded，保留原始范围。
编号计数不是完成度；当前没有新的工作包达到 done。

## 问题与验证记录

实现时持续填写：复现输入、实际入口、修复、验证命令、结果、残余限制。自动化和真实 Provider 结果分别记录。

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


## 当前实现与合同映射（待最终验证签收）

| 信息/约束 | 字段与生产者 | 准入与实际消费者 | 缺失/失败处理 |
| --- | --- | --- | --- |
| 当前恢复步骤 | RM `effect.pending` 的 request/checkpoint、continuation_expected_events | recovery_step → recovery_case → RM executor | 旧结果、重复消费、身份冲突阻止新副作用；历史 correlation 可以是请求 ID，不猜成 Run |
| RM 诊断结果 | 固定 recovery_context_ref、resident.prompted、agent.recommendation | bind_recommendation_to_request → 既有策略/共同动作准入 | 响应角色、原请求及版本不匹配拒绝；观察不能代替指定交付 |
| 运行中求助 | `recover assistance request/read`、必需 sidecar、operation/attempt/lease | RM tick 路由；OA advisory dispatch；原 Worker read | 旧 attempt/失效 lease/过期答复拒绝；消费者不可用或超时产生 task scoped 等待 |
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
- 原始可重放脚本/日志位于 `/tmp/zf-200-verification-20261106/`，最终签收时归档去密证据。
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
使用真实 Codex 0.163.0，登录状态有效；运行进展及问题见下节，最终业务终态尚待核验。

### 真实试跑问题记录（按实际状态更新）

| 编号 | 观察与原因 | 修正及验证 | 当前结果 |
| --- | --- | --- | --- |
| REAL-01 | 第一轮 Web TestClient 的准备器读取宿主 WorkspaceRegistry，尝试加载 LS 配置；该配置因未知参数在 loader 阶段拒绝 | 验证驱动设置独立 `ZF_WORKSPACE_HOME`，保留原始错误；首轮经 CLI `simulation.done` 和 `stop --include-run-manager` 退出，再建干净项目 | 第二轮未出现跨项目加载；不是修复或运行 LS 业务 |
| REVIEW-01 | 最后入口审计发现 RM 建议的三个诊断/修复分支未经过受控动作的共同准入 | 先复现 9 个失败反例，再补当前 case/human/precondition 检查；相关组 236 passed，sentinel 53 passed | 用现有 source_repair_restart_actuator 重载隔离控制面；新 runtime-load 回执证明新消费者已加载，原 Worker 会话保留 |

第二轮项目 `/tmp/zf-200-real-20261106T171651Z-8qffn5an`，需求摘要与首轮相同；
正式 request `workflow-4ef709e12ce26efd`。Scan 两个真实 Codex 子角色及 Planner 已交付，独立 Plan Critic 正在执行。
本次操作员为部署已验证修复重载过控制面，不能把这一操作描述为无人干预的宿主自动恢复；
宿主自动恢复资格由前述独立 systemd 故障注入测试证明。
