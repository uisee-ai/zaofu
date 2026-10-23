> 状态: active

# 184 · 真实验证发现的三项边界修复

来源：用户批准第三步失败驱动改进；从本批恢复任务拆出可独立验收的修复，不缩减原交付要求。

轻量 blocking 配置 -> verify: Issue/PRD 及参数别名在 Provider 前拒绝，原失败配置复测；正常 off/shadow 和标准控制器仍通过。

未知交付重试 -> verify: 原始失败日志重放的受控入口返回 409，不新建 synthesis；正常耗尽重试仍可用，投影不误报可生成。

Kanban 真实取消 -> verify: 公共 turn 映射实际 attempt，先持久化取消再停止进程；错误线程拒绝；真实 Codex 进程已停止，重建应用不复活且无 Task 副作用。

证据和测试见 `docs/impl/184-live-validation.md`。原生 tmux 接收确认和完整产品交付仍在原 active 任务中，不属于本项已验证结论。
