# 186 — 项目交付工作台：Kanban、Graph 与 Loop 关联设计

> 状态: candidate。本文记录已审查的现有能力、关联缺口和建议方案；仅批准文档编写与提交，不代表新增功能已实施或通过端到端验收。
> 归属: `docs/design/`；承接 [182](182-plan-review-task-slicing-and-evidence-closure.md)、[185](185-agent-routing-delegation-and-workflow-recovery.md) 及 184 的相关讨论。

## 1. 问题与目标

ZaoFu 已有 Kanban、Delivery Graph、Run Graph、Loop、任务详情与验证证据。
这些视图分别表达当前工作、需求覆盖、运行历史和返工，但用户仍需自行拼接：
原始需求由哪些任务承担、任务为何等待、失败影响哪些交付项、修复后哪些证据仍有效。

目标是形成同一个项目交付工作台：共享稳定身份、浏览上下文和状态含义，通过不同视图
解释同一份运行事实。优先打通以下用户路径：

```text
Kanban 发现阻塞 → Graph 查看原因和影响 → Loop 定位失败与恢复
       ↑                                      |
       +---------- 返回同一任务继续处理 --------+
```

本文不新增第二套 Task schema、Graph 状态库或调度器；不把模型退出成功等同于交付成功。
跨页导航、展示与聚合是投影职责；计划修改、审批和恢复仍由受控动作执行。

## 2. 当前实现与审查边界

本轮检查当前 checkout 的代码、直接 Web 接线和相关测试定义，没有重新执行测试或
浏览器 E2E。以下“已有”指代码及入口存在，不表示所有真实项目分支均已验证。
历史架构文档在公开 checkout 中可能缺失，状态所有权以 [AGENTS.md](../../AGENTS.md)
及当前实现为准。

| 能力 | 当前证据 | 范围与边界 |
| --- | --- | --- |
| Workflow Graph | [graph.py](../../src/zf/core/workflow/graph.py)、[Web 投影](../../src/zf/web/projections/workflow_graph.py) | 从配置导出阶段、事件边与返工路由；不是业务任务分解图 |
| Task Map | [task_map.py](../../src/zf/runtime/task_map.py)、[物化日志](../../src/zf/runtime/task_map_materialization.py) | 验证任务契约、依赖与范围，并通过受控路径物化 canonical Task |
| Execution Graph | [execution_graph.py](../../src/zf/runtime/execution_graph.py) | 关联已接纳 Task Map、Task 当前状态及运行证据，包含依赖边 |
| Goal Coverage | [goal_coverage_graph.py](../../src/zf/runtime/goal_coverage_graph.py) | 目标条款、任务、验证结果与目标闭合；区分显式条款和历史派生覆盖 |
| Delivery Web | [delivery_views.py](../../src/zf/web/projections/delivery_views.py)、[DeliveryMapView](../../web/src/components/delivery-trace/DeliveryMapView.tsx) | 按 overview/runs/graph/work 加载投影，已有 Coverage/Work 切换 |
| Run Graph | [run_chain.py](../../src/zf/runtime/run_chain.py)、[RunGraphView](../../web/src/components/delivery-trace/RunGraphView.tsx) | 阶段、因果链、执行状态及重规划历史入口 |
| Loop | [LoopPageV2](../../web/src/components/delivery-trace/LoopPageV2.tsx) | 阶段闭环、任务尝试时间线、故障与恢复观察 |
| Kanban | [列投影](../../web/src/components/kanban/board.ts)、[TaskCard](../../web/src/components/kanban/TaskCard.tsx) | 工作状态、角色与 workflow badges；列分组不能替代精确结果 |
| Task Pipeline | [reconciler](../../src/zf/runtime/task_pipeline_reconciler.py)、[运行投影](../../src/zf/runtime/task_pipeline_projection.py) | 按前置依赖、池容量、背压和返工条件计算调度状态 |

已确认的产品关联缺口：

1. [Work 图模型](../../web/src/components/delivery-trace/deliveryWorkGraphModel.ts) 目前使用
   `goal/claim/task/unmapped` 节点与 `tree/secondary` 边，表达覆盖关系；它未把 Task
   间的 `blocked_by` 画成依赖边。不能将后端已有依赖数据当作该 UI 已完整展示。
2. Loop 的 `openTask/openLoop` 保存在页面内部，当前调用入口主要传入项目上下文。
   [ProjectionPage](../../web/src/components/projection/ProjectionPage.tsx) 与
   [App 导航](../../web/src/app/App.tsx) 尚未形成贯穿三页的 Task/Attempt 定位合同。
3. 三个页面的范围不同：Board 面向项目任务，Delivery 主要面向 Feature/Goal，Loop
   有项目运行视角。统一入口前必须明确 scope，不能把不同 Run 的同名阶段拼在一起。

待专项验证：Request → Requirement 修订 → Plan generation → Task → Operation →
Attempt → Candidate → Evidence 的身份闭包；所有恢复分支的双向导航；大图折叠后
遗漏详情的按需加载。这里只登记验证对象，不把未审查部分断言为缺失实现。

## 3. 借鉴的开源交互方式

| 来源 | 官方能力 | ZaoFu 的采用方式与边界 |
| --- | --- | --- |
| [Apache Airflow UI](https://airflow.apache.org/docs/apache-airflow/stable/ui.html) | Graph 表达依赖，Grid 表达任务与运行状态，Task Instance 提供执行详情 | 共享任务与运行定位；不能照搬任意手动标记成功来绕过交付证据门禁 |
| [Dagster Asset selection](https://dagster.io/docs/guides/build/assets/asset-selection-syntax) | 筛选上游、下游和局部关系图 | 展示阻塞源、影响范围与验收关联；资产关系不能直接替代产品需求依赖 |
| [Langfuse Agent Graphs](https://langfuse.com/docs/observability/features/agent-graphs) | 聚合重复步骤或展开每次调用；展开视图保留执行顺序 | Graph 聚合任务状态，Loop 展开尝试；不能用调用先后或嵌套推断业务 `depends_on` |
| [React Flow 布局指南](https://reactflow.dev/learn/layouting/layouting) | 支持接入 Dagre、D3 Hierarchy、ELK 等布局方案 | 复用现有 React Flow；需求树与任务 DAG 分别选择布局，复杂分组先比较实际样例 |

以上是交互与关系表达的参考，不构成替换 ZaoFu Runtime 的建议。
外部文档会演进，实施选型时重新核对；没有据此承诺引入任何新依赖。

## 4. 同一工作台的三个视图

| 视图 | 用户问题 | 主体与展示重点 |
| --- | --- | --- |
| Kanban | 现在需要处理什么？ | canonical Task、当前状态、负责人、阻塞和审批入口 |
| Graph | 为什么这样安排，会影响什么？ | 需求覆盖、任务依赖、可执行前沿与下游影响 |
| Loop | 经历了什么，是否在收敛？ | 执行尝试、业务返工、基础设施恢复、计划修订与证据变化 |

```text
              项目 / 目标或 Feature / Run / Plan generation
                                  |
               +------------------+------------------+
               |                  |                  |
            Kanban              Graph               Loop
          当前工作状态       依赖与影响范围       尝试历史与收敛
               |                  |                  |
               +---------- 同一个 Task 身份 ----------+
                                  |
                            统一任务详情内容
                   契约 / 依赖 / 执行 / 证据 / 恢复
```

统一详情内容可以分期复用，不要求首批重写所有 Drawer。保留视图专属展示，但共享任务
身份解析、结果含义和导航动作；跨页往返保留筛选、选中节点与历史版本。

## 5. 需求到交付的结构

```text
Kanban Agent / Channel Group 输入
                 |
                 v
Requirement：范围、验收条款、未决项、修订版本
                 |
                 v
Workflow 路由与配置提案 → Preview 讨论 / 审批
                 |
                 v
Scan → Planner → Plan vN + Task Map → Critic
                    ^                    |
                    +------ 需修订 -------+
                                         | 通过
                                         v
                                  Plan 人工审批
                                         |
                                  接纳 / 物化任务
                                         |
                          +--------------+-------------+
                          v                            v
                     共享契约 T1                  独立能力 T2
                          |
                     +----+----+
                     v         v
                    T3         T4
                     +----+----+-----------------------+
                          |
                          v
                 增量集成 / Candidate
                          |
             完整业务链、部署、回归与验收验证
                          |
                 Judge / 目标闭合判定
                     /             \
                  有缺口           通过
                    |               |
             定向修复 / Plan Delta   交付包与版本证据
                    |
             受影响验证重新执行
```

该图表达交付关系与审批边界，不要求所有任务先完成 Impl 才统一进入 Verify；现有任务级
流水仍可流式推进。T1–T4 是设计示例，不是任何产品已审批的实际 Task Map。

任务拆解由验收反推：目标 → 原始条款 → 业务行为切片 → 共享契约/依赖 → 实施、验证与
集成责任。每个任务应说明需求来源、输入输出、范围、前置条件、验收方法和证据生产者。
跨任务业务链、部署和最终验收必须有责任归属，不能只拆页面或代码模块。
8 个 Impl lane、8 个 Verify lane 是容量，不决定任务数量，也不保证始终满载。

## 6. Graph 信息架构与交互

### 6.1 三个关系视角

- **任务依赖（默认）**：Task → Task；突出 `blocked_by`、Ready、阻塞源和影响范围。
- **需求覆盖**：Requirement/Claim → Task → 验收结果；明确未覆盖条款和无来源任务。
- **实际执行**：Run → Operation → Attempt；按需展开 Agent、产物和恢复路径。

Workflow 阶段图作为流程上下文可展开查看。默认视图不同时叠加所有边；关联类型必须
明确区分覆盖、依赖、归属、生产/消费、验证及恢复，方向和图例保持一致。
没有已接纳 Task Map 时展示“计划尚未接纳”，可进入带版本标识的 Preview；不得虚构
已可调度的任务依赖图。无契约绑定的历史数据明确显示未知或降级来源。

### 6.2 页面草图

```text
项目：loshu-fms   目标：完整交付   Run：R1   Plan：v3   [实时 / 历史]
[Kanban] [Graph] [Loop]
Graph：[任务依赖] [需求覆盖] [实际执行]
筛选：全部 / 可执行 / 阻塞 / 失败 / 待审批
聚焦：选中节点 / 上游 / 下游 / 关联验收

+----------------------------------------------+-------------------------+
| [共享权限契约：通过]                          | T-07 车辆授权接口       |
|          |                                   | 当前阶段：Verify失败    |
|     +----+------------+                      | 处理人：dev-2           |
|     v                 v                      |                         |
| [车辆接口：返工2次] [维修接口：等待]           | 原因：越权请求未拒绝    |
|     |                 |                      | 关联需求 / 验收项       |
|     v                 v                      | 上游 / 下游任务         |
| [车辆页面]        [维修页面]                 |                         |
|     +---------+-------+                      | [看板定位] [失败尝试]   |
|               v                              | [查看证据] [讨论任务]   |
|       [业务链集成验证]                        |                         |
|               v                              |                         |
|       [部署与交付验收]                        |                         |
+----------------------------------------------+-------------------------+
```

此草图为交互示例，不代表 loshu-fms 当前运行状态。

### 6.3 可读性与规模

1. 默认节点为业务 Task，角色和 Provider 作为执行信息；换 Agent 不改变 Task 身份。
2. 首屏展示任务名、阶段、阻塞摘要与返工次数；日志、Token 和工具调用下钻查看。
3. 大项目先按能力或任务组折叠；展开与收起不改变 canonical ID，跨组依赖仍可追踪。
4. 选中节点高亮相关上游/下游，其余淡化；可切换仅看未完成工作或未闭合验收链。
5. 状态刷新保持位置、选择与缩放；仅拓扑改变时触发布局，提供显式重新布局操作。
6. 颜色同时配合文字/图标；取消、被替代、失败、未知、验证通过与交付完成分开表达。
7. 按需加载邻居与详情；聚合数说明统计范围，截断返回 total/included/truncated 等信息，
   并提供继续查询路径。未加载不等于不存在，前端搜索未命中不等于全项目不存在。
8. 默认保持只读画布。将来若支持调整依赖，产生 Plan Delta 和差异预览，经既有审批
   接纳后生效；拖拽位置只属于本地布局偏好。

现有 `@xyflow/react` 可继续作为画布，`d3-hierarchy` 保留给需求树。任务 DAG 的布局
实现需用跨组依赖与多父节点夹具比较 Dagre/ELK，评估体积、耗时及稳定性后再选型。

## 7. Loop、返工与恢复的关联

Graph 上一个 Task 可聚合显示“Verify 失败 2 次、影响 3 个下游任务”。点击后 Loop
定位精确操作和尝试，而不是只跳转到页面顶部。

```text
业务返工：Impl #1 → Verify #1 失败 → Impl #2 → Verify #2 → 集成
                     |                         |
                 缺口与证据                 新版本证据

执行异常：Attempt 中断 → Supervisor 观察 → Run Manager 决策
                                              |
                         +--------------------+-------------------+
                         v                    v                   v
                    重试 / 恢复       Autoresearch 诊断       人工介入
                         +--------------------+-------------------+
                                              |
                                          受控恢复动作
                                              |
                                  新 Attempt / 核验后的恢复点

计划修订：Plan v1 → 提出 Delta → 审批 / 接纳 → Plan v2
               保留旧任务、替代关系和证据有效性变化
```

业务依赖在每个已接纳计划内保持 DAG。重试、返工和重规划保留独立历史身份，不把旧
失败覆盖成成功，也不在 `blocked_by` 中引入返回边。Loop 可以聚合显示循环，展开后
按实际 occurrence 和因果关系查看。Stage Loop、Task Operation 与 Provider 工具调用
是不同粒度，不因界面聚合而合并其身份。

三类变化必须分开统计：业务缺陷返工、基础设施重试、计划范围修订。对于执行效果未知
的恢复，展示核对状态与恢复决策引用；不能直接显示“重试即可”。Autoresearch 和
Single Agent 接管仅展示已记录的受控恢复路径，不暗示所有失败都已支持自动接管。

## 8. 身份、范围、状态与数据权威

### 8.1 浏览上下文

建议定义共享的导航上下文，字段按视图需要选填：

```text
project_id + goal_id / feature_id
request_id + requirement_revision
workflow_run_id + task_map_generation
task_id + operation_id + attempt_id
candidate / target_commit + evidence_ref
as_of_seq（历史定位）+ lens / selection / filters（展示偏好）
```

这是一项前端导航合同建议，不是新业务状态 schema。优先复用现有字段名与解析器，
缺少绑定时明确无法定位，不根据标题、角色或时间邻近猜测关联。
`trace_id`、`workflow_run_id`、子调用 run ID 不视为天然同义，必须通过已有引用解析。

同一个 Task 可经历多个 Run/Operation/Attempt；旧尝试链接必须落在原历史范围。
项目范围 Board 切入特定 Run 时明确显示筛选；Goal 的任务跨 Run 时先展示所属 Run，
不静默选择最近一次。项目切换应清空不属于新项目的选择，并拒绝迟到响应覆盖新页面。

### 8.2 状态与时间一致性

展示分别表达执行、验证和交付闭合，不引入单个绿色状态覆盖所有维度。
Done 列中的取消/替代任务不计为成功交付；Task 完成数量不作为需求完成率。
原始需求条款是覆盖分母，不能只由已拆出的 Task 反向生成“全部需求”。历史派生覆盖
必须明确标识，不能以此证明不存在遗漏。

各投影保留事件游标、生成时间和历史范围。实时视图允许不同刷新时刻，但需显示延迟；
固定 `as_of_seq` 的审计模式要求状态与计数遵循相同截面。若当前 Store 无历史版本，
必须通过可用事件/版本工件重建或声明不支持，不能把当前 Store 当作旧快照。
计算逻辑统一复用，避免各页面分别按角色名或事件后缀重新裁决业务状态。

```text
需求 / 已接纳 Plan / Task Map 工件 ----+
TaskStore / Operation / Attempt ------+--> 只读关系投影 --> Kanban / Graph / Loop
事件账本 / 验证 / Candidate 证据 -----+          |
                                            详情与导航

用户提出变更 --> 受控动作 / 审批 --> 权威写入 --> 新事件 --> 投影刷新
```

`zf.yaml` 仍是唯一控制面；Agent/skill 负责拆分和依赖的语义判断，Kernel 负责机械
校验、状态转换、证据准入和派发。Graph 不写 canonical state，不重新判断 Judge 结论。
完整计划和证据仍在 required artifact/sidecar 中，图只返回引用与摘要。

## 9. 分期实施与验收

本表是建议实施顺序，尚不是已执行 backlog；开发前按当前 HEAD 复核，优先复用已有能力。

| 优先级 | 范围 | step → verify |
| --- | --- | --- |
| P0 | 身份与范围映射审计 | 沿输入到证据列出来源与消费者 → verify: 每条导航关系有可解析身份或明确缺失原因，无标题猜测 |
| P0 | 共享导航与状态含义 | 同一 Task 在三页往返、刷新、复制链接和返回 → verify: 项目、Run、代次和选中尝试保持正确，旧响应不串项目 |
| P1 | 任务依赖 Graph | 展示已接纳 `blocked_by` 和调度投影 → verify: 阻塞源、就绪任务及上下游与实际状态一致，取消前置不误解锁 |
| P1 | 详情与证据关联 | 从 Graph 失败节点进入 Loop 尝试及报告 → verify: 精确匹配 Operation/Attempt、目标版本和证据引用 |
| P2 | 修订与恢复路径 | 展示 Plan v1/v2、重试、返工、介入 → verify: 旧节点和旧失败可追溯，过期证据不冒充当前通过 |
| P2 | 大图与增量更新 | 使用接近完整产品规模的跨组依赖图 → verify: 不静默漏节点，折叠可恢复，刷新不跳布局，查询范围可见 |

建议验证组合：

- 纯模型夹具：多父依赖、共享任务覆盖多条 Claim、无来源任务、未覆盖条款、历史代次、
  取消/替代任务、旧证据、数据截断以及项目切换迟到响应。
- Backend 投影：验证 scope、引用解析、状态来源和历史截面；沿直接调用方覆盖共享合同。
- Web 组件和浏览器：三视图双向定位、复制链接、返回、布局稳定和异常信息；按仓库规则
  使用 Docker Playwright，不安装宿主浏览器。
- Runtime mock E2E：Plan 审批等待、部分任务并行、Verify 失败返工、恢复后继续、计划
  修订以及最终闭合，核对图与 canonical state/events 一致。
- 真实任务：可使用用户授权的 loshu-fms 运行作只读观察。真实运行未出现的分支用隔离
  夹具验证，不为演示向真实项目注入失败；mock 与真实 Provider 证据分别报告。

参考现有 [execution graph 测试](../../tests/test_execution_graph.py)、
[goal coverage 测试](../../tests/test_goal_coverage_graph.py)、
[task pipeline 测试](../../tests/test_task_pipeline_reconciler.py) 与
[Work 图模型测试](../../web/tests/deliveryWorkGraphModel.test.ts)。测试存在不等于本轮已执行。

首批成功标准是：用户从一个 Blocked 卡片出发，能看到准确阻塞源、受影响需求、对应
失败尝试及证据，再返回同一任务处理；整个过程不丢 Run/Plan 版本，也不改变运行真相。
