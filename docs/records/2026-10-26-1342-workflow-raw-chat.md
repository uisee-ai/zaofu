# Workflow Raw 阻塞候选的 Agent Chat 修复

Playwright 在 8002 的 yy 项目复现：Raw 已有 YAML，但 Edit with Agent 数量为 0。请求处于 ready，候选为 blocked，未绑定可启动 Proposal；前后端仅允许 proposed 进入审阅，造成有问题的候选无法通过 Chat 修正。之前的模拟浏览器回归只覆盖正常 Proposal。

修复让未启动的阻塞候选也可审阅和修改：以 Request ID、修订、Requirement digest、候选 digest 校验身份；修改仍走已有候选编译、事件、不可变工件和审批流程。已有 Run、过期候选和陈旧配置仍被拒绝。修改后仍有阻塞时保持 ready，刷新可从受控修改的事件结果恢复新版 YAML；重新准备同一修订的候选会使旧编辑失效。Raw 默认选择配置字段，避免落到不可修改的来源需求字段。

## 浏览器实测

- 8002 已重启。yy 实际页面可打开 Edit with Agent，context 返回 200，config.1.lanes 可选且修改按钮可用；没有提交对 yy 的配置修改。[现场检查](2026-10-26-workflow-raw-chat/live-check.log)。
- 隔离项目使用真实 HTTP、Docker Playwright 和实际安装的 Codex adapter；仅初始及保留的阻塞条件是显式测试夹具，Provider 没有替身。
- Raw → Edit with Agent → lanes 1 改为 2 → 真实 Codex 返回局部 Diff → 应用 → 刷新后 YAML 仍含 lanes: 2，修订变为 3。候选仍为 blocked，请求仍为 ready，未提交 Run，项目 canonical zf.yaml 仍为 lanes: 1。
- [真实调用日志](2026-10-26-workflow-raw-chat/real-chat.log)、[Provider 元数据](2026-10-26-workflow-raw-chat/provider-proof.json)、[Diff 截图](2026-10-26-workflow-raw-chat/real-diff.png)、[刷新后的 YAML](2026-10-26-workflow-raw-chat/real-yaml-after.png)。
- 可复现脚本：[隔离服务](2026-10-26-workflow-raw-chat/server.py)、[浏览器步骤](2026-10-26-workflow-raw-chat/real-chat.mjs)。服务要求新的 /tmp/zf-* 根目录、8003、仓库 PYTHONPATH；浏览器使用 mcp/playwright:latest 和 host networking。脚本中的测试 token 仅用于隔离服务。

## 验证与边界

- Preview Review、阻塞候选、Web Proposal、Proposal 编译、SDLC 图及 intake semantic handoff mock E2E：100 passed。[日志](2026-10-26-workflow-raw-chat/closure-tests.log)。覆盖阻塞修改、刷新、重放、过期修订拒绝、失败后恢复与后续候选覆盖旧编辑。补充剩余 preflight STOP 下继续局部修订且保持禁止启动的用例后，阻塞路径单独复测 5 passed；[复测日志](2026-10-26-workflow-raw-chat/blocked-final.log)。
- Sentinel gate：50 passed，1 skipped；前端构建/typecheck、图预算与相关模型单测通过。[Gate](2026-10-26-workflow-raw-chat/gate.log)、[构建](2026-10-26-workflow-raw-chat/build.log)、[验证计划](2026-10-26-workflow-raw-chat/verification-plan.txt)。未运行完整 pytest。
- 测试状态通过 simulation.done 记账后清理，8003 停止。用户 8002 继续运行，8001 未重启。代码在独立工作树，未 commit/merge/push。
- 本次支持既有字段级局部修改；不把运行中的 YAML 改为可热编辑，也不自动消除原有编译阻塞。
