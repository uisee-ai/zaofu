# Task Pipeline 上下文发布与恢复

Task Pipeline 在准备新的 provider operation 前，通过 TaskStore 重新读取当前 Task，核对其计算出的合同版本与派发快照一致，再经 write_task_doc 原子写入任务说明投影并进行 freshness 检查。发布失败或版本不一致时停止派发，沿现有 dispatch deferred/cooldown 路径处理，不生成产品失败结果。

投影刷新不回写 TaskContract，不修改已接纳计划、验收、任务状态或工作树代码。新的 task.doc.published 事件记录 dispatch、来源事件和三种版本。

worker 发现任务说明、briefing、快照之间版本冲突时，以 implementation result 的 failure_class/blocker_kind=task_context_stale 报告具体版本和证据。该类型进入 harness 修复分支，不消耗产品重规划重试。真实 upstream_contract_gap 仍进入语义重规划；本修复不依赖自由文本猜测、不重写历史失败事件。

故障修复后，监工核验当前 operation 已停止、说明投影与快照一致，再通过现有受控恢复动作/CLI 恢复。已批准 Task Map 的修订仍须通过正式 Planner/Critic、Plan Preview 和新 package 接纳；重启进程不等于恢复 Run，也不等于批准新计划。

显式派发基准存在时，TaskRef 的文件范围由该基准到提交目标的 Git 差异确定，兼容 legacy `base_git_head` 与 v4 `base_commit`。局部返工或仅清理工作树的结果可以报告本轮没有新增文件，但不能据此清空此前尚未集成的任务文件；缺少显式基准的 legacy 空交接保持原有只读组装语义。候选集成消费完整范围，保留合同路径准入；不得只拼入最后一次修复文件，导致已经独立验证的入口、依赖清单或 smoke 脚本丢失。`tests/test_task_ref_rework_integration.py` 通过真实 TaskRef 与增量 candidate 入口覆盖完整实施、局部修复、空变更重交及滚动 smoke。

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

## Verify 后的集成失败与 Worker 生命周期

Task 尚未 done 不代表 Impl/Verify worker 仍有执行义务。watchdog 对精确匹配的 Task Pipeline dispatch 和 operation 终态停止恢复该 worker，避免在等待集成时重启 provider 或重排 Task。新的活跃 operation 仍受监测，旧终态不能屏蔽下一次派发。

Candidate 是独立工作区，不继承 worker 的未跟踪依赖。项目须在 zf.yaml 的 `project.scripts.setup` 声明可重复执行的环境准备入口；Candidate integrator 会在精确候选提交上执行该配置，再运行 rolling smoke。运行时不猜测项目使用 Node 或某个固定脚本。

修复集成阻塞后使用 `zf recover task-integration --operation <id> --reason '<修复证据>'` 预览，加 `--apply` 才执行。入口检查当前代际、原 needs_review 事件、已接纳的 Verify 通过结果、无活跃 operation/attempt 及非终态 Run/Task；apply 通过 WorkflowOperationService 追加 supersede 事实，原失败证据不变。既有 reconciler 派发下一 integration generation，重新执行 setup、rolling smoke 与 Candidate CAS；保留已通过的 Impl/Verify，不发新的产品成功事件。重复请求幂等，新的阶段代际或更新集成操作使旧恢复请求失效。

验证：`tests/test_task_pipeline_worker_liveness.py` 覆盖实际 watchdog 调用；`tests/test_task_pipeline_integration_retry.py` 覆盖 CLI、拒绝分支、代际投影和无 provider 的真实 git Candidate 集成恢复。

批量派发也受 `max_unverified_tasks` 剩余名额约束，每派发一个 fresh Impl 都占用名额；不能仅在批次开始检查一次阈值。Impl/Verify 池各有 8 个槽位不等于可以同时运行 16 个未验证任务。`tests/test_task_pipeline_parallel_backpressure.py` 覆盖 1 Verify + 7 Impl 并行与 8 Verify 排空，既有返工回归保持通过。

## 派发身份与合同版本分别验证

Task Pipeline 的 task.md/manifest 在发送前绑定本次 dispatch_id 与 role_instance；使用当前 canonical Task 的副本渲染，不提前更新 TaskStore 的 active_dispatch_id，也不改变合同版本。稳定 capsule revision 不应承担每次派发身份的校验职责。

`verify_task_capsule` 的派发调用显式传入 expected_dispatch_id/expected_role_instance，核对 manifest 的 dispatch_id、active_dispatch_id、任务与角色，以及 task.md 的头部和 Runtime Binding。先校验当前合同，再准备 operation；只有需要实际派发时才发布文档，运行中/终态 operation 的幂等重入不重写文档。准备 briefing 后、交付 transport 前再检查一次，覆盖准备期间投影被替换的情况。briefing 显式列出 operation、dispatch 和 attempt 身份。发送成功后沿原路径更新 canonical TaskStore 并记录派发事件；发送失败不伪造 in_progress 或已发送事实。

验证：`tests/test_task_pipeline_capsule.py` 在真实派发函数和 briefing/文档生成器、隔离的 provider transport 边界核对 Impl/Verify 首派、g2 重派及失败分支，并注入 manifest/正文/角色陈旧。它证明派发上下文一致性，不代表真实多 provider 并行或完整产品验收通过。
