# 成本停更监测不得把已结束Reader当作活动派发

> 状态: active

来源：8×8真实验证evt-cad0f729f660在15:44 UTC对已于15:25成功的product-scan告警。
RM正确识别其attempt结束，根因是tick只排除整个Run终态，没有排除具体child终态。
本修复仅使用精确Run/fanout/child/dispatch的内核终态排除该Reader；不得用其他
派发的成功关闭当前派发，也不能因最新child完成而漏掉更早的活动child。

- 最近Reader完成/失败 -> verify: 不触发其usage停更告警。
- 另一child仍活动 -> verify: 仍能告警，不被已完成的较新child遮蔽。
- 旧派发终态、异Run终态、worker自报 -> verify: 不关闭当前内核派发。
- 既有冷却、启动grace、Run作用域 -> verify: 保持现有回归。

验证：TDD先3 failed / 4 passed；修复后43项聚焦回归通过。
`dev-verify.py plan/run --base dev --tests tests/test_cost_blackout_reader_terminal.py`：
8,773 passed、7 skipped、12 warnings；Web typecheck/unit、53 sentinel、10 flow smoke通过。
真实告警时间点的只读重放不再告警，证据见
`reports/eight-qualification-20261120T122929Z/cost-reader-terminal-real-preview.json`。
运行中控制面仍加载e36dd01b，本修复将在Plan自然检查点重启后生效；不把历史重放
写成已经在长期进程部署。参见[实施记录](../../docs/impl/eight-by-eight-unattended-qualification.md)。
