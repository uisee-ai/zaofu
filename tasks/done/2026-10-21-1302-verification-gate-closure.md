> 状态: done — `ec3be8f feat: enforce verification gates and incident-specific diagnosis`

# 验证时序、并行执行与恢复证据闭环

授权：2026-10-21 用户「好，执行下一批优先验证」。基线 `4009aa7`。
设计：[184](../../docs/design/184-verification-stage-order-and-evidence-gates.md)。

## 本批 backlog

- [x] VG-01 阶段验收时序：复用现有任务流水线及更新 Planner/Critic 方法 -> verify:
  Impl 完成而独立 Verify 缺失/失败时下游不派发，合法通过并集成后解锁；重放不重复。
- [x] VG-02 候选命令阶段：装配门不执行尚未就绪的下游命令，并保留正式消费者 -> verify:
  实际 CandidateRebuilder 回归证明后续命令延后、当前命令仍执行、缺消费者不放行。
- [x] VG-03 Impl 收据闭包：当前本片 AC 的命令必须有绑定收据 -> verify:
  task_verify-owned 命令空收据/漏收据/失败收据均拒绝，完整证据通过，未来 AC 保持待验。
- [x] VG-04 八槽独立验证与汇总：验证隔离资源、实际派发和已准入结果交接 -> verify:
  确定性 caller E2E 覆盖八槽容量、并发任务、同一候选及不完整分片不能关闭产品目标。
  明确 Task Verify 与 Candidate Verify 的覆盖和未验证现场范围。
- [x] VG-05 故障专用诊断：为证据交接/身份/阶段时序提供有界复现 -> verify:
  诊断输入到实际执行器闭环，负例能复现，修复后同命令通过；通用演练不能替代原故障证据。

## 验证与发布

先保存 RED，再实施并记录 GREEN。运行 dev-verify 计划和影响闭包、sentinel、相关
mock E2E。旧产品 Run、Q01–Q03 与真实 provider 八路运行独立记录；缺外部事实不得
伪造完成。实现完成后提交、记录 hash 并归档；本批不自动推送。

## 本批验证记录

完成范围为 harness 补丁、方法与确定性/本地集成验证；不宣称原 Run、Q01–Q03、
真实八路 provider 或 Autoresearch 原故障诊断实验完成。对应源代码与断言见设计 184。
VG-01 增补了验证过程中复现的集成来源漏洞：最新 Verify 结果必须已准入、可读取、
摘要/身份/目标匹配，旧成功不能覆盖新失败。

- RED: `/tmp/zf-verification-gates-red.log`（3 failed/1 passed）；
  `/tmp/zf-candidate-context-red.log`（1 failed/1 passed）；
  `/tmp/zf-integration-evidence-red.log`（6 failed/1 passed）；
  `/tmp/zf-candidate-classification-red.log`（1 failed/1 passed）；
  `/tmp/zf-incident-scenario-red.log`（故障场景默认路由/解析反例）。
- GREEN: 32、73、60 项递进 focused；新增 mock E2E 7 项；最终重点复验 121 项；
  候选全延后/无消费者/本片命令的补充 4 项通过。各数字有重叠，不能累加为独立覆盖。
- `uv run python scripts/dev-verify.py plan --base dev --json`：初选 229 个文件；
  实际命令保存在 `/tmp/zf-verification-closure-command.json`，4 workers、900 秒预算。
  首轮 3340 passed / 8 skipped / 2 failed；默认场景旧断言已修，107/121 项复验通过。
  剩余 fixture 缺失已在干净基线 4009aa7 复现，非本批引入，未更改无关测试/补造来源。
- `uv run python scripts/dev-verify.py run --base dev --skip-tier deterministic --keep-going
  --receipt /tmp/zf-verification-dev-verify-receipt.json`：serial 10、sentinel 51、Flow mock
  E2E 10 通过。跳过的是文件数阈值触发的全套替代命令，受影响测试集合已单独执行。
- `tests/test_plan_readiness_skills.py` 及 skill 三份副本字节一致性通过。
- 洛书 worktree `/home/min/.codex/worktrees/loshu-integration-context-20261021/loshu`：
  `7714f44 feat: bind assembly receipts to kernel integration context`；
  `b5a671e chore: keep integration receipts outside source changes`。
  node context 测试 13 项、typecheck 通过；三条 canonical API-RULES 命令实际执行
  25 rules + 7 UI + 4 contract 断言，通过 Kernel helper 生成上下文与独立目录的
  integration receipts。该提交尚未接入原批准任务/候选 refs。

基线 fixture 修复的触发条件：恢复被遗漏的 canonical bundle 来源文件并核对 source index；
这是后续单独范围，不阻止已复现且通过回归的本批补丁交付。真实现场续跑按设计 184 的
新合同、产品补丁和外部证据条件执行。
