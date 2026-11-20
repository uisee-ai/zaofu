# 8×8 恢复：保留 RM 诊断所有权，只汇总当前失败

> 状态: done

实现：`e064e4cd fix: preserve reader recovery ownership and current failure evidence`。

来源：用户授权恢复并执行8×8验证，遇到实际问题修复后继续。真实 Run
`workflow-055abbd5196f67c6` 的 product-scan 在15:25 UTC恢复成功后，剩余 tech-scan
旧失败重新触发人工升级；汇总还带入已经恢复的 product-scan 历史失败。

## 实施范围

1. 有已配置 resident RM 且事件注册了 RM pending_action 消费责任时，基础设施失败
   保持现有诊断路由，不额外制造人工作为前置条件。未配置/未注册仍保留原升级；
   语义输入不变与重规划上限不变。不直接派发修复或自动批准。
2. 汇总补充原始失败正文时，只选当前 failed child 的当前派发/当前事件；历史保留在
   append-only账本，不作为当前缺口或恢复路线的证据。

## 验收

- Reader基础设施失败 -> verify: 实际reactor处理后RM投影仍有诊断动作，没有凭空新增人工门。
- RM未启用/无注册责任/语义输入不变 -> verify: 原人工门保留。
- 两个Reader各有旧失败，重试后一成一败 -> verify: 只有当前失败进入findings；旧账本不变。
- Reader精确重试 -> verify: 既有Run/attempt/暂停/终态门禁回归通过；不改业务验收。
- 部署修复后原Run继续 -> verify: 剩余Reader出现新的合法派发，最终扫描成功另记真实证据。

实现说明：[8×8 验证记录](../../docs/impl/eight-by-eight-unattended-qualification.md)。
日志：`reports/eight-qualification-20261120T122929Z/reader-owner-routing-*.log`。

初始回归重现两项实际缺陷；另一个语义反例fixture缺少correlation绑定，补齐后验证通过。
聚焦回归106 passed。`dev-verify.py plan/run --base dev` 的影响闭包7,551 passed、
7 skipped；Web typecheck/unit通过，sentinel 53 passed，flow smoke 10 passed。
这不是全量或真实业务通过；真实账本只读重放已确认只有tech-scan进入当前findings。
修复部署后RM受控重试技术Reader：evt-cce4d0b49708（15:57:18 UTC）；
15:59:56 evt-a4699c013be6完成，evt-c30b81674dc7扫描成功。
产品扫描既有成功与所有失败历史保留。后续新诊断被case_resolved抑制属于独立
修复项，不能用本次RM会话成功宣称整个无人恢复机制已通过。
