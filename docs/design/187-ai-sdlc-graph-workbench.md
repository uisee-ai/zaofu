# 187 — AI SDLC Graph 工作台：生命周期、模块任务与 Workflow 执行

> 状态: implemented（本批 8 项任务及真实 Provider / 浏览器验收完成）。实现、验证映射与规模/外部集成限制见 [187 实施记录](../impl/187-ai-sdlc-graph-workbench.md)。所有 ASCII 图均为设计原型，任务、提交和状态为示例。
> 归属: `docs/design/`；承接 [182：计划与验收证据闭环](182-plan-review-task-slicing-and-evidence-closure.md)、[185：Agent 路由与恢复](185-agent-routing-delegation-and-workflow-recovery.md)、[186：Kanban、Graph 与 Loop 关联](186-unified-delivery-kanban-graph-and-loop.md)。

## 1. 设计来源、目标与范围

本设计以用户提供的 `/tmp/ai_sdlc_graph_design.md` 为产品基线，结合 Ordewell 等官方资料和造父当前代码。原稿读取日期为 2026-10-25，SHA-256 为 `7f2c523fb4fd131ced791b0b238399075f250c7005e96dc964124a65b8a6725f`。本文保存落地所需的视图、交互与边界，不依赖该临时文件长期存在。

目标是通过图连接产品需求、工程模块与任务、Agent 执行、验证证据和最终交付：

```text
Idea -> Requirement / PRD -> SDD / Architecture -> 模块与 Task DAG
     -> Implementation -> Verify -> Ship / Release -> Production
```

这条链表达产品生命周期，各项目的实际 Workflow 由有效配置确定。研究流程可以交付报告，缺陷修复可以复用已有设计；不能为了画满生命周期而制造未发生的 PRD、SDD 或发布节点。

核心验收目标：

1. 在 Workflows 页面看见完整生命周期，并展开需求、模块、任务和证据。
2. 展示基于 PRD/SDD 的模块分解、跨模块任务依赖、阻塞原因与并行条件。
3. 渲染 `zf.yaml` 编译得到的实际流程，并在结构上叠加对应 Run 的任务流转。
4. 看见 Codex / Claude Code 的真实会话、操作和尝试，区分等待、运行、返工与失败。
5. 从任意需求、任务、提交、证据或发布记录双向追溯，并识别变更影响。
6. 完成必须关联被接纳的证据；验证通过、交付闭合、已发布和生产健康分别表达。

本设计扩展 186 的工作台方向。生命周期总览、PRD 模块任务图和 Workflow 流程执行图均为核心能力；不能仅保留阶段进度条或任务列表来替代它们。

## 2. 产品信息架构

```text
Workflows 工作台
 |
 +-- 生命周期总览                         默认首页
 |     Idea / PRD / SDD / 实现 / 验证 / 发布 / Production
 |
 +-- 模块与任务
 |     +-- PRD 模块分解图
 |     +-- 模块内任务图
 |     +-- 跨模块依赖 DAG
 |
 +-- Workflow 执行
 |     +-- zf.yaml 编译结构
 |     +-- 当前 Run 与任务流转
 |     +-- 多 Agent 协作
 |     +-- 单任务执行详情
 |
 +-- 追溯与变更
 |     +-- 需求到设计、代码、证据和发布
 |     +-- 需求 / 设计 / 计划修订影响
 |
 +-- 验证与交付
 |     +-- 门禁、失败与返工
 |     +-- 候选产物、发布与生产状态
 |
 +-- 时间线
       +-- 执行 / 排队 / 阻塞 / 重试
       +-- 并行情况与关键路径分析
```

原讨论的三个视角继续保留：A Product / Requirement Graph 对应需求、模块和覆盖；B Engineering Dependency Graph 对应工程依赖；C Execution / Delivery Graph 对应 Workflow 运行、验证和交付。生命周期图负责总览，各视角共享身份和导航上下文。

设计稿的业务图均有明确入口：

| 原稿图 | 工作台位置 | 主要问题 |
| --- | --- | --- |
| Lifecycle Overview | 生命周期首页 | 产品走到哪里，哪里尚未闭合？ |
| Dependency DAG | 模块与任务 | 哪些任务有前置条件，哪些可以并行？ |
| Agent Execution | Workflow 节点详情 | 这次尝试正在做什么，发生过什么？ |
| Agent Collaboration | Workflow 协作视角 | 谁负责执行、验证和交接？ |
| Traceability | 追溯与变更 | 哪些代码、测试与发布落实了这个需求？ |
| Change Impact | 追溯与变更 | 哪些对象确定受影响，哪些需要评估？ |
| Verification / Rework | 验证与交付 | 为什么失败，如何修复，是否重新验证？ |
| Release / Production | 验证与交付 | 发布了哪个产物，哪个环境处于什么状态？ |
| Timeline / Critical Path | 时间线 | 实际如何并行，等待和返工花了多久？ |

PRD 模块分解和 `zf.yaml` 编译结构是结合造父明确补充的两部分。原稿的 Workbench、Graph 数据模型和系统事件流分别落实为本文的页面布局、关联投影和权威边界。

## 3. 当前实现与关联缺口

设计调研的静态基线为 `dev` 的 `5fc688f`；下表记录当时的入口与缺口。实施后的 Provider、浏览器和 Runtime 验证另见 [实施记录](../impl/187-ai-sdlc-graph-workbench.md)，不以此历史审计表代替验收。

| 能力 | 当前代码证据 | 可复用部分与缺口 |
| --- | --- | --- |
| Workflow 编译 | [graph.py](../../src/zf/core/workflow/graph.py) | 阶段、事件边、fanout、汇聚、依赖屏障、门禁和返工路由；需按所选路线、配置版本呈现 |
| 阶段运行投影 | [workflow_trace.py](../../src/zf/runtime/workflow_trace.py) | 静态图关联事件和 Task；Run 隔离和历史配置绑定需专项验证 |
| Task Pipeline 叠层 | [workflow_graph.py](../../src/zf/web/projections/workflow_graph.py)、[task_pipeline_projection.py](../../src/zf/runtime/task_pipeline_projection.py) | 已含任务、Operation、执行位置、队列、容量和闭合信息；需要接入统一工作台 |
| 工程依赖 | [task_map.py](../../src/zf/runtime/task_map.py)、[execution_graph.py](../../src/zf/runtime/execution_graph.py) | 已有 Task Map 与 `blocked_by`；不能把 `phase` / `plan_section` 直接当成模块树 |
| 需求与验证覆盖 | [goal_coverage_graph.py](../../src/zf/runtime/goal_coverage_graph.py) | 已有关联条款、任务、验证、目标闭合和版本绑定；完整模块、设计及发布关联仍需审计 |
| Work 图 | [deliveryWorkGraphModel.ts](../../web/src/components/delivery-trace/deliveryWorkGraphModel.ts) | 当前以 goal/claim/task/unmapped 和 tree/secondary 表达覆盖；未完整表达模块和任务依赖图 |
| Run / Loop | [RunGraphView.tsx](../../web/src/components/delivery-trace/RunGraphView.tsx)、[LoopPageV2.tsx](../../web/src/components/delivery-trace/LoopPageV2.tsx) | 已有阶段展开、fanout、尝试、返工和历史入口；需要精确跨视图定位 |
| Workflows 入口 | [ProjectionPage.tsx](../../web/src/components/projection/ProjectionPage.tsx)、[WorkflowProposalPage.tsx](../../web/src/components/workflow/WorkflowProposalPage.tsx) | 当前主要进入 Proposal 与阶段预览；准备、Run 和交付图需连通 |
| 运行链归属 | [run_chain.py](../../src/zf/runtime/run_chain.py) | 部分任务按事件时间窗口归属；并行和返工不能仅凭时间相邻确定阶段 |
| 关键路径指标 | [workflow_trace.py](../../src/zf/runtime/workflow_trace.py) | 当前 `critical_path_ms` 为阶段耗时求和，不能作为并行 DAG 的关键路径分析 |

实现优先补齐这些关联及消费者，不另建 Task schema、事件账本或调度器。历史设计文件在当前 checkout 中不完整时，以 [AGENTS.md](../../AGENTS.md)、现存代码和测试为准。

## 4. 生命周期首页与工作台布局

生命周期首页是一张可展开的产品关系图。阶段汇总向下展开到模块、任务、执行与证据，支持共享节点及跨分支关系。

```text
[Idea]
   |
[PRD / 需求与验收条款]
   |
[SDD / 系统设计]
   |
   +------------------+------------------+------------------+
   |                  |                  |                  |
[基础模块]         [玩家模块]         [战斗模块]         [计分模块]
   |                  |                  |                  |
[任务子图]         [任务子图]         [任务子图]         [任务子图]
   |                  |                  |                  |
   +------------------+------------------+------------------+
                              |
                    [实现与集成 / 执行中]
                              |
                    [测试与审查 / 部分完成]
                              |
                       [产品验收 / 等待]
                              |
                       [交付版本 / 等待]
                              |
                       [部署与生产状态]
```

该图为产品级聚合关系，不规定所有 Task 必须同时经过每个阶段。实际执行、测试和集成允许交错推进；各节点明确是产物、活动、门禁还是聚合视图。PRD/SDD 文档不天然等于一个可执行阶段。

```text
+--------------------------------------------------------------------------------+
| 项目 / PRD 版本 / Plan 版本 / Workflow / Run                  [实时] [历史回放] |
+--------------------------------------------------------------------------------+
| [生命周期] [模块与任务] [Workflow 执行] [追溯与变更] [验证与交付] [时间线]       |
| [完整 YAML] [Agent Chat]                                                       |
+----------------------+----------------------------------+----------------------+
| 模块与需求树         | 当前图画布                       | 节点 Inspector       |
|                      |                                  |                      |
| 打飞机游戏           | 生命周期图                       | 来源 PRD / SDD       |
|  + 基础模块          | 或模块任务 DAG                   | 模块与任务关系       |
|  + 玩家模块          | 或 Workflow 流程执行图           | 当前阶段 / Agent     |
|  + 战斗模块          |                                  | 阻塞与下游影响       |
|  |  + T3 射击碰撞    | 选中 T3，在各视图保持定位        | Diff / 测试 / 审查   |
|  + 计分模块          |                                  | 尝试历史 / 发布记录  |
+----------------------+----------------------------------+----------------------+
| Live Events / 当前任务时间线                                                   |
+--------------------------------------------------------------------------------+
```

小屏将树和 Inspector 收为抽屉；关键状态用文字与图标共同表达。图布局拖动是显示偏好，业务依赖、阶段和审批变更通过既有受控动作处理。

## 5. PRD 模块分解、Task DAG 与覆盖

### 5.1 分解来源

Planner 根据确认的 PRD/需求、SDD 和代码现状提出模块、任务及关联，Critic 审查覆盖与切分，计划接纳后形成展示依据。预览中的模块和 Task 明确标记 draft，不冒充已物化的 canonical Task。

模块表示业务能力或必要的共享技术边界，不等于目录、角色、Provider 或 Workflow 阶段。设计模块与产品模块可通过明确关系关联；共享任务可以支撑多个模块，不能复制 Task 身份或重复计算总数。

```text
PRD：打飞机游戏
 |
 +-- R1：玩家可控制飞机移动
 |      |
 |   [玩家模块 / SDD：输入与运动]
 |      +-- T2：键盘控制与移动边界
 |
 +-- R2：可以射击并击毁敌机
 |      |
 |   [战斗模块 / SDD：弹幕与碰撞]
 |      +-- T3：子弹、敌机与碰撞处理
 |
 +-- R3：命中后正确计分
        |
     [计分模块 / SDD：得分规则]
        +-- T4：计分与难度增长
```

当前 Task Map 的 `goal_claim_ids`、`phase`、`plan_section`、`blocked_by` 可作为关联基础，不能推定已有完整模块合同。实施时先审计既有计划与 Task Map 生产者/消费者，再在现有合同或受引用计划工件中补充稳定模块身份、来源条款、设计引用和任务关联；不另建第二套 Task。

### 5.2 依赖与并行

```text
                    [T1：共享游戏实体 / 已集成]
                              |
                   +----------+----------+
                   |                     |
          [T2：玩家移动 / 验证中] [T3：射击碰撞 / 返工中]
                   |                     |
                   |            [T4：计分难度 / 等待 T3]
                   |                     |
                   +----------+----------+
                              |
                    [T5：集成与验收 / 等待]
```

按模块折叠时仍显示跨组依赖；展开后显示 Task 的前置条件、输入输出、验证责任与下游影响。工程 Task 的依赖在每个已接纳计划内保持 DAG；返工回边属于执行历史，不写进 `blocked_by` 制造环。

就绪和实际派发复用当前路线的调度判定，展示待审批、前置未满足、失败阻塞、等待集成、容量或背压等具体原因。独立任务也可能因资源限制等待；取消前置不能自动视为成功。依赖满足条件按实际合同显示，不一律解释为“上游 Agent 已完成”。

模块状态分别展示需求覆盖、实施、验证和交付闭合。覆盖分母来自原始确认条款，未拆出的需求也必须可见；任务完成数不能充当需求满足率。历史推导的覆盖标注来源，不能证明原始需求没有遗漏。

## 6. zf.yaml 流程结构与运行叠层

### 6.1 配置与路线

主画布通过现有配置加载、路线选择与 Workflow 编译路径取得有效结构，不根据阶段名称硬编码顺序，也不直接把 YAML 文本行变成执行事实。

```text
候选配置 + 校验结果 -> 配置预览图 / 尚未启动
批准并绑定的有效配置 -> 对应 Run 的结构图 + 实际运行叠层
历史配置版本 + 历史证据 -> 历史运行图
```

运行图绑定对应配置版本或摘要；当前 `zf.yaml` 修改不能静默重写历史 Run。一个项目含多条路线时明确选择 Workflow/Run，避免把多个路线的角色和阶段混成一个流程。

PRD、Issue、Refactor、Research 和已注册 Workflow 均以实际能力呈现；某条路线只有报告交付时不强制展示代码发布。未知节点类型应有通用详情及诊断，不能静默遗漏；新增图能力不意味着任意 YAML 已获得编译或执行支持。

### 6.2 结构内的任务流转

```text
Workflow：PrdFlow / 配置版本 cfg-03 / Run #03

 [Scan] ---> [Planner] ---> [Plan Critic] ---> [Plan Approval]
   OK           OK              OK                 OK
                 ^               |
                 +---- 修订 -----+
                                                    |
                                             [Task Map 接纳]
                                                    |
 +--------------------------------------------------------------------+
 | Task Pipeline / 执行中                                             |
 |                                                                    |
 | [Implementation] ---> [Verify] ---> [Acceptance Review] ---> [集成] |
 |        |                  |                                    |   |
 |    T3 / Impl #2       T2 / Verify #1                        T1 已集成|
 |    Codex 执行中       Claude Code 执行中                            |
 |        ^                  |                                        |
 |        +-- [返工判定] <----+                                        |
 |             T3 上次验证失败                                        |
 |                                                                    |
 | 等待：T4，因为 T3 的前置条件尚未满足                               |
 +--------------------------------------------------------------------+
                                                    |
                                             [目标闭合判定]
                                                    |
                                                [交付结果]
```

该图的阶段、审批与返工顺序为示例，落地时必须来自所选有效配置。节点区分 stage、fanout/aggregate、dependency barrier、gate 和任务流水容器；复杂角色池默认折叠，展开显示 lane、操作和实际会话。

任务流转表示已接纳的状态迁移：

- Task 身份稳定，Operation/Attempt 保留每次实施、验证、返工和恢复身份。
- 以实际绑定显示活动位置；一个 Task 若有多个合法并行操作，应分别展示，不能强制只有一个当前位置。
- 仅在已记录的迁移发生时强调对应连线；正在运行的节点可以有活动指示，但不能用循环动画暗示持续发生交接。
- `worker reported done`、操作结束、门禁通过、集成完成分别显示，文本自述不会直接推动成功状态。
- 阶段汇总按当前 scope 的实际运行和门禁计算；部分子任务通过不代表 fanout、模块或目标已闭合。
- 旧失败保留在尝试历史；新尝试运行不能覆盖旧失败证据。缺少精确关联时显示未知或降级来源。

### 6.3 准备阶段也有图

```text
[Idea] -> [需求澄清] -> [Workflow 配置提案] -> [审批] -> [启动 Run]
              |                  |
      [待回答的问题]     [完整 YAML / 校验结果]
                                 |
                       [Agent Chat 修改与预览]
```

从 CLI、Channel Group 或 Kanban Agent 进入的同一 Request 应可在 Workflows 定位。未生成计划时显示已知输入与等待原因；配置阻塞时保留完整候选预览及诊断，不能因尚无 Run 而显示空白，也不能绕过准入门禁。

Chat 修改产生带版本的需求、计划或配置提案，展示差异并沿既有审批应用。语义生成、计划审批、配置应用和 Run 启动保留各自真实边界。

## 7. Agent 执行与协作

### 7.1 单任务执行图

```text
[T3 / Impl #2 / Codex / 执行中]
                 |
          [读取文件 / 已结束]
                 |
          [修改碰撞逻辑 / 已结束]
                 |
          [运行命令 / 执行中]
                 |
          [输出与退出结果]
                 |
       [验证及门禁 / 等待接纳]
```

工具调用图来自 Provider 已采集事实，不要求每次执行固定按上述顺序。显示 session、工作区、开始/结束时间、当前工具与可用日志；只记录到“已派发”时不能宣称 Provider 正在编辑文件。缺失遥测、断连或长期无活动需显示观测新鲜度，不直接判定业务失败。

聚合模式按操作汇总，展开模式显示每次调用、尝试及因果关系。工具调用顺序与嵌套不能推导工程任务的 `depends_on`；工具退出成功也不能替代独立验证。

### 7.2 多 Agent 协作图

```text
[Planner Agent / 提出计划] -> [Critic / 审查] -> [计划接纳]
                                                    |
                                      [Kernel / 依赖与门禁派发]
                                                    |
                      +-----------------------------+------------------+
                      |                             |                  |
                [Codex / Impl]              [Claude Code / Verify] [Review]
                      |                             |                  |
                [代码与提交]                   [验证报告]         [审查结论]
                      +-----------------------------+------------------+
                                                    |
                                         [集成 / 目标闭合判定]
```

这是责任与产物关系示例，不暗示验证早于目标产物准备完成。角色职责与 Provider 分开表示，具体模型和执行会话是绑定信息；换 Provider 不改变 Task 身份。连线区分派发、交接、生产、消费和审查，显示关联产物与发生时间。

原稿的 Orchestrator “Plan + Assign” 在造父中拆为 Agent 提出语义计划、Kernel 负责确定性接纳与派发，不能把配置中的 orchestrator 角色当成第二套全局状态机。

## 8. 端到端追溯与变更影响

### 8.1 需求到发布的双向追溯

```text
[Idea / Requirement 修订]
             |
       [PRD 原始条款 R2]
             |
       [SDD 碰撞设计] -> [战斗模块]
             |                 |
             +--------+--------+
                      |
                  [任务 T3]
                      |
              [Operation / Attempt]
                      |
             [Commit / 候选产物摘要]
                      |
              [测试与审查证据]
                      |
                [交付闭合结果]
                      |
              [Release / 部署记录]
```

任意节点可查询来源与下游；发布记录可反查需求，失败测试可反查具体提交、任务和尝试。关系是多对多，不把时间先后等同于验证或实现关联。文件级追溯绑定具体版本，只有当前路径不能证明当时验证过哪份代码。

### 8.2 需求变化后的影响

```text
[PRD R2 v2：新增穿透弹规则]
                |
         [影响分析结果]
                |
       +--------+---------+
       |                  |
[碰撞设计：待复核]  [计分规则：待评估]
       |                  |
[任务 T3：需调整]   [任务 T4：待确认]
       |                  |
       +--------+---------+
                |
      [相关测试：待重新验证]
                |
      [当前版本交付检查：等待]
```

沿显式关系计算可证明的影响范围；Agent 对语义影响、遗漏关系和修订建议提供分析工件，经受控流程接纳。区分确定受影响、待评估、不适用和经审查可复用，不自动把全部下游认定为必须重写。

保留旧版本的成功结论和证据，另行表达它们对当前修订是否适用。Plan Delta、新任务和替代关系必须可追溯，不能删除旧节点制造“从未失败”的历史。

## 9. 验证、失败回流、发布与生产

### 9.1 验证和修复闭环

```text
[需求 R2 / 验收条款]
           |
[任务 T3 / Commit A] -> [验证 #1：失败] -> [失败证据]
                                               |
                                          [返工决策]
                                               |
[修复 #2 / Commit B] <--------------------------+
           |
     [验证 #2：通过]
           |
     [审查与交付门禁]
```

门禁展示命令与退出结果、报告、环境、执行时间、责任角色和证据引用；按路线展示适用的静态检查、单元、集成、浏览器和产品验收。不能强制所有项目使用同一测试顺序。

证据需关联正确的需求/合同修订、计划代次、目标提交或产物摘要、操作和验证范围。Verifier 给出语义结论，Kernel 校验机械条件及准入，图展示已接纳判定；前端不再裁决质量。Agent 自述、退出码 0 或存在报告文件均不足以独立证明交付成功。

基础设施执行失败与产品验证失败分开；前者不能直接判为产品不合格。恢复可以是同一操作的新 Attempt、业务返工的新操作代次、新修复任务或人工介入，按现有恢复路径呈现。

### 9.2 发布与生产环境

```text
[当前目标验证通过]
        |
[构建产物 / digest]
        |
[Staging 部署] -> [Smoke / E2E]
                        |
                  [Release Gate]
                        |
                  [Production 部署]
                        |
                  [健康观测 / 时间窗]
                        |
              +---------+----------+
              |                    |
          [Healthy]         [Degraded / Incident]
                                   |
                           [受控回滚 / 修复]
                                   |
                               [重新验证]
```

发布信息绑定具体产物、环境、时间和结果。`Verified`、交付闭合、`Shipped`、`Production Healthy` 是独立事实；生产健康可以在发布后变化，需显示观测时间和范围。未接入部署或监控时显示未接入/未知，不能从测试通过推导健康。

CI、部署与健康信息经既有集成和受控写入路径接入，完整报告保存为受引用工件。图中的发布、回滚和重试操作仍遵守权限、审批与幂等规则，查看关系本身不触发执行。

## 10. 时间线与关键路径

```text
任务 / 时间       10:00       10:10       10:20       10:30

T1 共享实体       [执行][验证][集成]
T2 玩家移动                         [实施------][验证----]
T3 射击碰撞                         [实施][失败][修复----]
T4 计分难度                         [等待 T3-------------]
T5 集成验收                         [等待前置------------]

可切换：按 Task / Agent / Stage 分组；展开 Operation / Attempt
```

时间段来自记录的开始、结束、排队和恢复事件；无法重建时标记缺失，不根据当前状态伪造时间。业务返工、基础设施重试和计划修订分别统计。

关键路径分析使用特定计划版本的依赖 DAG、明确的时长依据及约束范围。区分已执行路径耗时、剩余工期估计、依赖瓶颈和资源排队；有环的聚合返工图不能直接套用 DAG 关键路径算法。缺少估计时展示阻塞链和实际耗时，不能给出精确 ETA。现有阶段耗时总和不能直接作为此功能的实现。

## 11. 关联投影、身份与状态权威

### 11.1 统一关系，不复制业务状态

以下是投影需要表达的概念，不是新建一组 canonical stores 的要求。字段优先复用现有合同；缺失内容在实施时审计并扩展已有生产/消费链。

| 概念 | 身份与来源要求 |
| --- | --- |
| Request / Requirement / PRD | 输入来源、确认修订、条款身份和内容摘要 |
| SDD / Module / Plan | 设计工件、模块稳定身份、计划代次、原始条款与 Task 关联 |
| Workflow definition / Stage / Gate | 所选路线、有效配置版本与摘要、编译节点身份 |
| Run / Stage occurrence | 对应定义、运行身份、阶段发生记录；定义节点与多次发生不混同 |
| Task / Operation / Attempt | 复用 canonical 身份、合同修订、尝试与替代关系 |
| Agent / Session | 角色、Provider、模型、会话与执行位置的实际绑定 |
| Artifact / Candidate / Evidence | 引用、摘要、目标版本、生产者、适用范围与判定来源 |
| Release / Environment / Health | 产物、环境、部署记录及带时间范围的观测 |

关系至少区分：需求细化、模块包含、实现、工程依赖、控制流、执行、生产/消费、验证、发布、替代与因果。方向及含义有固定图例，例如 `Task --implements--> Requirement`、`Task --depends_on--> Task`、`Evidence --verifies--> Candidate`。计划依赖、运行因果、覆盖与产物关联不能统一画成无语义的箭头。

关系的来源应可检查：已接纳计划/配置、运行事件、受引用工件，或尚未接纳的分析建议。建议关系不能影响调度或覆盖结论。

### 11.2 运行与投影边界

```text
确认需求 / 已接纳 Plan / Task Map / 有效 zf.yaml
                               |
                     [Kernel / 受控动作]
                               |
          +--------------------+--------------------+
          |                    |                    |
     [Canonical Stores]   [EventWriter / EventLog] [Required artifacts]
          |                    |                    |
          +--------------------+--------------------+
                               |
                   [只读 Graph / Trace 投影]
                               |
                    [Graph API / 实时通知]
                               |
                     [Web 多视图工作台]

Provider 工具调用与日志 -> 既有采集和关联路径 -> 执行详情
用户修改意图 -> 提案 / 审批 / 受控动作 -> 权威更新 -> 投影刷新
```

`zf.yaml` 仍是唯一控制面，尊重 `project.state_dir`。事件账本记录发生、顺序、因果和判定引用；当前态由相应 Store 管理；完整语义及大型证据保留在受引用的工件/sidecar。Graph API 不直接修改 canonical state，Graph Engine 不成为第二个调度器。

Provider 遥测只证明其观察到的执行事实；完成、调度、恢复和交付准入仍走 Kernel。读取日志与 Diff 沿用权限、路径校验和脱敏规则，图不泄露 token、环境秘密或未经授权的文件内容。

### 11.3 范围、历史与状态含义

共享浏览上下文按需包含 project、request/requirement revision、goal/feature、workflow/run、配置摘要、plan generation、task、operation、attempt、target commit、evidence ref 和事件游标。切换图、刷新、复制链接和返回均应保留适用范围；标题、角色名或时间接近不能替代身份解析。

实时投影返回生成时间、来源游标及是否滞后。历史模式要求同一事件截面下的拓扑、状态和计数一致；若现有 Store 无历史快照，需通过可用事件与版本工件重建，或明确不支持，不能把当前态伪装成旧时刻。

执行、验证、交付、发布和健康分别聚合。取消、跳过、被替代、未知与成功分开；被批准的跳过或豁免显示依据与范围。实时断连、遗漏数据和业务失败也分别显示。

## 12. 交互与规模约束

1. 点击模块高亮所属任务和跨组依赖；点击 Task 保持同一身份切换到 Workflow、证据与时间线。
2. 节点 Inspector 提供来源、上下游、Agent、日志、Diff、证据、历史与受控动作入口。
3. 支持按状态、模块、需求、Agent、阶段、Run 和版本过滤；总数说明当前统计范围。
4. 稳定 ID 驱动增量更新，状态更新保持位置、选中与缩放；拓扑变化才重新布局。
5. 大图按模块/阶段折叠，按需加载邻居和详情；返回 total/included/truncated 等信息，提供继续查询路径。未加载不能显示为“不存在”。
6. 复用现有 live events 与刷新机制；重连按游标补齐或重新取一致快照，去重处理；迟到响应不能覆盖新项目或新 Run。
7. 初期复用 React Flow；需求层级可使用现有树布局，多父节点和跨组任务 DAG 用真实夹具比较 Dagre/ELK 后选型。布局能力与业务状态解耦。
8. 所有视图共享状态解释和引用解析，复用已有 Kanban、Delivery、Loop、Trace 详情，避免各页面重复裁决。

## 13. 分期实施与验收

下表是验收范围。2026-10-25 已按用户授权建立并完成 8 项任务；真实 Run 经独立 Verify、Discovery、Judge 和内核门禁闭合，6/6 目标通过。实现入口、验证结果和明确限制见实施记录。

| 阶段 | 范围 | step -> verify |
| --- | --- | --- |
| P0 | 身份与来源审计 | 从 PRD 条款沿模块、Task、Run、Attempt 到证据梳理生产者/消费者 -> verify: 每条关系可解析或明确缺失，不使用标题猜测 |
| P0 | 模块与需求关联 | Planner 输出模块分解并经计划接纳 -> verify: 未覆盖条款可见，共享 Task 不重复计数，draft 不冒充 canonical Task |
| P1 | 生命周期与准备阶段 | CLI/Channel Group/Kanban Agent 创建请求 -> verify: 同一 Request 可定位；待澄清、候选 YAML、校验阻塞与审批可见，无 Run 时不空白 |
| P1 | Workflow 结构与流转 | 预览并启动一条路线，出现并行、等待与汇聚 -> verify: 图匹配有效配置和实际运行，配置修订不改写旧 Run |
| P1 | 模块 DAG 与执行关联 | 从模块选择 Task 并切换到流程节点、Attempt -> verify: 上下游、阻塞原因与调度一致，多父依赖及跨模块关系不丢失 |
| P1 | 追溯、证据与基础时间线 | 实施、验证失败、修复、重新验证 -> verify: 旧失败保留，证据绑定正确版本，Agent 自述不变绿，时间来自真实事件 |
| P2 | 变更影响与协作总览 | 修订需求/计划，切换执行者与恢复方式 -> verify: 已确认影响与待评估分开，历史及交接可追溯，未接纳建议不改调度 |
| P2 | 发布与生产 | 接入实际构建、部署和健康记录 -> verify: 产物、环境和观测时间可追踪，未接入时显示未知，发布不自动等于健康 |
| P2 | 大图与关键路径分析 | 用跨组 DAG、资源等待、重试和时长缺失夹具分析 -> verify: 不漏节点、不重复计数、无虚假 ETA，状态刷新不跳布局 |

第一批必须贯通生命周期、模块任务、Workflow 流转与证据，不能先交付孤立静态图，再把需求和验证关联无限后置。

验证范围：

- 纯投影与合同：多父依赖、共享任务、未覆盖需求、取消/替代、旧证据、跨 Run 同名阶段、不同配置版本、丢失引用和历史截面。
- Web 与 API：跨视图定位、复制链接、折叠、缺失/截断提示、迟到响应、断线重连、候选配置阻塞和运行图版本绑定。
- Runtime mock E2E：计划审批、依赖调度、并行与汇聚、验证失败返工、基础设施恢复、目标闭合；核对图与 canonical state/events 一致。
- 真实 Provider 观察：单独授权与报告，不能将 mock 当作真实 Codex/Claude Code 执行证明；不向真实项目人为注入失败来演示。
- 浏览器验证使用仓库规定的 Docker Playwright；临时服务绑定 `0.0.0.0`、使用 `8002+` 和隔离 state_dir，结束后清理。

验收状态以实施记录和任务中的证据为准；真实 Provider 验证与 Mock 验证分别记录。

## 14. 外部调研依据与采用边界

资料检索日期为 2026-10-25。以下是官方资料/源码的能力说明及设计借鉴，不代表在本机安装并完成竞品 E2E，也不构成新增依赖承诺。

| 来源 | 已确认内容 | 在造父的采用方式 |
| --- | --- | --- |
| [Ordewell](https://ordewell.ai/) | 读仓库、澄清、计划编辑、按任务分配执行者；完成判定主要依赖工具提交的完成报告 | 借鉴准备到执行的连贯交互；交付闭合继续使用造父的证据与准入机制 |
| [Ordewell PRD](https://ordewell.ai/prd-before-code) | PRD 可选，不强制检查计划覆盖全部用户故事 | 保留原始需求分母与显式覆盖检查，不能仅有文档路径就视为已覆盖 |
| [Ordewell 调度源码](https://github.com/ordewell/ordewell/blob/main/packages/core/src/services/readiness.ts) | 统一表达审批、依赖、合并、用量等等待原因，按可用槽选择任务 | 图与实际调度复用同一判定来源，显示具体阻塞原因 |
| [Azure DevOps 端到端追溯](https://learn.microsoft.com/en-us/azure/devops/cross-service/end-to-end-traceability?view=azure-devops) | 关联需求、提交、PR、构建、测试及发布 | 设计双向追溯与缺失关联提示 |
| [Langfuse Agent Graphs](https://langfuse.com/docs/observability/features/agent-graphs) | 聚合重复步骤或展开每次调用 | 区分结构、实际尝试与调用粒度；调用关系不冒充业务依赖 |
| [Grafana Agent 图](https://grafana.com/docs/grafana-cloud/observe-and-act/agent-observability/instrumentation/agent-dependencies-and-workflows/) | 区分模型调用依赖和高层 Workflow 步骤 | 保留不同图层粒度及明确关联 |
| [Dagster Asset selection](https://dagster.io/docs/guides/build/assets/asset-selection-syntax) | 查询上游、下游、根与终点及局部关系 | 支持阻塞来源和变更影响范围查询 |
| [SLSA Build Provenance](https://slsa.dev/spec/v1.2/build-provenance) | 关联产物、构建输入、提交、执行信息与摘要 | 发布与产物来源绑定；来源证明不替代功能验收 |
| [React Flow 布局指南](https://reactflow.dev/learn/layouting/layouting) | 外接 Dagre、D3、ELK 等布局方案 | 复用现有画布，按实际树/DAG/分组复杂度验证布局选择 |
