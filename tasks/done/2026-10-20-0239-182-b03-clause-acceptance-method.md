# 182-B03 P2 · 条款级验收责任与规格充分性审查

> 状态: done；实现：`865863d feat: bind planning roles to execution readiness methods`。
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B03。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：F02、F08；所有者：Planner/Critic 方法；依赖：与 B02 对齐完成语义。
- 审查每个混合 AC 的子条款 → verify: 盘点决定、真实行为、条件扩展分别有明确生产者、验证方法和最早可验证时点。
- 去除按任务名字整体豁免的审查方法 → verify: “工程盘点 AC 内夹入真实事务要求”的反例在语义评审中被指出。
- 重建行为条款到实际测试边界的映射 → verify: 例如同 key 不同请求冲突、回滚及保存边界能定位到服务/数据库测试责任，而非仅 inventory 检查。
- 保留非 UI 前置工作的合理验证 → verify: 不强迫仓库盘点、架构决定等生成无意义浏览器旅程。
- 自动检查只验证 ID、引用、归属及依赖闭包；语义充分性由 Critic 给出可引用判断。
- 方法变更应包含一个正确示例和一个混合职责反例；验证技能注入/产物契约，不用关键词出现次数冒充语义通过。

## 实施记录

- 方法实现：`865863d feat: bind planning roles to execution readiness methods`。
- 新 `zf-plan-execution-readiness` 明确条款的行为生产者、可观察结果、owner/tier、命令、
  最早验证时点；包含正确例与“盘点 AC 夹入真实事务”的反例。Planner/Critic 依赖已物化，
  canonical 与两份分发镜像一致，实际 Planner 读取有工具事件与文件 hash 证据。
- 真实独立 Critic 曾拒绝 R-L2659 整段静态盘点：历史 PGlite 局限与并发/恢复义务不能混验。
  返工拆分该段及同类条款为 15 个显式子条款，分别指定真实服务/隔离 PG 的测试责任。
- 工程盘点保留静态验证；同 key 异请求、库存回滚、设备竞争和迁移恢复没有用 inventory 绿灯替代。
- 新旧映射复审还纠正 E 场景的首条 AC 兜底与跨矩阵漏同步；最终正式版本证据见
  [综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。

## 完成证据

- 正式案例：`e7d1cd1 docs: record verified Loshu replanning and coverage review`。
- 最终 Critic `evt-bc99c0e49fbc` 完成，聚合 `evt-56357575dfc5` 建议 approve；
  新 Plan `evt-3f6b832282a3` 保持 pending。15 个混合子条款的行为 owner、runtime/e2e 层级
  与阶段责任已独立复核，26 个 E 的跨矩阵 AC 集合一致。
- 方法及相关注入测试 83 passed、1 skip；结构检查保留 2,043 来源记录，零未映射旧业务条款。
  结构统计不是语义通过证据，实际拒绝/返工和最终 Critic 结论见综合评审。

- 详细评估：[综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。
