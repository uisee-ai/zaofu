# 并行 Candidate 下部分实现的后继任务续接

> 状态: done — 8b607139 `fix: preserve partial task work across concurrent candidate advances`

用户授权持续修复 LS-v1 阻塞。evt-d3a6fcd1f93a：FMS04 后继已准入，但部分提交1b3ffa8与已前进的Candidate881e分叉，工作区拒绝。仅让Reader看见部分代码（第18轮）不代表后继可实施及集成。

- [x] 复现工作区与集成对已授权部分实现的分叉拒绝 -> verify: 保留普通未知分叉拒绝的回归。
- [x] 新辅助模块核验同Run、显式supersedes/base绑定、原Impl已准入失败、原base是Candidate祖先、部分提交完整在后继scope内 -> verify: 篡改/无授权/跨Run/越界/不相关历史拒绝。
- [x] 工作区沿用确切部分提交；集成保持现有Candidate，以原cherry-pick/Verify/smoke/CAS路径合入部分提交和补修 -> verify: 候选同时保留并发成果、原部分代码和新修复；不提前发布失败代码。
- [x] 受控重载后真实FMS04开始实施；记录源缺陷、自动恢复缺口与验收边界。

验证：dev-verify 影响闭包 526 passed / 7 skipped，premerge sentinel 53 passed，flow mock smoke 10 passed；另行 TaskStore/Sidecar/WorkflowOperation/部分失败续接契约 49 passed。新增冲突反例先失败（暴露原 scoped checkout 覆盖并发修改），改为受 scope 校验的真正 cherry-pick 后通过。入口文件超 1000 行检查曾失败，分叉证明/拒绝移至新模块后通过。未运行完整 pytest 或真实 provider E2E。

现场验收：LS-v1 FMS-04-GAP-BUSINESS-01 于 2026-11-07 10:03:53 UTC 派发 Impl（evt-ea9739bbd80f），base_commit/target_ref 均为 1b3ffa8aeaaffa053785eadf28532271536344a0。Round20 已完成增量准入与审批（evt-d9ddc55cd432 / evt-e9c5b1e923c1），Round21消除并发Candidate分叉的工作区拒绝。全量map保留35项及此前TODO变更。控制面加载c8d5b93e，回执72f78eb20653472198f618c2211419ba。正在执行的TODO Verify与FMS21 Reader pane/PID均保持；已结算FMS21旧Impl pane被正常回收。真实任务尚未完成，不将派发成功冒充产品验收或真实候选集成通过；后者当前由确定性集成/冲突回归证明，继续现场监工。
