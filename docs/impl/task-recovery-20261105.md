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
