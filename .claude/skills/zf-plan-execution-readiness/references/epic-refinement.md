# 薄 Epic、任务细化与依赖审阅

Epic 是需求和 Task 之间的规划阅读层。复用 `requirement_spec`、`planning_result`、
`execution_spec` 等当前合同允许的正文，不新增 EpicStore、Task 字段或调度状态。

## 先保留范围，再细化可执行任务

薄 Epic 记录：用户结果、来源/AC、包含与排除范围、前置决定、风险和验收方法。
开始时不必填满所有实现细节；原始 Goal 的 mandatory 要求始终保留。

1. 按可观察行为划 Epic，再为接近执行的行为补实体/状态、接口、失败后态及用例。
2. 形成 Task 前确定输入、输出、路径 owner、真实依赖、AC、验证命令及责任人。
   单个原子事务保留一个实现 owner，测试属于该实现片，不另建后置补测试任务。
3. 用阅读标签区分 **ready-to-refine**（范围明确）、**ready-to-start**（完整合同
   且当前配置准入）、**blocked**（点名缺失决定/输入）；这些不是新的状态枚举。
4. Task Map 提交仍必须覆盖本次获准 Goal。未来 Epic 未细化时，不能塞入无合同
   占位 Task，也不能把它算完成。保留草稿，或由有权者明确阶段 Goal 后再提交；
   原总目标的剩余义务和返回条件仍可审阅。
5. 接纳后的契约变化走受控 replan：来源变化 → 受影响 Epic/Task/AC/证据 → 修订。
   未受影响工作仅在证据仍 current 时保留；禁止原地改冻结 Task Map 或沿用旧 verdict。

[阅读示例](epic-plan-example.json)使用现有 `task-scoped-plan-input.v1`：`shared`
保留所有 Epic、未决问题和完整 Goal；任务只读取自身正文与 shared，Candidate 读取
全文。示例是规划草稿，既不是可派发 Task Map，也不是完整 Goal 的通过证据。
固定 schema 的矩阵不能套此包装，正式端口仍受当前 Plan Package 合同约束。

## 拆分、依赖和并行必须有理由

每条依赖标出具体输入或证据生产者，不以任务名称、Epic 顺序或编号推导。
审阅 `plan-execution-review.graph` 的 `longest_dependency_path`、`tasks_by_level`、
`direct_dependencies`、`direct_dependents`，解释最长依赖链是否必要及变更影响范围。
缺失依赖或环导致完整路径不可得时，先修图；同层仅表示拓扑关系，不证明可以同时写。
最长链按节点数计算，不含工时，不可称为已测量的关键耗时路径。

并行建议同时检查：全图独占路径、共享接口版本、数据库/schema/迁移 owner、
fixture/测试账号、端口、服务、工作区及后续集成边界。共享资源不能隔离时给出
串行理由或交给已有受控资源约束；不新增调度器或修改默认 lanes。
任务完成是否解锁后继由当前路线的 Kernel 合同决定，不能照搬其他系统的
“评审完成即解锁”策略。独立 Impl/Verify 池、背压和候选冻结须按实际配置核对。

## 评审产物

记录 Epic→来源/AC→Task 的阅读映射、真实依赖理由、允许并行的前提、首个可运行
入口和首个独立反馈点。解释当前阻塞如何限制后继、后续问题何时重访、最终 Candidate
负责哪些跨任务用例。自动图诊断只提供事实；Critic 负责判断切片是否支持目标。
