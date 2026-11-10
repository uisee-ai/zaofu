# 两条 PRD 到 Plan Preview 的 UI 验证

> 状态: active

按用户批准先 fake provider、再真实 provider 验证。隔离工作区继承上一轮验证 Skill，
不改变真实 dev 项目、既有 runtime 或未提交文件。

- Kanban 多轮讨论生成持久 PRD → verify: 无 Channel 替代，来源能绑定后续 Workflow。
- Channel Group 讨论、Finalize、Owner confirm → verify: 当前 revision/ref/digest 被交接。
- 两条路径 UI 创建 Task/Workflow，采用预先启用 plan_approval 的 PRD v5 → verify: 生效快照与 UI 一致。
- 真实 Kernel 消费 fake/real provider 产物 → verify: 完整 Plan 与本 Run 绑定，未审批不派发实施。
- Plan Preview 阅读/刷新/下载 → verify: 全文与本次产物一致，不使用预置成功状态。
- fake 与真实层分别记录证据与未完成阶段，不把模型替身结果当作真实模型验收。

测试仅准备空白项目、配置和依赖。业务操作从 UI 发起；只读 API/账本/产物负责核验。
停止自己创建的 Web、runtime、tmux 和 provider，保留报告与失败诊断。

已复现并修复：conversation → multi_lens 的 Start 按钮被旧状态禁用；
Channel 新 Task 的 prepare_workflow 丢失可信 target_root 且提示仍要求旧 workflow_plan；
综合 Plan 的显式完整性声明未传给 task_map.ready。

验证进展：两条 fake UI 全链路通过；Kanban 交互式澄清卡片再回归通过。
定向闭包 1127 passed / 8 skipped；Web typecheck/unit/build 通过；sentinel 53 passed。
真实 Codex Kanban 到达 PRD/Task/YAML，启动被 consumer_config_stale 拒绝；
真实 Channel 完成问题归并和交叉审查、进入综合，测试阶段预算先耗尽。
已修正半程等待预算，但真实完整 Plan 尚未验收，任务保持 active。
配置衔接问题另存 proposed backlog，未绕过校验。

结果和证据索引：[实测记录](../docs/records/2026-11-10-prd-ui-validation.md)。
