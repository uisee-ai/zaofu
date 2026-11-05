# Task Pipeline 断点修复（2026-11-05）

本批任务见 `tasks/done/2026-11-05-1400-task-recovery.md`。

## 修复边界

失败分类的环境启发式只读取失败诊断字段和未通过探针的诊断，不能从已通过命令、文件路径、传输参数中推断故障。无结构化结论的 dev.blocked 继续交给语义 triage，不由内核臆测产品结论。真实连接错误仍保持环境恢复路线。

TaskRefManager 使用 `git diff --name-only -z` 读取原始文件名，按 NUL 分隔。中文、引号、换行路径仍逐项执行原有 scope 检查，不改变越界拒绝条件。

operation 已终结且任务 active_dispatch_id 清空时，旧 assigned_to 不能构成 worker 存活义务。仍核对最新 task.dispatched 的 operation、dispatch、stage 和 generation；新 operation/legacy dispatch 不被旧终局遮蔽。此一行修正在 dev 工作区此前存在，本批纳入独立 RED/GREEN 验证，不复制其余未提交修改。

## 验证与恢复

新增集成回归在修改前 13 failed / 3 passed；修复后与 TaskRef、生命周期、triage 直接调用测试共 107 passed。最终自动验证见任务归档记录。

恢复前确认无活跃 provider operation，保留工作区和审计账本；加载修复代码重启 runtime，让已有 operator-intervention 路线解除 remediation safe-halt。P04 明确批准一次额外代次，其他任务保持原有重试上限；所有结果继续走独立 Verify 和候选集成门禁。不得把恢复派发等同于产品完成。

## 产品修复责任

- P04：服务端写入/审核权限、worker host 签名和持久化、七字段查询及历史审核 UI、单位与条件校验、分类名/ID 规范化。
- P06：取消任意模型名生成固定几何的成功路径；注册 adapter/输入加载/执行回执。无已批准真实模型时保持人工待处理，不签收真实推理验收。
- P18：PAUSE_REQUESTED 允许当前有效 attempt 报告失败；取消只终结当前 attempt，历史记录逐字段保留。
- P16B：原隐私任务缺少 schedule 私有中间结果和发布/读取守卫接口，先由受控 replan 明确上游契约，再实现脱敏、审核、导出与撤权。普通内部 copy 不被追溯判定为隐私实现失败。

## Reader stage 重试的引用一致性

P16B 的历史 Task 派发包与当前 Run Plan 包不同，属于 gap-only 多代任务共存。首次操作将任务包作为 Run 规划包，被 preregistration 正确拒绝，未执行 reader。已纠正此恢复请求；没有放宽包准入规则。

后续发现 `stage_failure_replan` 仅更新 Plan 包/generation 却保留旧 task_map/source_index。修复从当前已接纳包的 source_event_id 同步读取 map/index 和对应摘要；不存在的新摘要不会继承旧值，未接纳草稿不构成来源。回归先 RED 后 GREEN；104 项 stage/run-manager/controlled-action 测试通过。任务见 `tasks/done/2026-11-05-1410-stage-replan-map-binding.md`。

当前全量 Plan 已有 P16B-RELEASE-CONTRACT 和 P16B-PRIVACY-R1，但 canonical 队列没有这两项且旧 P16B 仍被下游引用。受控规划需对账任务创建、替代和下游依赖，不能重复生成未物化的空 gap 集，也不能把不存在于当前 Map 的旧 ID 当作直接 supersede 目标。

## Discovery 替代图优先级

Reader 已声明新任务的 `supersedes_task_ids` 时，flow discovery bridge 使用该显式替代图；显式空数组表示追加，不再混入恢复请求继承的历史 ID。没有任务级声明的旧结果仍使用 envelope。任务是否存在、基线是否合法、图是否闭合继续由原准入检查负责；本修复不把历史节点自动签认为当前节点。实证为 `evt-4e10a21d6f6e`，修复回归涵盖当前节点、追加、兼容路线和未知节点拒绝。

## Candidate replan 使用同一引用规则

`replan_resynth` 与 reader stage retry 均调用 `plan_source_binding.bind_admitted_plan_source_refs`，从当前已接纳 Plan Package 的 source event 绑定 map/index 的 ref 与 digest。避免重合成沿用最早 scan/Planner trigger 的旧路径。只读回归覆盖两个实际入口；未接纳草稿、缺失新摘要及显式空 index 保持既定约束。

## 历史替代的准入对账

writer adoption 在物化本轮后继后，通过 `task_map_supersession_history` 读取当前 Map 的有限 amendment 引用链。只有同 workflow run 的 kernel `task_map.amended`、匹配的历史文件 SHA256、显式替代边和已经物化的当前后继共同成立，才补入历史替代。缺失/损坏引用、无事件、跨 run、无法闭合的后继链均不授权取消；仍有活跃派发的旧任务也保持原状。

随后复用原有 TaskStore / task_doc / EventWriter 路径：先更新保留任务依赖，再取消旧节点。取消事件记录历史证据 ID，并绑定当前 workflow run，供 Task Pipeline 的 supersession/归档检查消费。不读取 Planner 自由文字作为控制指令，不因旧 ID 不在新图就推断可删除，也不重开 done。

历史节点已经 cancelled 时，仍可据同样证明迁移滞留依赖，并补齐当前 run/Map 的替代见证；同图重放不重复追加见证，不修改 cancelled 状态。

## PAS 打包后身份字段的输入范围

Plan Package 会给原生 product_acceptance_spec.v1 绑定 schema/run/flow/revision/generation，而生产者分区基于绑定前正文。输入范围计算将这五个具名标量身份作为机械共享字段读取；原始语义路径仍按显式 task_input_policy 检查完整覆盖。其他 schema、对象型身份或遗漏的业务内容继续拒绝。读取侧不重写不可变工件、摘要、已批准 Plan 或已有 TaskAttempt，既有包无需重新生成即可使用同一已批准内容。

本修复接纳主工作区已运行的显式 task_input_policy 读取支持及其原有测试作为直接依赖，未接纳其余未提交改动。新增回归走实际 prepare_plan_artifact_package -> current_plan_port_sources，修改前 1 failed / 12 passed。

## 监工控制面事件窗口开销

`EventLog.read_days` 保留原有归档窗口选择，按文件身份、大小/时间和签名策略缓存完整行，并只解码追加尾部。轮转、删除、截断或同大小重写失效；半行不前进。缓存最多16个文件/验证策略组合，仅供读取，不影响 EventWriter、事件顺序或规范状态。实况生命周期usage扫描反复解析112MB日志，控制进程心跳可用但恢复tick落后，需以真正派发和tick恢复验证效果。

Task Pipeline 静默门禁同时识别 kernel task.dispatched/task.done、绑定 operation 与 control_result_ref 的实现/验证结果，以及非终局 task-stage operation。失败只唤醒恢复，不等于通过、自动消除升级或扩大重试配额；心跳与无绑定worker自报仍不唤醒。旧P07B孤儿升级导致后续P16B结果未触发RM，已新增真实tick_services入口回归。
