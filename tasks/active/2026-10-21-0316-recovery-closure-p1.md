> 状态: active

# 第二批：恢复效果与诊断闭环

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)。
授权：2026-10-21 用户明确要求 P0/P1 两批实施、提交并断点恢复验证。

## 验收

1. P1-1 恢复效果验收 -> verify: 请求发出不关闭 case，匹配实际效果才关闭，失败/无关成功/重放均覆盖。
2. P1-2 诊断环境 -> verify: 隔离来源无重复、固定源码版本、validate 通过且故障输入有引用。
3. P1-3 诊断失败生命周期 -> verify: 环境失败保留未确认候选和原因，不误标 dismissed，不无证据生成 repair。
4. P1-4 终态维修/续接 -> verify: 保留历史终态事件，诊断独立进行，受控新代次/续接与状态投影正确。

## TDD / 证据

先记录失败回归，再实现；影响闭包、sentinel、mock E2E 与现场验证分别记录。

## 已执行验证（2026-10-21 UTC）

- 有效 RED：排队身份覆盖、自检丢失/冲突、错误形状异常、恢复回执误关闭、效果超时缺失、诊断失败误 dismissed、技能重复、故障引用未传递；修复后对应测试转绿。
- 最终影响闭包：`/tmp/zf-recovery-final-focused-v2.log`，19 个测试入口，530 passed，包含真实 runtime caller 和确定性 stale-contract/terminal mock E2E。
- `dev-verify.py run --base dev` 自动选择确定性全套：11,576 passed / 21 failed / 19 skipped。1 项旧模板断言已随行为修正并在最终闭包转绿；其余 20 项在未修改基线 8406826 的隔离 worktree 全部复现（20 failed / 44 passed），属于公开 checkout 缺失源文档/夹具/导出文件及主机版本、命令环境传感器，不属于本补丁回归，未篡改基线。
- 剩余自动 tiers 单独执行：serial 10 passed；premerge sentinel 51 passed；flow-smoke 10 passed。记录 `/tmp/zf-recovery-closure-other-tiers.json`。
- 诊断隔离配置使用本地 skills/Yoke 与当前 Python，`zf validate --path <isolated>/zf.yaml` exit 0；保留故障事件引用，日志 `/tmp/zf-recovery-environment-20261021T0339/validate-v4.log`。
- 以上是源码/机械行为验证；现场服务恢复、真实 provider 和 Q01–Q03 属于第三个任务，不能据此宣称全部产品验收通过。

P1-4 经当前 HEAD 验证已有实现：`stage-replan-new-generation` 校验 escalation 和 approval_ref，同一 Run 新阶段代次幂等续接、重开父任务；复用现有机制，不再叠加第二套状态机。效果超时写入新 pending 事件，历史无截止时间事件不被追溯判超时。
