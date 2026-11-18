> 状态: done

# P1-03 生命周期与槽位一致性

用户于本轮明确批准按执行顺序实施与收尾。基线 dev 70295cc8，工作分支 wip/codex-20261118-unattended-progress。
承接 [8×8 真实验收](../active/2026-11-18-0001-eight-by-eight-recovery-closure.md)，不重复归档已完成修复。

来源：F08/F11/F13；HR-01/02/03；本轮批准执行方案。Spec/207–209 为本地主工作区来源，原文摘要保存在 reports/unattended-progress-20261118/baseline.json。

## 验收

角色重启/控制器重启/聚合重开/Impl→Verify→Impl -> verify: 持久与驻留状态收敛，不需人工重建投影；未知身份和活跃 writer 保持隔离。

## 约束与进度

沿现有 contract、operation、Work Record、RecoveryCase、Store/事件和受控动作，不新增并行状态机、不绕过权限/结果门禁、不恢复用户取消的 Planner/Run 时间及 token 限制。真实 Provider 为独立层，模型保持 Astra High / GPT-6.1 Terra High；Go 不纳入。
实施说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
当前：新增跨进程回归复现驻留blocked_human；housekeeping入口已接通驻留状态同步，保留来源与任务代际隔离，最终影响闭包已通过。


本批提交前验证：影响闭包6074 passed / 8 skipped，合并哨兵53 passed，流程冒烟10 passed；具体分层证据见实施说明。

实现提交：`ba57f731 fix: preserve reader progress and converge recovery ownership`。源码与确定性调用链验收完成；独立完整真实工作流资格由 P2 单独记录，不在本任务冒称通过。
