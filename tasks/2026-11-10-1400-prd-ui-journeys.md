# 两条 PRD 到 Plan Preview 的 UI 验证

> 状态: done — `a640c5c1` fix: declare full Plan review coverage and stabilize real UI tests

首批 UI/交接修复：`d7cabbef` fix: repair PRD UI handoffs and add provider journey verification。

按批准完成 fake 与真实 Provider 验证，真实场景采用冷启动，终点为待审批 Plan。
未批准实施；已有 runtime 热配置衔接等独立问题仍留在 proposed backlog。

- Kanban 独立多轮讨论生成持久 PRD → verify: 真实整轮 Playwright 和独立审计通过，无 Channel 替代。
- Channel Group 讨论、四角色签署、Owner confirm → verify: 当前 revision/ref/digest 交接到同一 Task/Run。
- 两条 UI 创建 Task/Workflow，PRD v5 且 plan_approval=true → verify: Raw YAML 可见，由 UI 启动生产入口。
- 真实 Scan/Planner/Critic 生成完整 Plan → verify: 同 Run、显式完整性声明、PRD/SDD/TDD/Decision Register 与来源覆盖，审批前只派发 reader。
- Plan Preview 全文/下载/刷新 → verify: 与本次产物逐字一致，刷新后实际 DOM 正文也一致；真实两条路径均有短时浏览器补验。
- fake 与真实结果分开 → verify: 原失败记录、补验、候选 Skill 更新和冷启动边界分别保存。

Channel 原整轮业务断言完成后 trace 导出 OOM，因此原 runner 保持 failed；独立审计
及同产物浏览器补验通过，不能把原轮写成绿色。修复增量取证后，真实 Kanban 独立轮
正常结束并导出完整 trace（34.2 分钟）。本次没有覆盖多页导航条件分支或产品实施。

验证：233 个影响模块 3623 passed / 8 skipped；串行层 10 passed；哨兵 53 passed；
mock flow smoke 10 passed；Web typecheck/unit/build 通过。完成临时运行清理与脱敏诊断归档。

详细身份、时间线、证据、原始失败和未修复问题见
[实测记录](../docs/records/2026-11-10-prd-ui-validation.md)。
