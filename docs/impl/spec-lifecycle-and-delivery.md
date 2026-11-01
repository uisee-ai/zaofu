# 规格生命周期、修订审查与可复现交付

本批承接[Plan 执行一致性](plan-execution-readiness.md)，复用当前角色、冻结
Plan Package、controlled replan、独立 Verify/discovery 和 Thin Judge。
语义方法不新增运行态枚举、Task schema、调度门或配置字段。

## Judge 后的规格审计

当前 `EventReactorMixin._emit_spec_promote_decision` 不更新规格，也没有接收并准入
规格更新产物的协议。旧实现只凭 `contract.spec_ref` 和非空 `verification_tiers`
就发布 `spec.promote.completed`；验证层级是计划声明，Judge 通过也不证明规格更新。

回调现在保留 `spec.promote.skipped`：没有引用时记录 `no_spec_ref`，有引用但没有
实际规格更新准入路径时记录 `promotion_not_verified`。事件绑定源 Judge 的
causation/correlation，合法 Task 完成继续由原完成门负责。自报 `spec_promoted`
或文件存在不能改变结果。

本批没有增加自动同步服务或待处理队列。`promotion_not_verified` 不是已安排稍后
回写；确需修改规格时按原授权范围生产新工件，经现有评审/受控修订生效。
历史 completed 事件和注册名保留以便重放，但旧事件本身不能证明实际更新。
未来若接入同步完成声明，须先提供真实生产者和同任务/目标/版本的准入收据，
覆盖实际 caller；不要重新用布尔参数、文件存在或代码通过代替该证据。

## 角色方法与实际交接

| 方法 | 消费角色 | 产物归属 |
| --- | --- | --- |
| [规格修订审查](../../skills/zf-plan-execution-readiness/references/spec-revision-review.md) | Planner/Critic 的既有 readiness 依赖 | 当前 Plan 人读正文、既有评审工件/端口 |
| [交付规划](../../skills/zf-plan-execution-readiness/references/delivery-validation.md) | Planner/Critic | 执行规格、正式矩阵及交付任务 |
| [实施交接](../../skills/zf-harness-done-contract/SKILL.md) | Impl/Fix 的既有依赖 | 已授权交付文件和自检证据 |
| [产品收口](../../skills/zf-verify-rescan-replan/references/product-convergence.md) | 现有 discovery/rescan | 既有 coverage/gap/产品验收报告 |

规格修订记录授权、旧新条款/合同映射、受影响消费者、旧结论的复用依据或重审范围、
未读/未验范围和剩余责任。纯编辑不自动扩大审查；需求变动不能靠旧任务 done 通过。
摘要写入同版 Plan，既有 Preview 全文即可阅读，不增加第二份审批或前端状态。

产品收口增加 missing/partial/contradicts/unrequested 四类语义说明；不向固定的
runtime `gap_category` 或 verdict 塞入新枚举。原始 mandatory 范围保留，必要工程
支撑可以由批准设计解释。环境不可用/未验不自动判产品缺失，也不能判通过。
有证据的必修缺口通过既有 gap/replan 处理，保持有效已完成工作与历史身份。

交付指南按实际产品范围记录准备、配置和测试身份取得、初始化、启动/就绪、业务
旅程与命令/收据、资源隔离及清理；恢复验证以项目合同为准。Compose 只在适用或
被要求时采用。规划为 NOT_RUN，实施自检与独立重现分开，缺环境时保留未验义务。
本批提供通用方法，不给在线 ls/FMS 伪造部署结果或直接修订已冻结计划。

## 生效与验证

`skills/` 是源，两个 Provider 分发副本同步。新的派发/角色物化应从新版本加载；
在途会话不会自动重读更新。部署本批代码后，正常重载使用该代码的 runtime 消费者；
不用为了文档方法重启既有 workflow 或重置其状态。已批准 Plan 如需补内容，仍走
受控修订及适用审批。

回归：`tests/test_spec_promote.py` 从 Judge handler 经真实回调/事件日志覆盖
“正常完成但不虚报规格同步”；`tests/test_wave5_batch.py` 保留旧跳过原因与事件兼容。
`tests/test_plan_readiness_skills.py` 验证真实角色物化与分发闭包，证明可读而非语义正确。
隔离独立方法评估及实际验证结果见[验证记录](../records/2026-10-31-sdd-lifecycle-validation.md)。
