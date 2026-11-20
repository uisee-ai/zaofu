# Reader 语义检查点与外部等待核验

> 状态: done — 3b6ae7ab feat: add reader checkpoints and preserve recovery pause fences

来源：ZF-SPEC-004 UE-07/09；用户授权继续剩余实现，明确本轮不做新版真实 8×8。
沿用当前工作分支，上一批 merge-ready 交接失效，完成后重新审计。

## 本批验收

- 共享候选 Reader 发布和接续语义记录 -> verify: 实际 CLI publish/show/recover、
  会话凭据轮换、确定性中断恢复；不要求独占锚点 Task。
- 使用受限 stdin 保存检查点 -> verify: 不扩张只读 Reader 文件写权限、不改原结果草稿；
  已评估条款、证据引用、未决项、报告草稿可回读，正式 submit 能继续。
- 保持角色、Run、operation、双 attempt、lease、fanout、输入摘要绑定 ->
  verify: 错角色、过期租约、换代、终态、损坏、旧记录及跨 Reader 读取拒绝。
- 接入恢复简报并保留结果出口 -> verify: 检查点不完成 Task、不代替必读、
  不自动获得额外付费 turn；语义方法沿现有 skill。
- 核实外部等待已具备和缺少的范围 -> verify: 发送前等待、重启、恢复后原
  identity 接续、暂停/未知发送保护的 mock；额度耗尽后的主动探测单独对账，
  不用已有发送前等待冒称全链完成。
- 影响闭包 -> verify: dev-verify plan/run、sentinel、相关 mock；不运行真实 Provider。

## 记录

RED：共享 Reader publish 返回需要 current Task assignee；外部额度等待解除、重启
后的 dispatch.paused/run.paused 两种场景错误发送。后续已修通，Run cancelled 保持
禁止续派。新 Reader 记录只提供同 operation 接力，不授权跨责任继承。

定向联验 111 passed（Reader 续跑/纠错、外部等待、Run 准入）。
新记录与原 Task 工作记录联验 65 passed；按需改造后的输入/完整产品 mock 联验
62 passed；最终局部联验 43 passed。不同运行有重叠，不累加为独立测试数。

完整影响闭包首次为 2 failed、7196 passed、10 skipped：新增详细方法导致 Judge
简报超出 12 KiB。修复为短入口 + recover 按需返回，未修改断言或提升上限。
额外复现并修复无 Plan package 的旧候选跳过 currentness，以及暂停重复 deferral。
冻结候选 mock 验证检查点后正式失败报告仍可独立准入，不以此冒称产品 passed。

最终验证（工作区执行，PATH 包含共享 .venv，PYTHONPATH=src）：

```bash
python scripts/dev-verify.py plan --base dev
python scripts/dev-verify.py run --base dev --workers 8 \
  --receipt /tmp/zf-reader-continuity-final-verification.json
```

- planner 选择 571 个测试文件，broad_python=false；这是影响闭包，非 full pytest。
- deterministic：7231 passed、10 skipped；premerge sentinel：53 passed；
  flow smoke：10 passed。receipt status=passed。
- 日志：/tmp/zf-reader-continuity-final-verification.log。
- 两个改动 skill 的 canonical/.codex/.claude 副本逐字一致；git diff --check 通过。
- 未启动真实 Provider、8×8、浏览器或宿主服务；未恢复旧 workflow、未 merge/push。

完整 Spec 的其他剩余项按 docs/impl/unattended-progress-recovery.md 对账；
本批不包含跨 operation/责任人的记录继承、运行中额度耗尽的主动付费探测、
未确认的自主源码推广或零监工资格。它们未标完成。
