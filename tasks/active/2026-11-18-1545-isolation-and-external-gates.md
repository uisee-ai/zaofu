> 状态: active

# 真实8×8复盘：隔离失效与外部门禁错误消耗派发预算

用户已批准监工评估、复盘修复、mock先行后真实Provider验证。本项承接P2，不表示P2通过。

来源：workflow-8cacd612404a641a 的 evt-ca71b3de01c7（3/3）、evt-835011819824 等Provider交互失败；workflow-fd9e9212ab345b77。两个fixture的TMUX_TMPDIR目录缺失，默认socket中product会话混有两项目pane，原“独立隔离”判断无效。原始报告纠正但不覆盖原账本。

## 验收

- 显式隔离目录缺失/无效 -> verify: 在任何tmux命令执行前拒绝，不能回退默认server；有效目录维持兼容。
- Reader初次/重试遭遇quota/login/trust等门禁 -> verify: 保持operation，等待不生成child.failed，不发送输入，不消耗TaskAttempt故障重试预算；解除后恰好派发一次。
- 派发竞争窗口 -> verify: 只在transport明确证明未触碰输入时释放本次delivery claim；sent、过期身份及不明发送结果不能回滚。
- mock+影响闭包+sentinel -> verify: 通过后才能开始新真实provider轮；host隔离探针独立记录，不能代替真实8+8。

## 边界

不自动确认Provider弹窗、不改sandbox/凭据、不提高重试预算、不直接写运行状态；代码在新worktree实施，冻结实验源码不修改。当前真实CLI显示usage_limit_reached；恢复前只执行mock/host探针。旧两轮正常停止并保留全部代码与证据，不能冒称无人验收。

## 实施验证

RED：8 failed、2 passed；初次绿色3项fixture未开启blocking协议，修正fixture后专项60 passed。最终影响闭包4845 passed、7 skipped；串行4 passed；哨兵53 passed；流程mock10 passed。8×8 mock 8 passed，混合16进程8+8重叠0.202秒（mock人为同步，不算真实provider）；host私有双socket隔离1 passed。详细日志保存在reports/external-gates-20261118。

本任务仅收敛已复现的源代码缺陷；真实无人资格仍由P2独立验收。真实provider因usage_limit_reached未新开，不能把本任务完成等同P2通过。

第二轮调用链复盘追加：Task Pipeline的Impl/Verify也会把Provider激活/投递门禁累计为派发失败冷却。交付入口和激活入口分别RED 2 failed；修复收敛到实际reconcile边界，四种组合验证门禁解除后原operation继续。Reader等待中重建Orchestrator同样通过。最终专项64 passed（host另跑）。

最终合并候选验证（含Task Pipeline激活/投递补充）：专项64 passed；影响闭包5020 passed、8 skipped；串行4 passed；哨兵53 passed；流程mock10 passed；8×8 mock8 passed；host双socket隔离1 passed。证据：reports/external-gates-20261118/validation-summary.json；真实provider未重开，P2保持active。
