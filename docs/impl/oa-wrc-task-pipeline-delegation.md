# OA + WRC 的 v4 委派与证据闭环

本实现对应 [Plan / Task Map / 调度手册](../manual/13-plan-task-map-orchestrator-dispatch.md)，基线为 dev c6a92a2f。OA 提出语义策略，WRC 校验并执行；不增加第二套 canonical Task/Graph。

## 委派如何影响执行

`pre_impl` / `run_start` 接纳的 execution plan 与 Run、Task Map generation 和 Plan Artifact Package ref/digest 绑定。`task_pipeline_runtime` 调用 `admitted_pipeline_delegations`，再将结果交给 `TaskPipelineReconciler`。WRC 实际派发前仍执行 Task contract、依赖、背压、租约和输出协议校验。

OA work unit 用 `task_ids` 限定任务，用 `stage_ids` 限定 `impl`、`verify`、`acceptance_review`（兼容 `implementation` 与 `<flow>-lanes-<stage>`）。未限定任务的 stage unit 适用于当前接纳的 generation。一个 task-stage 的重叠委派拒绝执行；损坏或身份不匹配的工件也拒绝执行。

角色选择顺序：

1. OA `preferred_role_refs` 中仍在对应执行池的兼容空闲实例，按声明顺序。
2. 兼容的历史 task-stage 会话实例。
3. 对应池中的其他兼容空闲实例，按稳定顺序。

OA 角色偏好是软约束；`capability_refs`、Task 能力要求和执行池范围是硬约束。忙碌或能力不符时不抢占、不绕过容量。调度 operation payload 和 `task.pipeline.stage.dispatched` 中的 `orchestration_assignment` 保存计划 ref/digest、work unit、角色偏好、最终角色及回退原因。已运行的 operation 不会因后续选择重新派发。

OA execution plan 的 graph/edges 不替换 Task Map 的依赖；`skill_refs` 也不授权临时安装或执行未配置的技能。改变任务依赖、scope 或执行配置须走既有计划修订与审批。局部修订保留的任务继续使用自身已接纳 generation 的委派。

## 阶段结果回流

正常结果在已启用的 `stage_barrier` / `pre_closeout` 检查点批量进入 OA 的 canonical source manifest 和 required reads。聚合阶段不会把每个 lane 的完成变成全局 LLM 等待点。

v4 Impl/Verify/acceptance 的结果通过已接纳的当前 Task generation、最新 stage operation 和结果摘要校验筛选；有新 attempt 时旧结果不再作为当前结果。局部修订中未替换任务的旧 generation 证据继续保留。Judge 等既有聚合结果也进入来源集合。其他 Run、无 Run 身份的历史结果及 OA 自己的语义决策不能冒充本 Run 的执行结果。

## 受控调整

语义异常继续经 Run Manager / 已配置 `semantic_failure` 检查点。指定 Run 的恢复请求不得回退到另一个 Run 的 Plan；已声明 generation 过期或失败事件属于另一任务/Run 时拒绝创建检查点。

OA 对已 settled、当前 Impl operation 的精确 `rework`，经 decision admission 后转换为 v4 的下一次 Impl operation 输入，保留反馈和原有最大返工次数。只有存在 kernel `decision.applied` 回执的指令才可消费；活跃 successor、旧 attempt、旧 generation 或未应用的指令不触发重试。v4 reactor 不调用 legacy `_dispatch_rework`。

Verify 的常规失败继续使用既有有界 Impl 返工。涉及图变化、读者角色重绑定或超出当前精确 Impl 重试范围的调整，使用 `return_to_plan` / 人工处置，不能通过强行 legacy 派发实现。回计划请求仅触发 Planner 修订，不能自动生成 `task_map.ready` 或跳过 Critic/人工审批，也不覆盖其他运行中或完成任务。

## 启用与验证

本批不修改活跃项目的冻结配置，也不默认启用所有 OA 检查点。希望 OA 实际委派的 v4 Run，需要在 `zf.yaml` 相应 flow policy 中启用 `semantic_control` 和 blocking `pre_impl`；正式 YAML 入口仅对 `prd` / `issue` / `refactor` 的 `standard` / `full` 路由、带显式 `pilot_id` 的策略开放 `plan_candidate`、`pre_impl`、`semantic_failure`、`pre_closeout` blocking。其他检查点保留 shadow，不能把直接构造配置的单测当作 YAML 准入证明。必须通过新的受控 Workflow 提案或既有配置修订流程生效，不能手改运行工件。

验证入口：

- `tests/test_task_pipeline_orchestration.py`：OA 选择、忙碌回退、能力/依赖边界、重放及损坏计划。
- `tests/test_orchestrator_agent_results.py`：当前 Impl/Verify/Judge required reads、跨 Run 隔离、旧 attempt 和局部 generation。
- `tests/test_task_pipeline_oa_rework.py` 与 `tests/test_orchestrator_agent_semantic_failure.py`：精确返工、重放、预算、过期拒绝及局部重规划。
- `tests/e2e/test_oa_wrc_dispatch_mock_e2e.py`：真实 WRC 入口、隔离 Git worktree、实际 briefing/operation/TaskStore、记录型 transport 和重启去重。此层不调用真实 Provider，不作为模型语义判断质量证明。


## Hello World 实测后的边界修复

`workflow.plan_approval: true` 时，OA `adopt` 只满足语义采纳。Kernel 回到同一 Plan Artifact Package 的审批门，生成 `plan.approval.requested`，人工批准前不能孵化或派发 Impl。历史 `semantic_control: true` 的 `plan.approved` 不作为人工批准；关闭人工审批时仍保留既有自动推进。配置入口回归与真实运行需要分别验证这两种路径。

同一 `orchestrator` 会话一次只接收一个 typed operation。后续检查点保留为账本中的 requested operation，前一项结算后由 Kernel 周期再次派发；重启不依赖进程内队列。reserved/running/suspended 的 operation 保留会话占用，原 operation 的受控恢复重试不被自己的占用阻挡。普通 Layer 2 通知在 typed operation 活跃期间暂存，不覆盖其对话。

Codex 当前使用 `tmux`。`stream-json` 仅准入 `claude-code`，包含逐副本 backend 校验，错误在 loader 中报告，不等到 provider 启动后误入 Claude SDK。

高频 provider 活动事件可复用文件身份和版本未变化的 RoleSessionRegistry 读快照；其他 writer 修改或原子替换文件即失效。heartbeat 写仍走原有锁内重新加载和原子写，不缓存 canonical 写入。注册表优先采用 PyYAML 的 C safe loader/dumper，缺少扩展时回退 safe 实现。相同状态、任务、attempt 和 context ratio 的 usage liveness 在既有 60 秒窗口内不重复写入；任何上述字段变化立即落盘。费用事实、租约和事件顺序均不折叠。

回归覆盖 `tests/test_activity_liveness.py`、`tests/test_orchestrator_agent_operations.py` 与 `tests/e2e/test_orchestrator_agent_semantic_control_mock_e2e.py`。本轮 backlog 与实际验证结果见 [任务记录](../../tasks/done/2026-10-26-0339-hello-pipeline-validation-fixes.md)。

Autoresearch 实验配置的 `zaofu-skills` 和 `yoke` 来源优先绑定实验 checkout 的本地目录；本地不存在时才回退安装来源。存在本地 yoke 时显式声明来源，避免隐式 fallback 同时命中 driver 的副本。客户自定义来源保持原声明，严格碰撞校验不关闭。`tests/test_autoresearch_controller_skill_sources.py` 与实际 11 角色默认模板预检覆盖这一恢复分支。

### 高并发下的派发归属读取

日志看门狗查询角色活动任务时，派发归属使用活动账本的增量只读投影，通过 EventLog.read_from_offset 消费完整新增行，不再为每个角色重复搜索全部工具正文。保持原有 task/fanout 派发、重分配、重排和终态释放语义；日志替换、截断或轮转后重建，半行写入不提前消费。该缓存不是 canonical store，不产生派发或修改任务状态。

### 候选 Reader 的停止与恢复

`zf recover workflow --state-dir <state> --json` 将因 `fast_stop` / `graceful_stop` 中断、且没有更新活跃 TaskAttempt 的 `fanout_reader_child` 列为 `redrive_interrupted_reader_operation`。核对 checkpoint 后，使用 `--resume-pending --checkpoint-id <id>` 经既有 WorkflowOperationService 准入 redrive；保留 operation/request/candidate 身份，由运行中的 Kernel 派发。已 settled 的 sibling 不重跑；未知中断原因、其他 operation 类型与活跃重试继续遵守原有门禁。仅重启进程不能把 suspended operation 当成新请求。

对应入口及竞态回归：`tests/test_workflow_operation_resume.py`、`tests/test_workflow_resume_batch.py`、`tests/test_call_result_runtime.py`。临时运行的精确收尾见 [按 Run 收尾](../runbooks/scoped-fast-teardown.md)。

### Autoresearch 实验续验

真实 loop 的 eval/指标显式绑定实验配置解析出的 state_dir，在清理生成配置前记录；父项目仍持有诊断与改进上下文，不能用父 Run 的完成数证明内层实验完成。实验 PRD 输入即使被 checkout 忽略规则覆盖，也仅强制加入精确输入路径，提交不吸收其他暂存文件。Codex 反思通过 stdin 执行，审批参数位于顶层 `codex -a never exec ...`，解析或 provider 失败仍保留 unknown 与原始错误。

续验与修复账目见 [本轮任务](../../tasks/active/2026-10-26-0507-hello-pipeline-remaining-validation.md)。
