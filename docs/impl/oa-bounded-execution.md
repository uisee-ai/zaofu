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

## 只读观测

```bash
zf trace dispatch --run-id RUN-ID --state-dir CONFIGURED-STATE
zf trace execution-graph FEATURE-ID --summary --state-dir CONFIGURED-STATE
zf trace workflow-operation OPERATION-ID --state-dir CONFIGURED-STATE
```

API：`/api/task-pipeline/summary`、`/api/projects/{project_id}/task-pipeline/summary`，
可用 workflow_run_id 查询参数限制 Run。复用现有访问控制。
Task 生命周期计数保持原意，execution_statuses 单列 active/blocked/ready/waiting/done。
capacity 明确是项目共享容量，摘要不预留槽位，不作为可直接写入的 runtime truth。

## 验证

`tests/test_task_pipeline_dispatch_summary.py` 覆盖 CLI/API 和读取边界；
`tests/test_orchestrator_agent_execution_policy.py` 覆盖权限、合同变化与局部升级；
`tests/test_orchestrator_agent_execution_review.py` 覆盖 Run Manager 实际入口与去重；
`tests/e2e/test_oa_bounded_dispatch_mock_e2e.py` 验证隔离 Runtime 派发对照。
测试使用 mock provider，不能证明真实模型判断质量、产品验收或 FMS 已上线。
