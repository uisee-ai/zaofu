# Task Pipeline 上下文发布与恢复

Task Pipeline 在准备新的 provider operation 前，通过 TaskStore 重新读取当前 Task，核对其计算出的合同版本与派发快照一致，再经 write_task_doc 原子写入任务说明投影并进行 freshness 检查。发布失败或版本不一致时停止派发，沿现有 dispatch deferred/cooldown 路径处理，不生成产品失败结果。

投影刷新不回写 TaskContract，不修改已接纳计划、验收、任务状态或工作树代码。新的 task.doc.published 事件记录 dispatch、来源事件和三种版本。

worker 发现任务说明、briefing、快照之间版本冲突时，以 implementation result 的 failure_class/blocker_kind=task_context_stale 报告具体版本和证据。该类型进入 harness 修复分支，不消耗产品重规划重试。真实 upstream_contract_gap 仍进入语义重规划；本修复不依赖自由文本猜测、不重写历史失败事件。

故障修复后，监工核验当前 operation 已停止、说明投影与快照一致，再通过现有受控恢复动作/CLI 恢复。已批准 Task Map 的修订仍须通过正式 Planner/Critic、Plan Preview 和新 package 接纳；重启进程不等于恢复 Run，也不等于批准新计划。

验证：tests/test_task_pipeline_capsule.py 覆盖派发入口与真实 briefing/transport 调用边界；tests/test_task_context_recovery.py 覆盖分类与 Run Manager 重规划排除；保留 pipeline、TaskDoc、result submit 和 flow mock E2E 回归。

真实恢复时还发现：候选 Task Map 保留了草稿状态，Planner 预检放行、Critic 通过后才被 Package 门禁拒绝。预检现在也检查必需 Task Map / Source Inventory 中显式声明的 enrichment contract：只有 status=ready 且 enrichment=fulfilled 才能提交；无该契约的历史输入保持兼容。复用已有矩阵检查，不自动把草稿改为 ready，也不替代语义审核。tests/test_plan_enriched_port_readiness.py 覆盖两个端口，以及同一次 result submit 被拒绝后修正并重新提交的实际入口。

规划失败可能携带上一版本的 task_map/package 与失败任务 ID。批量恢复不得据此把 reader 的 Plan 协议失败当成实施失败并重发 task_map.ready。workflow_resume 将显式 plan_artifact_package_protocol、plan_candidate_review_identity、plan_candidate_preflight 继续交给 stage replan；原有实施失败/候选运行时恢复保持原路径。tests/test_plan_failure_batch_resume.py 覆盖带继承身份的实际恢复投影入口。

Plan 审批去重同时绑定任务集合与产物摘要（优先 Package digest，其次 Plan digest）。任务 ID 不变而 Package 更新时必须能产生新的审批单；同一版本仍保持去重。旧 plan.minting.suppressed 只记录当时的阻挡事实，重入时按当前未决审批和摘要重新判断，不能永久锁住新版本。tests/test_plan_approval_identity.py 覆盖新版本、同版本、已决阻挡和历史任务集合指纹的恢复。


## Task Workspace 写入与阻塞后的恢复

Task Pipeline writer 的 hook 从当前 running operation 与精确匹配的 kernel dispatch 读取工作目录；同时匹配角色、task、dispatch、task-map generation、workspace generation 和 placement epoch。不能由 hook cwd 自行授权。当前任务的合同路径限制与现有结果 scratch 能力继续生效。事件索引纳入 task.pipeline.stage.dispatched；完成后的旧任务目录不再获得运行中授权。legacy role workdir 仍按原路径处理。

复用 Task-stage 会话时，CompositeTransport 必须转发底层生命周期快照，供重绑定入口核对实际进程 cwd。同一 binding 的旧角色目录不能因为路由层丢失快照而被继续复用；确认无活跃 attempt/operation 后，原重绑定流程终止闲置槽位并在 Task Workspace 激活。没有目录观测能力的 adapter 不伪造 cwd。tests/test_task_pipeline_transport_binding.py 覆盖真实重绑定入口、同目录复用及按实例路由。

Impl 已提交 blocked 时 operation 是终态，重启不等于重试。操作员修复阻塞后，可先预览：

```bash
zf recover task-impl --operation <operation-id> --reason '<修复与验证证据>'
# 核对后使用同样参数并加 --apply
```

默认不追加事件；apply 经 Run Contract 检查，只为当前已接纳的 blocked Impl 记录 task.rework.requested，绑定原结果、任务代际和 operation。重复请求幂等，旧代际/已通过/有更新 operation/活跃 attempt/终态任务或 Run 均拒绝。调度器继续使用原 Plan 和合同，在原 max_rework_attempts 预算内派发下一 operation generation；不重开旧 operation，不伪造 passed，不自动批准新计划。后续仍需 Impl 结果、TaskRef 接纳与 Verify。
