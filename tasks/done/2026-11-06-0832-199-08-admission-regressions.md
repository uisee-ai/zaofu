# 199-08：有效配置、准入摘要、存储与预演

> 状态: done
> 实现: `8666bfdf fix: make recovery scoped, durable, and nonblocking`

来源：[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)；关联 R06/R10/R11；C01/C03/C05。

授权：2026-11-06 用户要求基于 199 在新 worktree 生成 backlog 并实施。
基线：`cff2a8d1`；范围仅 ZaoFu，真实 Provider/宿主部署验收独立记录。

## 实施

核对当前已修复入口；增加缺失边界回归；预演不产生副作用，权限变化执行前复核。

## 验收

- 配置/权限改变、部分写入、dry-run -> verify: 不接受失效事实或执行越权动作；未知事实不被伪造为成功。
- 修改通过实际 runtime/CLI/briefing 入口生效 -> verify: 调用方回归与影响闭包通过。

## 记录

- 实施：修复不同根文件 owner 误报；验证有效预算、审批身份、dry-run、Store ENOSPC 和必需 sidecar 原子性；修正归档签名测试固定日期。
- 对应验证：`tests/test_plan_digest.py tests/test_reader_budget_briefing.py tests/test_cli_restart.py tests/test_recovery_storage_faults.py tests/test_event_log_read_all_fold.py`；最终命令/结果见 [实施记录](../../docs/impl/199-worker-recovery-implementation.md)。
- 本轮关闭的是上述工作包验收，199尚存缺口不计作完成；针对性回归及调用方检查通过；广域验证无新增失败、仍有6项隔离复现的基线失败，详见实施记录；实现与验证证据已记录；真实 Provider/宿主及完整199剩余义务按覆盖矩阵单独跟踪。
