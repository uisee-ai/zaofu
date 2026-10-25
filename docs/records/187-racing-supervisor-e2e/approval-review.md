# 赛车小游戏：待你批准的 Plan

> 状态: active。Plan `evt-adf3807d914c` 已通过独立 Critic，等待用户批准；尚未实现或通过产品验收。

[打开 8002 Workflow](http://10.0.169.194:8002/?page=workflows&project=racing-project-33598481&request=prepare-TASK-5F44B4-c32763f31115843f)，点击 **PRD / Plan Preview · pending**；可切换审批概览、模块规格、验收场景、完整原文。用户也可在当前对话批准此具体版本，由监工代为操作审批按钮。

## 本次批准的内容

桌面 Chromium 的单人俯视赛车：方向键/WASD 驾驶、赛道与障碍碰撞、合法前进距离计分、完整90秒有效比赛时间、开始/暂停/继续/结束、结束面板“重新比赛”。具体数值、状态和防刷分规则以完整 PRD 为准。

- [完整 PRD](pending-prd.md)：从此 Plan 绑定的 execution_spec.shared.prd 原样导出。
- [完整 Plan 原文](pending-plan.md)：与 accepted_plan 绑定摘要逐字一致。
- [SDD 接口约定](pending-interfaces.md)：从绑定的 execution_spec.shared.interfaces 原样导出。
- [结构化任务与16项验收](pending-plan-task_map.json)、[执行规格和具名场景](pending-plan-execution_spec.json)。

| 任务 | 目标 | 实现角色 | 波次 | 前置任务 |
| --- | --- | --- | --- | --- |
| RACE-FOUNDATION | 建立可启动的工程骨架与共享契约 | prd-dev-lane-0 | 1 | 无 |
| RACE-DRIVE | 可驾驶并可暂停重开的90秒比赛 | prd-dev-lane-0 | 2 | RACE-FOUNDATION |
| RACE-TRACK | 可见赛道、障碍与不能刷分的碰撞路径 | prd-dev-lane-1 | 2 | RACE-FOUNDATION |
| RACE-ASSEMBLY | 装配正式赛车入口与完整交付验收 | prd-dev-lane-2 | 3 | RACE-FOUNDATION, RACE-DRIVE, RACE-TRACK |

配置容量为3个Impl lane和3个独立Verify lane。依赖允许DRIVE与TRACK同时实现，预计最大实现并行宽度为2；FOUNDATION与ASSEMBLY分别在前后串行。各片完成自身检查，候选集成后再触发独立多lane Verify、Discovery和Judge，由Kernel根据证据接纳终态。真实重叠时间与最终游戏验收仍待执行，配置不等于并发证明。

## 已确认的局部修订

1. 结束面板按钮文案统一为“重新比赛”，保持原有重开行为；已同步到PRD、AC-A02和浏览器验收。
2. 增加专属自检AC-A07，绑定VC-ASSEMBLY：正式入口装配真实比赛与赛道依赖；卸载后按钮、输入和更新循环停止作用且重新挂载不重复响应；统一验证runner的任一阶段失败、超时或缺证据均向调用者返回非零并清理本次资源。

该自检覆盖入口依赖注入、卸载/重新挂载、失败/超时传播及缺失证据拒绝。保留原有15项验收，合计16项；完整90秒浏览器验证和独立最终验证没有提前算作本片通过。原Plan正文保留局部编辑边界，新增细节在本版绑定Task Map、执行规格和界面验收场景中查看。

## 审批与运行条件

此批准只授权按本版开始实施与验收，不代表任务、测试或交付已成功。预算上限沿用当前未配置状态，风险说明保留；Worker环境与治理证据包仍需后续真实核验。浏览器测试使用Docker，游戏服务使用8004以上端口，8001/8002不用于游戏服务。

- Plan ID：`evt-adf3807d914c`。
- 正文 SHA-256：`c12ce3ef9dd16ba97c60259ba3ee75dca12d19d8397f67f5127f6546f9d2cc28`。
- 当前：`pending`；`plan.approved=false`；`impl_dispatched=false`。
- [真实浏览器检查](pending-plan-browser-proof.json)：全文与绑定摘要一致、AC-A07可读、审批按钮可用且未点击、桌面/手机不横向溢出。

按照用户明确要求“plan preview需要approve”，监工等待对此版本的显式批准后才继续实现。8002和隔离运行状态保留。
