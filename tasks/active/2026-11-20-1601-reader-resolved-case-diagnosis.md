# Reader 部分恢复后的新失败诊断

> 状态: active

来源：已授权的8×8真实验证。最新失败通过 checkpoint 去重后，又被 recovery case
的 resolved 状态过滤；旧 case 的成功回执只证明 product-scan 修好，不能消费之后
出现的、仍有 tech-scan 失败的输入。证据：evt-bf286469640a、evt-e42e38459358。

只允许同 Run、同 fanout 的较新内核 infra 失败在前一 child-rebuild 成功后重新进入
诊断；保持 case identity、历史、权限、人工等待、未完成 effect 和重试上限。
不直接执行重试，不复用旧失败，不把 Agent 文本当作内核新失败。

- 原问题→诊断→单 child 修复成功→新失败 -> verify: RM 实际投影选择当前失败，执行前准入一致。
- 新失败先于旧 child 成功回执到达 -> verify: 完成旧 effect 后仍可诊断新输入。
- 重放/异 Run/异 fanout/非内核/active effect/人工阻塞 -> verify: 不获得新的诊断准入。
- 全部真实 Reader 恢复 -> verify: 保留旧成功，观察扫描与后续阶段，Plan 后窗口另计。

验证：修正fixture API后TDD 2 failed/2 passed；修复后84项聚焦回归通过。
`dev-verify.py plan/run --base dev`：7,979 passed、7 skipped、12 warnings；
Web typecheck/unit通过，sentinel53通过，flow smoke10通过。
原账本只读完整投影已选中evt-bf286469640a并通过恢复准入；
真实旧技术Reader另经RM会话于15:59完成，不能冒充本补丁的自动恢复证明。
待计划审批自然停点部署，Plan后8×8仍未开始。
参见[实施记录](../../docs/impl/eight-by-eight-unattended-qualification.md)，
日志`reports/eight-qualification-20261120T122929Z/reader-case-reopen-*`。
