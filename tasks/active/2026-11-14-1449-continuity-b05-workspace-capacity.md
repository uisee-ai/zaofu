> 状态: active

# ZF-SPEC-001 切分、并发与环境复用

用户于2026-11-14明确授权基于 spec 在新 worktree 实施；覆盖 F10/F11。
基线：dev `0625d001`；spec SHA256：`f5a033210875e90fe059236974ca0c4ff2737b7b7fbbf6b7c62f6238bce8c52a`。
来源：`docs/spec/zaofu-continuity-and-handoff.spec.md`；207–209 是设计背景，非目标不自动扩大。

## 验收

- 计划反例审查；8 Impl/8 Verify 独立合格目标并发与2目标窄前沿；环境身份变化失效，准备不能破坏在用工作区。 -> verify: 真实 caller + 定向回归和异常负对照，逐项留原始命令/退出码；已有能力标记已验证保留，不伪称新实现。
- 影响面验证 -> verify: 按 AGENTS.md 执行后端/共享合同/mock E2E 或前端构建与 Docker 浏览器对应层，不用截图替代业务断言。
- 完成对账 -> verify: 记录代码位置、测试证据、资格限制及未完成项；未验收不标 done。

## 执行记录

本轮：setup 准备锁、失败失效及前后输入核验；新增 8 Impl/8 Verify/8+8 与 2 ready 的模拟子进程区间验证。

验证：contracts-final.log、eight-lane.log 与 eight-lane.xml。

2026-11-15 增量：物理工作区安装 mutex、setup/bootstrap 互斥、bootstrap 成功回执、
失败残留拒绝伪就绪、禁止失败后借用其他 checkout venv；`project.setup_inputs` 绑定显式工具/数据身份文件。
818 passed（7 项既有别名 allowlist skipped），281 项调用方回归，sentinel 53、flow smoke 10。
配置与限制见 [环境准备实现](../../docs/impl/continuity-environment-preparation.md)。

未关闭：未声明工具/数据变化的资格，实际执行者持有环境锁与共享依赖目标使用者；真实 DAG/Provider 并发资格。

对账报告：[207–209 / Spec 覆盖](../../reports/continuity-handoff-20261114-review.md)。
实现入口：[实现记录](../../docs/impl/continuity-handoff-spec-001.md)。
首批实现 commit：`2313bf61 feat: harden task continuity and preserve handoff evidence`；未 merge / push / 部署。
