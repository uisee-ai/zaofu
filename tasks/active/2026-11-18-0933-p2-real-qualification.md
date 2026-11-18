> 状态: active

# P2 分层真实无人资格与 8×8

用户于本轮明确批准按执行顺序实施与收尾。基线 dev 70295cc8，工作分支 wip/codex-20261118-unattended-progress。
承接 [8×8 真实验收](2026-11-18-0001-eight-by-eight-recovery-closure.md)，不重复归档已完成修复。

来源：F18；209 §10–11；本轮批准执行方案。Spec/207–209 为本地主工作区来源，原文摘要保存在 reports/unattended-progress-20261118/baseline.json。

## 验收

确定性 caller 回归/影响闭包/sentinel/mock -> verify: 正反例通过；定向真实 Provider 后冻结新 PRD -> verify: 独立 Verify/Judge/goal 完成、临时恢复介入0；真实8 Impl+8 TaskVerify重叠独立统计，供给不足不冒称通过。

## 约束与进度

沿现有 contract、operation、Work Record、RecoveryCase、Store/事件和受控动作，不新增并行状态机、不绕过权限/结果门禁、不恢复用户取消的 Planner/Run 时间及 token 限制。真实 Provider 为独立层，模型保持 Astra High / GPT-6.1 Terra High；Go 不纳入。
实施说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
当前：真实Terra两会话受控接力已通过；Docker浏览器预检通过；新16任务项目已准备，完整无人8×8真实资格尚待执行。 实现提交及最终验证完成前保持 active。
