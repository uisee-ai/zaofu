# OA 有界执行与统一调度摘要

本能力复用 [OA/WRC 委派](oa-wrc-task-pipeline-delegation.md) 和唯一 Task contract。
任务生命周期、执行阶段和独立验证结果分别保留。OA 的执行意图经 Kernel 接纳后
影响角色偏好、就绪任务顺序或精确 Impl 返工，不增加日常 Planner/Critic 回合。

## 配置

在既有 PRD/Issue/Refactor standard/full pilot 的 flow policy 中显式配置：

```yaml
workflow:
  orchestration:
    flow_policies:
      prd:
        mode: semantic_control
        pilot_id: bounded-execution-pilot
        checkpoints: [pre_impl, semantic_failure]
        checkpoint_policies:
          pre_impl: shadow
          semantic_failure: blocking
        autonomous_actions: [delegate, continue, rework]
        review_blocked_tasks: true
```

这只是策略片段，须与完整 controller/kind/workflow 配置一起验证。新 Run 或既有受控
配置修订后生效，不覆盖活跃 Run 的冻结工件。`plan_approval` 保留原配置。
autonomous_actions 省略/null 沿用原 checkpoint 策略；空列表关闭直接执行动作。
review_blocked_tasks 默认 false，启用必须声明 autonomous_actions 与 semantic_failure。
未启用的 flow 与 Legacy 路线保持原行为；shadow 意见仍不能改变执行。
在 C 路线中，`pre_impl` 建议保持 shadow：WRC 继续消费已接纳的 Task Map 正常前沿，
OA 只记录执行计划建议；只有 `semantic_failure` 才能阻塞或触发有界修订。把
`pre_impl` 配成 blocking 会重新把正常派发变成 OA 的全局等待点，只有需要强审计的
高风险部署才应显式启用该门。

生产 Codex controller 的 reader/planner 默认使用可通过 `ZF_CODEX_MODEL` 覆盖的
`gpt-6.1-sol`、`medium` reasoning 和 120k 上下文
上限。真实 provider 会重复发送 briefing、artifact refs 和工具回执；所有角色默认
使用 `high` 容易在 typed result 提交前触发 operation token budget。专门的高风险角色
可以按角色显式提升，但应通过独立 canary 重新测量提交率、尾延迟和 token 消耗。

## 输入与执行

执行检查点增加不可变 execution-context 工件：原 Plan 的任务图摘要、当前调度状态、
目标 Task 合同/依赖/dispatch 摘要和 operation 绑定、动作许可。配置、执行摘要、
本次失败及恢复上下文为必读，其他 Plan ports 仍可按需读取。Plan candidate 仍执行
完整规划审查，不因该优化省略其来源。

OA 在 delegation 中可给出可选非负整数 dispatch_rank，较小值优先，仅重排已经就绪
的候选；原 bounded rework fairness、能力约束、背压和容量继续生效。执行图的 edges
不替换 blocked_by，工作单元不能创造 Plan 外任务。运行中的 operation 不被重分配。

接纳前核对允许动作、目标合同/依赖/dispatch 与最新 operation。心跳、费用、时间戳
不参与目标授权 hash，避免正常遥测使决策不断失效；真实合同、代次或目标变化拒绝
执行。原精确返工路径继续限制次数、活跃后继及当前 attempt。
return_to_plan 只走既有局部 Planner/Critic/审批路径；没有自签 Verify 或删除依赖权限。

## 增量唤醒

Run Manager 仅对显式启用策略、活跃 Run、当前 generation、已结束且未通过的 Impl
生成现有 orchestrator-rework-triage 动作。必须存在有效结果工件和来源事件；明确的
环境/Provider 故障留在原恢复路径。依赖等待、正常 Verify、活跃后继、已完成任务
和其他代次不触发。以 Run/generation/operation/结果摘要去重；相同输入已有请求
不因心跳或重启再次请求。不伪造 exhaustion，不增加返工额度。

首次已准入的失败 Impl 是独立审查，不套用重复失败的 exhaustion 次数门槛；
仍须绑定当前 Run、Task Map generation、operation 和结果来源，并在 OA checkpoint
再次核对目标。其他 Autoresearch 已占用诊断槽位时，独立的 OA 语义审查可以继续；
原诊断和熔断仍保留，人工决策门和受控重规划不被绕过。已超时的诊断即使携带
operation key，也必须进入失败结算；已被接受的活跃诊断继续按原活性窗口处理。
状态解释展示实际选中的后续操作；当诊断槽被其他 Autoresearch 请求占用时，
在 `pending_execution.diagnosis_wait` 列出等待原因和关联请求 ID。
被同一 Run 的后续 reopen 替代、且尚未桥接到 Autoresearch 的历史终态诊断，
不会继续占用单飞诊断槽；该判断复用 invocation 入口的相同来源/顺序校验。
已经桥接、排队或启动的请求仍保持占槽，直到存在其终态，其他 Run 的 reopen
和缺失来源都不推定请求失效；历史事件保持不变。

历史 `human.escalate` 若没有 checkpoint 和审批命令，仍阻止运行完成；
无进展熔断的 `needs_diagnosis` 可先创建只产出诊断建议的请求，避免等待
无法操作的旧通知形成死结。带明确 checkpoint 或审批命令的人工关卡仍阻止
该诊断路径；此例外不批准任务、不执行恢复，也不解除任何人工升级。
失败输入携带同一 Run 的最新用户目标及来源事件，和 execution-context 使用同一快照。
审查后用户目标变化时拒绝旧 OA 决策，按新目标重新排队一次；其他 Run 的更新不影响
当前决策。历史未绑定目标事件的检查点保持重放兼容。
任务结算后可清除 Task 的 active dispatch；失败审查改从当前已结算 operation 传递
原 attempt 身份。旧请求因缺少 attempt 被拒绝时，只在已找到同一 operation 的真实
attempt 且旧请求未携带该字段时重发一次；保留原拒绝，其他原因不自动重试。
绑定具体已结算 operation 的失败检查点使用该 operation 实际执行角色，而不是
用任务合同中的历史 owner 覆盖实际派发角色；未绑定 operation 的旧检查点保持回退。
若旧检查点因目标角色错误升级，操作员可用 `zf recover oa-review --escalation
<event-id> --reason <reason>` 预览并追加 `--apply` 请求一次新的 OA 审查。
恢复要求原升级和决策已准入、同一已结算 operation 的 attempt 一致且角色确有偏差；
保留旧结论，新检查点仍须独立决策，不直接派发返工或修改冻结合同。

局部 Plan 更新可能保留其他 Task 的旧代次。此时 semantic_failure 的请求、结果提交
和决策准入都按目标 Task 最近一次 `task.pipeline.generation.admitted` 绑定的不可变
Plan 包校验，不能用全局最新包误拒绝仍生效的任务。任务自身重新准入、包摘要不符
或 Run 不符仍拒绝旧请求/结果；非任务范围的 Plan 检查点继续使用全局最新包。
对应回归在 `tests/test_orchestrator_agent_plan_binding.py`。
Impl 结算后 Task 的活跃 dispatch 可以清空；OA 从已核对仍为当前且 settled 的
目标 operation 取回 attempt 绑定，不依赖任务继续占有执行槽位。
`return_to_plan` 的后续触发同样绑定已接纳 OA 决定的 Plan 包，不能重新替换为
全局最新包。恢复扫描会识别历史触发与该不可变基线不一致的情况；去重键包含 OA
基线和决定引用，使正确触发恰好补发一次。普通 candidate replan 仍使用最新包。
Plan-domain 的工件预注册也按输入声明的已准入代次核对历史包；Plan 输出仍需经过
后续审查和准入，读取基线不等于授予任务执行权限。未准入代次和摘要不符继续拒绝。
Owner 授权的 stage recovery 从同 Run、同 OA 决定引用的 Kernel 重规划记录恢复
基线，避免沿用旧错误触发中的包身份；历史未记录代次的包保留原兼容校验。

Discovery 的 gap amendment 同样按声明的已准入代次选择包内 task_map，不能将
较新的计划路径与旧任务代次拼接。后续 gap-only Plan Package 从
`supersedes_task_map_generation` 对应的历史包继承验收工件；Run、包摘要、前置
task_map 摘要及代次必须一致。此过程只修复证据绑定，不改变任务内容或验收要求。
回归覆盖 `tests/test_gap_plan_baseline.py` 的真实 bridge 调用与
`tests/test_plan_acceptance_rework.py` 的新旧计划并存场景。

## 只读观测

Reader 重启恢复按触发请求的 ledger 顺序判定计划替代；显式 owner 新代际或
redrive 可以重置局部 attempt 计数，不能只靠 attempt 大小比较新旧。
Run、stage、task-base recovery 范围仍须一致。恢复失联 reader 前核对同角色
当前派发 owner；已有另一未交付 reader 时，不强制置 idle、不重复排入 briefing。
测试见 `tests/test_reader_recovery_owner.py` 和 fanout hardening 的实际 housekeeping。

Owner 授权的 reader new-generation / retrigger 遇到历史错误携带的 OA
shadow 建议时，仅凭同 Run、相同不可变 descriptor 的 Kernel
`orchestrator.semantic.decision.shadowed` 且 `applied=false` 回执，将其保留为
`historical_orchestration_context`。Planner 仍能通过 manifest 读取该观察，
但它不成为 return_to_plan 授权。已有正式 replan marker、未知来源、不同 Run
或不同摘要均不走该修正；基线和历史工件的摘要校验保持。真实调用测试见
`tests/test_reader_shadow_recovery.py`。

```bash
zf trace dispatch --run-id RUN-ID --state-dir CONFIGURED-STATE
zf trace execution-graph FEATURE-ID --summary --state-dir CONFIGURED-STATE
zf trace workflow-operation OPERATION-ID --state-dir CONFIGURED-STATE
```

API：`/api/task-pipeline/summary`、`/api/projects/{project_id}/task-pipeline/summary`，
可用 workflow_run_id 查询参数限制 Run。复用现有访问控制。
Task 生命周期计数保持原意，execution_statuses 单列 active/blocked/ready/waiting/done。
capacity 明确是项目共享容量，摘要不预留槽位，不作为可直接写入的 runtime truth。

Run Manager 的 `monitor.task_evidence` 对在途任务分别展示最近派发、活动与结果
信号的事件 ID、时间及事件中已有的 Run/attempt ID；缺少标识时留空，缺少信号为
null。活动信号的 `ts` 是账本记录时间；直接用量或同任务来源用量可查时，
`usage_sample_ts` 与 `source_usage_event_id` 指向 provider 原采样时间及事件，
缺失来源时不推测采样时间。它只按明确的 task ID 关联，可能引用历史 Run/attempt，结果信号也可能是
阻塞或 worker 自报，不能充当当前代独立验收。没有 heartbeat 或结果均不直接触发
停止、重试或判定任务完成；resident 应结合执行摘要和合同作语义判断。

## 验证

`tests/test_task_pipeline_dispatch_summary.py` 覆盖 CLI/API 和读取边界；
`tests/test_orchestrator_agent_execution_policy.py` 覆盖权限、合同变化与局部升级；
`tests/test_orchestrator_agent_execution_review.py` 覆盖 Run Manager 实际入口与去重；
`tests/e2e/test_oa_bounded_dispatch_mock_e2e.py` 验证隔离 Runtime 派发对照。
测试使用 mock provider，不能证明真实模型判断质量、产品验收或 FMS 已上线。
