# 182-B06 P2 · 洛书 Plan 受控重规划与人工复审

> 状态: done；实现：`e7d1cd1 docs: record verified Loshu replanning and coverage review`。
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B06。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：F01–F08 的案例落地；所有者：洛书 Planner/Critic/operator。
- 依赖：B02–B05 的选定方案；最终网页复审依赖 B01。
- 先重新读取当前规范、Plan、事件与决定 → verify: 记录当前引用，旧问题已解决则不重复修改。
- 按受影响阶段澄清 Q 项并重排任务 → verify: 原页面、条款、E 场景和条件范围保留，所有修改有理由及旧新任务/AC 对照。
- 同步 Plan、task_map、source_index 和相关矩阵 → verify: 正式生产者一致，命令身份/路径/证据归属闭合，依赖可行且无写入重叠。
- 通过既有受控工作流提交新 revision → verify: Kernel 生成新绑定，旧事件和 Package 保留，不手改 `.zf-loshu`。
- 人工打开完整 Preview 复审 → verify: 审查者可定位全文、覆盖、条件、风险与变更；未批准前没有产品 writer、业务或数据库操作。
- 本项是规划修订，不因生成新计划而宣称产品已验收。

## 实施记录


## 完成证据

- 使用独立项目 `/home/min/workspace/loshu-plan182`、配置状态 `.zf-plan182` 和正式
  `loshu-plan182-review` flow；扫描、规划、独立 Critic、受控拒绝及既有 admission_replan
  恢复链均有事件。原项目、旧事件与旧 Package 保留。
- Planner/Critic 实际消费 24 个配套文件和 8 张图；保留 62 页面、6 表单、80 ASCII、
  26 E、15 Q，正式旧 26 任务 / 82 AC 全部映射。Q 按阶段保留 open，不伪造答案。
- 最终 Plan `evt-3f6b832282a3`，审批请求 `evt-e9b5532a5842`；独立 Critic 聚合建议 approve，
  人工审批仍 pending。正式绑定、内容哈希、领域覆盖及 TDD/SDD 结论见综合评审。
- Docker Chromium 桌面/手机读取 222,432 字节正文、12 附件并核对 hash；正文末行实际进入
  可视区，0 JS 错误、0 审批写请求。事件无 plan.approved，项目无 app/，没有产品实现或数据库操作。

- 详细评估：[综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。
