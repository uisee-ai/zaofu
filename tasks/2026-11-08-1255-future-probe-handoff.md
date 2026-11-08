# 后续验收程序的当前交付责任

> 状态: done；`f50d9bf7 fix: review downstream verification program handoffs`；已合并 dev `860fdc1c`，LS技能源同步提交 `b63e85b`。

LS-v1八类candidate_verify命令仍无条件失败，前序任务仅运行local而通过。运行环境未就绪不等于当前owner可以不交程序；但不能倒过来要求当前Task提前完成所有后续E2E。证据：LS项目docs/records/2026-11-08-candidate-e2e-command-followups.json、candidate-e2e-additional-stubs.json和candidate-e2e-program-recovery/。

- 在done-contract与Verify方法按producer_task_id和写域区分程序交付与实际执行 -> verify: 当前owner有可执行实现、输入约定、preflight；未来owner只记录依赖，无依据不误阻断。
- 源和两份分发同步 -> verify: 字节一致，现有role注入/skill解析/指令生成测试通过。
- 只改方法 -> verify: 不增runtime字符串扫描、事件类型、必读正文或新控制面配置。
- 记录行为验证边界 -> verify: 本轮方法和生成器检查；自然后续Verify是否减少漏检需继续观察，不冒称盲测已证明提升。

实现说明：[后续程序交付](../docs/impl/future-verification-program-handoff.md)。验证33 +41通过/1跳过；frontmatter未改、三份一致。通用技能校验器不支持既有项目扩展；仓库解析/物化检查通过，尚无真实Provider方法效果对照。

部署核对：LS两源由resolver解析到新摘要，19份已物化快照全等保留。后续自然Verify派发是否加载新摘要仍在监工，未以方法部署冒称语义成效。dev合并后33项生成/解析/派发回归通过。
