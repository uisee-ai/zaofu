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
当前：真实Terra两会话受控接力已通过；Docker浏览器预检通过。冻结源码33a343ed已合入dev ebaa20f5。16任务项目 workflow-46d731707cd1e693 在 Plan 批准后完成8/16项，实际Provider峰值 Impl=8、TaskVerify=6、同时8+8时长0。没有人工恢复介入，但出现宿主证据接力遗漏、并行诊断误失效及缺失RED明细的人工升级；已于2026-11-18 11:57 UTC按失败资格停止，保存原账本、代码与停止回执，不冒称业务Goal/无人8×8完成。

补充修复4df856f8（宿主证据接力）、83241455（诊断按原任务作用域）已完成测试。后续须用合并源码、预检后的可输出断言的测试入口，以及足够独立任务供给，重新冻结并完成真实资格。P2保持 active。

2026-11-18复核更新：32任务轮workflow-8cacd612404a641a账本32项done、浏览器通过，但TaskVerify峰值6、8+8重叠0，最终Judge/Goal未完成。批处理轮workflow-fd9e9212ab345b77停在第二次Critic，未批准。两轮的TMUX_TMPDIR目录缺失并回退默认server，product会话混有两轮pane；之前隔离成立的判断撤回，重叠区间不能作无人资格证据。监工已正常停止两轮并保留全部证据，记录为介入。

补充修复：显式私有目录fail-closed，Reader外部门禁保留原派发身份和故障预算，仅证明未发送时释放delivery claim。源码回归、8×8 mock与host隔离探针通过；当前真实Codex CLI额度门禁，P2仍active，待最新dev冻结新轮实际8+8、独立Judge/Goal/交付及0人工恢复完成。
