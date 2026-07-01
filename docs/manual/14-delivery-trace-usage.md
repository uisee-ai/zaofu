# Delivery Trace 使用手册（`zf trace`）

> 适用对象: 需要从 feature 级别观察「计划如何拆 / 实际如何跑 / 是否偏移」的操作者。

## 1. 是什么

`zf trace` 的 delivery 系列子命令是一组**只读投影**：把 *计划*（accepted `task-map.v1`）
与 *实际运行态*（kanban task + events）合并，回答 operator 最关心的几个问题：

- 一个 feature 拆成了多少子 task、依赖/wave 怎么排？
- 每个子 task 实际跑在哪个 worker、什么状态、有哪些证据事件？
- 实际执行是否偏离计划（依赖、分配、scope、证据）？
- 这个 feature 离 ship 还差什么？

它**不写任何 runtime 状态**（不碰 `events.jsonl` / `kanban.json`），也**不重判** kernel 已裁决的事
——scope 偏移只是把 kernel 发过的 `scope.violation` 事件 surface 出来，不重跑 gate。

> 当前面向 CLI（本手册）。Web 页面（doc 65 P1）是后续增强。

## 2. 命令一览

| 命令 | 作用 | 输出 schema |
|---|---|---|
| `zf trace delivery <feature_id>` | feature 全链路总览（spine + waves + drift 摘要 + ship readiness） | `delivery-trace.v1` |
| `zf trace execution-graph <feature_id>` | planned task-map 与实际状态 join 的对账图（节点 + blocked_by 边 + wave） | `execution-graph.v1` |
| `zf trace drift <feature_id>` | planned vs actual 偏移报告 | `drift-report.v1` |
| `zf trace task-node <task_id>` | 单个 task 节点的 planned vs actual + drift | execution-graph 节点 |

公共参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--format table\|json` | `table` | 终端表格 或 完整 JSON（喂给脚本/Web） |
| `--task-map-ref <path>` | — | 显式指定 task-map 路径；默认从 `<state_dir>/artifacts/<feature_id>/task_map.json` 找 |
| `--state-dir <path>` | `project.state_dir`（否则 `.zf`） | 运行态目录 |

## 3. 典型用法

```bash
# feature 全链路总览
zf trace delivery F-CANGJIE-GA

# 只看对账图 / 偏移 / 单节点
zf trace execution-graph F-CANGJIE-GA
zf trace drift F-CANGJIE-GA
zf trace task-node TASK-API-002

# 机器可读输出
zf trace delivery F-CANGJIE-GA --format json
```

`delivery` 表格输出示例：

```text
Delivery Trace F-1 status=in_progress ship=blocked drift=warning
  tasks 2: done=1 in_progress=1 blocked=0 waiting=0
Wave 1 [done]
  T1               done         owner=dev actual=dev-1
Wave 2 [in_progress]
  T2               in_progress  owner=dev actual=review-9
Drift [warning] error=0 warning=3 info=0
  WARNING T1               evidence_drift: done with no recorded evidence events
  WARNING T2               assignment_drift: assigned to review-9 but planned owner_role dev
  WARNING T2               scope_drift: kernel emitted scope.violation for this task
```

读法：
- **status** = trace 整体状态（`in_progress` / `done` / `blocked` / `not_started` / `empty`）。
- **ship** = ship readiness 投影（`ready` 当且仅当所有节点 done 且无 error 级 drift）。**仅投影，绝不自动 ship**；真正 ship 仍走 ship gate / 人工。
- 每行节点：`task_id  实际状态  owner=计划角色  actual=实际 assigned_to`。
- **Drift** 段按严重度列出偏移（见 §5）。

## 4. Drift 分类（P0 覆盖）

| 类型 | 触发 | 严重度 |
|---|---|---|
| `dependency_drift` | writer 已开工（in_progress/done/...）但其 `blocked_by` 前置未完成 | error |
| `assignment_drift` | 实际 `assigned_to` 的角色前缀 ≠ 计划 `owner_role` | warning |
| `evidence_drift` | task 已 done 但无任何证据事件 | warning（**不升 error**，kernel 已放行，投影不重判）|
| `scope_drift` | kernel 已 emit 该 task 的 `scope.violation`（直接引用其 event id） | warning |

> 红线：drift 报告**消费** kernel 的判定，不构成第二判断面（守不变量 I2/I7）。
> runtime / artifact 等其余 drift 维度是后续阶段（doc 65 P2）。

## 5. 边界与降级

- **缺 accepted task-map**：不报错，降级为 *kanban-only* 图（仅实际节点，无 planned 维度），并在 `diagnostics` 标 `task_map_missing`。
- **task 无 `feature_id`**（legacy / 真实 task 常见）：归入 *synthetic trace*（`synthetic: true`，`trace_id = synthetic:<project>`），不与真 feature 混淆。
- **task-map 有节点但 kanban 无对应 task**：节点 `actual.status = not_created` + `diagnostics` 标 `kanban_task_missing`。
- **kanban task 不在 task-map 内**：`diagnostics` 标 `task_not_in_task_map`。

## 6. 实现位置（排障参考）

- 投影纯函数：`src/zf/runtime/execution_graph.py`、`delivery_trace.py`、`drift_report.py`
- 加载层（从盘读 kanban/events/task-map）：`src/zf/runtime/delivery_trace_resolve.py`
- CLI：`src/zf/cli/trace.py`（`run_delivery_trace`）
- 复用：`execution_route` / `task_run_panel` 在节点级被复用，本系列只做 feature JOIN + 对账 + drift（不另造投影）。

## 7. 一句话原则

`task-map.v1` 说明「计划怎么执行」；`zf trace delivery` 说明「实际是否按计划执行」。
后者是只读观测，任何修正仍走 orchestrator / rework / re-map。
