> 状态: active

# P0-04 完整合同的自动采证

用户于本轮明确批准按执行顺序实施与收尾。基线 dev 70295cc8，工作分支 wip/codex-20261118-unattended-progress。
承接 [8×8 真实验收](2026-11-18-0001-eight-by-eight-recovery-closure.md)，不重复归档已完成修复。

来源：F12；209 §8；本轮批准执行方案。Spec/207–209 为本地主工作区来源，原文摘要保存在 reports/unattended-progress-20261118/baseline.json。

## 验收

冻结命令/环境/授权/媒体前置核对 -> verify: 授权内完整命令自动采证并交接，不需人工补采；缺授权、错目标、旧收据或不稳定源码拒绝。

## 约束与进度

沿现有 contract、operation、Work Record、RecoveryCase、Store/事件和受控动作，不新增并行状态机、不绕过权限/结果门禁、不恢复用户取消的 Planner/Run 时间及 token 限制。真实 Provider 为独立层，模型保持 Astra High / GPT-6.1 Terra High；Go 不纳入。
实施说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
当前：已修复部分白名单采证提前重开；缺少/不合法证据保持等待。新fixture全部19条冻结命令均预授权，执行上限100。 实现提交及最终验证完成前保持 active。


本批提交前验证：影响闭包6074 passed / 8 skipped，合并哨兵53 passed，流程冒烟10 passed；具体分层证据见实施说明。
