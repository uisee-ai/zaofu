# 运行前有界技术恢复授权

> 状态: done
> 实现提交 `e4991e9ac fix: close duplicate reader diagnosis and recheck recovery authority`

使用已有配置与受控入口把单路恢复、预批准宿主命令、控制面修复装载的明确界限贯穿到效果。

## 验收

- 启用与未启用两套配置，并试验陈旧来源、越界命令或修复、耗尽的预算 -> verify: 正例按冻结与检查点受理，旧配置保留，扩权及已有人闸仍走正式授权。

依据为 8×8 原 Run 的七票保留、技术恢复和最终交付复盘，以及 [199 恢复职责](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 验证结果

在受控入口核验准入固定的预算和权限、并发扣最后一个名额、宿主命令和延迟重启前撤销授权。启用 HMAC 和仅允许 fallback 的签名能力变化均有覆盖；独立真实 Run 在首派前记录了授权摘要。测试见 `tests/test_run_recovery_authorization.py`、`tests/test_source_repair_restart_actuator.py` 及 `tests/e2e/test_source_repair_checkpoint_mock_e2e.py`。
