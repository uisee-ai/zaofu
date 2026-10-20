# 182-B06 P2 · 洛书 Plan 受控重规划与人工复审

> 状态: active
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

- 采用设计 182 推荐方向；公共契约修改先由实际 handoff 回归确定。
- 行为改动先补失败回归，再实现并记录聚焦验证；方法变更验证注入及示例。
- 完成状态以实际证据为准；产品 Plan 复审不授权产品 writer。
