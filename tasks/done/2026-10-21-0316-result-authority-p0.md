> 状态: done
> 实现: `6ef7593 fix: preserve dispatch authority and verify recovery effects`

# 第一批：结果权威与故障分流

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)。
授权：2026-10-21 用户明确要求 P0/P1 两批实施、提交并断点恢复验证。

## 验收

1. P0-1 排队→合同更新→派发→完成身份绑定 -> verify: 旧队列身份不覆盖 pinned operation；真正旧结果仍拒收；caller/E2E 回归。
2. P0-2 实现结果无损适配 -> verify: impl_self_check、authority、refs 保留，重复别名冲突显式拒收。
3. P0-3 故障分类 -> verify: 内核准入证据约束恢复路由，harness 故障不重拆产品任务；产品缺口仍可 replan。

## TDD / 证据

先记录失败回归，再实现；影响闭包、sentinel、mock E2E 与现场验证分别记录。

## 已执行验证（2026-10-21 UTC）

- 有效 RED：排队身份覆盖、自检丢失/冲突、错误形状异常、恢复回执误关闭、效果超时缺失、诊断失败误 dismissed、技能重复、故障引用未传递；修复后对应测试转绿。
- 最终影响闭包：`/tmp/zf-recovery-final-focused-v2.log`，19 个测试入口，530 passed，包含真实 runtime caller 和确定性 stale-contract/terminal mock E2E。
- `dev-verify.py run --base dev` 自动选择确定性全套：11,576 passed / 21 failed / 19 skipped。1 项旧模板断言已随行为修正并在最终闭包转绿；其余 20 项在未修改基线 8406826 的隔离 worktree 全部复现（20 failed / 44 passed），属于公开 checkout 缺失源文档/夹具/导出文件及主机版本、命令环境传感器，不属于本补丁回归，未篡改基线。
- 剩余自动 tiers 单独执行：serial 10 passed；premerge sentinel 51 passed；flow-smoke 10 passed。记录 `/tmp/zf-recovery-closure-other-tiers.json`。
- 诊断隔离配置使用本地 skills/Yoke 与当前 Python，`zf validate --path <isolated>/zf.yaml` exit 0；保留故障事件引用，日志 `/tmp/zf-recovery-environment-20261021T0339/validate-v4.log`。
- 以上是源码/机械行为验证；现场服务恢复、真实 provider 和 Q01–Q03 属于第三个任务，不能据此宣称全部产品验收通过。
