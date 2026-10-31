# SDD 规格生命周期与可复现交付

> 状态: active

授权来源：用户「好，按照建议的，生成backlog 实施吧」。本批按前轮建议的四项实施，
使用独立 worktree；基于当前 dev 接入上一轮已提交的 SDD readiness 成果。
语义方法沿用 Planner/Critic/discovery 与现有产物，运行时修正错误完成声明。

## SDD-LIFE-01：规格同步事件如实记录

- 当前 judge 回调只凭 spec_ref 和 verification_tiers 就声明 spec.promote.completed。
- 重新复现当前代码；Judge 通过不证明规格已修改，也不授权把实现反写成需求。
- 现有回调没有规格更新生产者/收据，保留审计 skipped 及明确原因；不虚构自动同步能力。
- 验收：真实 terminal caller + EventWriter/EventLog 回归 -> verify: 文件存在/不存在、
  层级声明或自报 promotion 都不能变成同步完成；合法任务完成不受影响，来源事件可追溯。

## SDD-LIFE-02：规格修订与审查影响记录

- 补当前有效规格、不可变历史、授权变更、影响追踪、旧结论复用/重审方法。
- 在既有 Plan/Critic 工件及人读正文中交付，不新增 Task schema、状态或语义自动门禁。
- 验收：Planner/Critic 真正物化新参考；独立前向样例 -> verify: 行为修订能识别关联
  消费者和失效结论，纯编辑不扩大范围，旧批准工件保持不变。

## SDD-LIFE-03：产品收口分类

- discovery/rescan 区分 missing、partial、contradicts、unrequested；必要工程支撑须解释。
- 核对已完成任务在固定最终候选上的行为；保留未验和环境阻塞，不用计数证明收口。
- 验收：实际 discovery skill bundle 可读；独立前向样例 -> verify: 全部 task 标完成
  仍识别必需旅程缺口，生成有界 gap 建议而不改需求或重启无关已完成任务。

## SDD-LIFE-04：可复现交付指南

- Planner 规划入口/配置/初始化/测试身份/旅程/清理与适用恢复步骤、责任和验证时点。
- Impl 交付精确候选指南与收据，独立验证者从干净环境复验；按项目适用性使用 Compose。
- 验收：相关角色真实可读方法；独立样例 -> verify: 无隐藏人工步骤、无真实凭据，
  规划期不伪造执行证据，无外部环境时如实记录限制，普通库任务不被强制容器化。

## 验证与收尾

- 聚焦运行时、直接 caller、Event/Store、技能物化与 provenance 回归；执行 dev-verify
  影响闭包、premerge sentinel 与相关 deterministic mock E2E。
- 新方法进行隔离独立前向评估，记录输入、输出及局限；不操作在线 ls/FMS。
- 实现提交后记录 hash/title，再归档任务并提交。仅本分支提交，不合 dev、不 push。
- 当前本批不增加新的自动规格更新服务、合同 registry 或 lane 调度机制。

## 已完成验收证据

- SDD-LIFE-01：先复现 5 failed；修复后真实 Judge caller、事件/Store、终态和
  自动交付影响闭包 376 passed, 1 skipped。有效 Task 完成不再虚报规格更新。
- SDD-LIFE-02～04：角色依赖/参考文件物化与 provider 分发通过；两个隔离合成
  样例形成版本影响表、16 次 CLI 收据、有界缺口建议和完整运行指南。
- 独立评估反馈三处歧义，补充说明后独立复核确认解决；规范结论复用不冒充 current
  执行通过，指南作者自检不冒充独立验收。输入未改，测试数据已清理。
- 门禁 51 passed；flow smoke 10 passed；Web typecheck/unit passed；最终技能/
  文档闭包 61 passed, 1 skipped。没有运行全套 pytest、真实 provider 或在线部署。
- 通用 skill-creator validator 不支持仓库既有扩展 frontmatter；Kernel 元数据和
  分发测试通过，保留该兼容性限制，不删除仓库支持的字段。
- 详细命令、边界和完整前向评估存档见
  [验证记录](../../docs/records/2026-10-31-sdd-lifecycle-validation.md)。
