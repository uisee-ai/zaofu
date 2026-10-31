# Task Pipeline 上下文发布与恢复

Task Pipeline 在准备新的 provider operation 前，通过 TaskStore 重新读取当前 Task，核对其计算出的合同版本与派发快照一致，再经 write_task_doc 原子写入任务说明投影并进行 freshness 检查。发布失败或版本不一致时停止派发，沿现有 dispatch deferred/cooldown 路径处理，不生成产品失败结果。

投影刷新不回写 TaskContract，不修改已接纳计划、验收、任务状态或工作树代码。新的 task.doc.published 事件记录 dispatch、来源事件和三种版本。

上下文压缩或换会话后，read ledger 证明来源与已产出范围；Provider 交付需单独观测，两者都不能证明当前仍理解正文。worker 方法允许先核对续跑说明与来源摘要，再经原受控读取入口按需补读当前任务的条款、接口和验收；减少重复读取不是禁止恢复语义。已有必读声明仍须完整满足，不因方法修订改写账本或旧 policy。

worker 发现任务说明、briefing、快照之间版本冲突时，以 implementation result 的 failure_class/blocker_kind=task_context_stale 报告具体版本和证据。该类型进入 harness 修复分支，不消耗产品重规划重试。真实 upstream_contract_gap 仍进入语义重规划；本修复不依赖自由文本猜测、不重写历史失败事件。

故障修复后，监工核验当前 operation 已停止、说明投影与快照一致，再通过现有受控恢复动作/CLI 恢复。已批准 Task Map 的修订仍须通过正式 Planner/Critic、Plan Preview 和新 package 接纳；重启进程不等于恢复 Run，也不等于批准新计划。

`zf restart --dry-run` 全量重启预览只报告将执行的 stop/start，不调用实际停止或启动入口；该预览不证明 provider 连接或运行态已恢复。`tests/test_cli_restart.py` 通过真实 CLI 入口阻断对这两个入口的调用。

单角色 `zf restart <role> --dry-run` 同样只报告计划，不重启角色、改写角色状态或追加合同检查事件。任务级语义恢复复扫按原任务身份隔离：同一父 Run 和候选提交下不同任务的复扫不得互相替代；同一任务的后续代仍按原规则替代旧代。实际 provider 断连后仍需通过正常角色恢复链恢复派发，不能把预览当作恢复。

对于修复前已发生的跨任务错误替代，Run Manager 仅在账本能绑定原失败、原复扫请求、取消事件和同 Run 的另一任务替代请求时，以取消事件为幂等依据重发原语义复扫；已产生后续进展或已重发的任务不重复派发，同任务正常换代不触发此恢复。

若原恢复案卷仍等待被取消复扫的效果，先用匹配的请求和取消事件收口为 effect.failed，重新开放该案卷；只有通过正常恢复案卷准入后才可重发，不直接改写案卷或清除历史事件。

显式派发基准存在时，TaskRef 的文件范围由该基准到提交目标的 Git 差异确定，兼容 legacy `base_git_head` 与 v4 `base_commit`。局部返工或仅清理工作树的结果可以报告本轮没有新增文件，但不能据此清空此前尚未集成的任务文件；缺少显式基准的 legacy 空交接保持原有只读组装语义。候选集成消费完整范围，保留合同路径准入；不得只拼入最后一次修复文件，导致已经独立验证的入口、依赖清单或 smoke 脚本丢失。`tests/test_task_ref_rework_integration.py` 通过真实 TaskRef 与增量 candidate 入口覆盖完整实施、局部修复、空变更重交及滚动 smoke。

验证：tests/test_task_pipeline_capsule.py 覆盖派发入口与真实 briefing/transport 调用边界；tests/test_task_context_recovery.py 覆盖分类与 Run Manager 重规划排除；保留 pipeline、TaskDoc、result submit 和 flow mock E2E 回归。

真实恢复时还发现：候选 Task Map 保留了草稿状态，Planner 预检放行、Critic 通过后才被 Package 门禁拒绝。预检现在也检查必需 Task Map / Source Inventory 中显式声明的 enrichment contract：只有 status=ready 且 enrichment=fulfilled 才能提交；无该契约的历史输入保持兼容。复用已有矩阵检查，不自动把草稿改为 ready，也不替代语义审核。tests/test_plan_enriched_port_readiness.py 覆盖两个端口，以及同一次 result submit 被拒绝后修正并重新提交的实际入口。

规划失败可能携带上一版本的 task_map/package 与失败任务 ID。批量恢复不得据此把 reader 的 Plan 协议失败当成实施失败并重发 task_map.ready。workflow_resume 将显式 plan_artifact_package_protocol、plan_candidate_review_identity、plan_candidate_preflight 继续交给 stage replan；原有实施失败/候选运行时恢复保持原路径。tests/test_plan_failure_batch_resume.py 覆盖带继承身份的实际恢复投影入口。

清理无活跃租约的旧 operation 时，若同一 Run、同一父任务下另有 running operation 且持有有效 TaskAttempt 租约，只取消旧操作，不把父任务改为 blocked；恢复收据记录该活跃 operation。不同 Run、不同父任务或过期租约不授予豁免。`tests/test_workflow_resume_parent_liveness.py` 通过正式恢复入口覆盖父级 Planner 和下游子任务继续执行的情况。

Plan 审批去重同时绑定任务集合与产物摘要（优先 Package digest，其次 Plan digest）。任务 ID 不变而 Package 更新时必须能产生新的审批单；同一版本仍保持去重。旧 plan.minting.suppressed 只记录当时的阻挡事实，重入时按当前未决审批和摘要重新判断，不能永久锁住新版本。tests/test_plan_approval_identity.py 覆盖新版本、同版本、已决阻挡和历史任务集合指纹的恢复。


## Task Workspace 写入与阻塞后的恢复

心跳巡检与 pane 存活巡检使用相同的 Task Pipeline 终态归属证据。任务状态因等待集成或上游资料仍为 in_progress 时，已结束的 operation 不再承担心跳义务；active_dispatch_id 清空也不会恢复该义务。后续新的运行中 operation 或 legacy 派发仍接受正常存活检测。`tests/test_task_pipeline_worker_liveness.py` 覆盖真实 heartbeat sweep，避免空闲 lane 被反复误重启。

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

结果接纳会先清空 Task 的 active_dispatch_id，集成失败时 Task 仍可保留 in_progress。此时仍按精确匹配的派发与 operation 终态排除旧 worker，不能因为活动标记已清空而再次催交旧完成事件；新的运行中 operation 或不同派发仍可见。`tests/test_task_pipeline_worker_liveness.py` 通过真实派发标记清理入口与 watchdog 验证这一交接。

新建必读声明合并 ref、摘要、selector、读取限制、访问范围和保留策略均相同的 Plan 端口别名；完整 Package 端口保留。既有 attempt 的不可变 policy 与读取账本不改写，独立 Verify 的证据权限也不与 Impl 共享。`tests/test_plan_read_aliases.py` 覆盖同内容别名与各项不等价边界。

大任务输入可拆成多个显式 Package 端口。Task Map 顶层 `required_plan_ports` 保留完整全局矩阵；单任务补充端口由既有 `evidence_contract.required_plan_ports` 声明，并由同任务 `plan_input_selection` 选择。准入前核对每项任务声明与实际 Package 端口绑定，读取仍校验 ref、摘要及文件上限。任务级声明不再要求重复列入全局必读，因此独立全局验证不会仅因某任务需要一个副本，就被迫消费所有任务副本；有任务身份的消费者仍承担该任务声明的必读。Planner/Critic 必须确保真正的全局义务仍在完整矩阵中。`tests/test_task_local_plan_ports.py` 覆盖真实 Package 准备、Impl/Task Verify 选择、全局消费者及缺失/篡改拒绝。旧 Package 和已派发读取账本不改写；新声明依赖消费者代码重载。

Candidate 是独立工作区，不继承 worker 的未跟踪依赖。项目须在 zf.yaml 的 `project.scripts.setup` 声明可重复执行的环境准备入口；Candidate integrator 会在精确候选提交上执行该配置，再运行 rolling smoke。运行时不猜测项目使用 Node 或某个固定脚本。

修复集成阻塞后使用 `zf recover task-integration --operation <id> --reason '<修复证据>'` 预览，加 `--apply` 才执行。入口检查当前代际、原 needs_review 事件、已接纳的 Verify 通过结果、无活跃 operation/attempt 及非终态 Run/Task；apply 通过 WorkflowOperationService 追加 supersede 事实，原失败证据不变。既有 reconciler 派发下一 integration generation，重新执行 setup、rolling smoke 与 Candidate CAS；保留已通过的 Impl/Verify，不发新的产品成功事件。重复请求幂等，新的阶段代际或更新集成操作使旧恢复请求失效。

Task Pipeline operation 因 `usage_samples` 达限时，Owner 可针对该 operation 显式修订采样上限：`zf goal set --status active --operation <id> --operation-max-usage-samples <非负整数> --reason '<批准依据>'`。零表示取消该上限；非零值必须高于阻断时的累计采样数，不修改其他 operation 或冻结请求。之后用 `zf recover workflow --dry-run --json` 检查对应 checkpoint，再以 `--resume-pending --checkpoint-id <id>` 受控恢复。运行时只接受该 operation 的预算阻断之后、由正式恢复链准入的修订；继续沿用原计量基线，并在达到修订后非零上限时再次阻断。美元定价未知不等于零成本；不可通过此入口修订美元限额。

Owner 明确要求对同一 Run 后续 operation 一并修改时，可在上述命令增加 `--all-run-operations`；受控恢复准入后同一 Run 的 operation 使用该采样上限，其他 Run 和其他预算维度不受影响。原冻结请求和历史用量记录不改写。

验证：`tests/test_task_pipeline_worker_liveness.py` 覆盖实际 watchdog 调用；`tests/test_task_pipeline_integration_retry.py` 覆盖 CLI、拒绝分支、代际投影和无 provider 的真实 git Candidate 集成恢复。

批量派发也受 `max_unverified_tasks` 剩余名额约束，每派发一个 fresh Impl 都占用名额；不能仅在批次开始检查一次阈值。Impl/Verify 池各有 8 个槽位不等于可以同时运行 16 个未验证任务。`tests/test_task_pipeline_parallel_backpressure.py` 覆盖 1 Verify + 7 Impl 并行与 8 Verify 排空，既有返工回归保持通过。

## 派发身份与合同版本分别验证

Task Pipeline 激活 provider 时显式选择当前 briefing 的 typed-result 完成协议。角色启动早于本次 dispatch 发布，角色说明不再从 TaskStore 上一阶段的 active_dispatch_id 和 role.publishes 生成旧 emit 命令或并列的完成 payload；结果 scratch、operation、提交命令及可用预检均由当前 briefing 的 Output Contract 提供。Legacy 激活保留原事件协议。`tests/test_task_pipeline_role_instructions.py` 经过真实 Task-stage 绑定、按需激活与说明生成入口，覆盖 Impl/Verify、尚无 Task 投影和 Legacy 回归；不提前改写 TaskStore 身份。已启动 provider 继续以当前 briefing 为准，消费者重载后新激活才生成更新说明。

Task Pipeline 的 task.md/manifest 在发送前绑定本次 dispatch_id 与 role_instance；使用当前 canonical Task 的副本渲染，不提前更新 TaskStore 的 active_dispatch_id，也不改变合同版本。稳定 capsule revision 不应承担每次派发身份的校验职责。

`verify_task_capsule` 的派发调用显式传入 expected_dispatch_id/expected_role_instance，核对 manifest 的 dispatch_id、active_dispatch_id、任务与角色，以及 task.md 的头部和 Runtime Binding。先校验当前合同，再准备 operation；只有需要实际派发时才发布文档，运行中/终态 operation 的幂等重入不重写文档。准备 briefing 后、交付 transport 前再检查一次，覆盖准备期间投影被替换的情况。briefing 显式列出 operation、dispatch 和 attempt 身份。发送成功后沿原路径更新 canonical TaskStore 并记录派发事件；发送失败不伪造 in_progress 或已发送事实。

验证：`tests/test_task_pipeline_capsule.py` 在真实派发函数和 briefing/文档生成器、隔离的 provider transport 边界核对 Impl/Verify 首派、g2 重派及失败分支，并注入 manifest/正文/角色陈旧。它证明派发上下文一致性，不代表真实多 provider 并行或完整产品验收通过。


### 完整来源重读

恢复 briefing 只重新列出当前 active dispatch 且仍存活 attempt 的受控读取入口；
旧 attempt 的账本不能替代当前约束。原始问答、批准配置与当前未完成 AC/返工项
通过完整来源恢复，历史输出通过精确 ref/digest 获取。版本副本校验失败时回退
有效原文，全部不可用则停止推断。当前实现不自动删除历史或触发 Provider compaction。
细节见 [读取面](task-pipeline-capsule-read-path.md#产出交付与版本恢复2026-10-30)。
