# 并行 Candidate 下部分实现的后继任务续接

> 状态: active

用户授权持续修复 LS-v1 阻塞。evt-d3a6fcd1f93a：FMS04 后继已准入，但部分提交1b3ffa8与已前进的Candidate881e分叉，工作区拒绝。仅让Reader看见部分代码（第18轮）不代表后继可实施及集成。

- [x] 复现工作区与集成对已授权部分实现的分叉拒绝 -> verify: 保留普通未知分叉拒绝的回归。
- [x] 新辅助模块核验同Run、显式supersedes/base绑定、原Impl已准入失败、原base是Candidate祖先、部分提交完整在后继scope内 -> verify: 篡改/无授权/跨Run/越界/不相关历史拒绝。
- [x] 工作区沿用确切部分提交；集成保持现有Candidate，以原cherry-pick/Verify/smoke/CAS路径合入部分提交和补修 -> verify: 候选同时保留并发成果、原部分代码和新修复；不提前发布失败代码。
- [ ] 受控重载后真实FMS04开始实施；记录源缺陷、自动恢复缺口与验收边界。

验证：dev-verify 影响闭包 526 passed / 7 skipped，premerge sentinel 53 passed，flow mock smoke 10 passed；另行 TaskStore/Sidecar/WorkflowOperation/部分失败续接契约 49 passed。新增冲突反例先失败（暴露原 scoped checkout 覆盖并发修改），改为受 scope 校验的真正 cherry-pick 后通过。入口文件超 1000 行检查曾失败，分叉证明/拒绝移至新模块后通过。未运行完整 pytest 或真实 provider E2E。
