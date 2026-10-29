> 状态: active

# 重启后运行时 PATH 与候选集成恢复

用户授权监工记录并修复无法自愈的问题，完成后从原断点继续。

## 已复现故障

datal / workflow-0ea133e63454375e 的 P17-SCHEDULE-R1 与 P01C 独立 Verify 通过后，分别在 2026-10-29 02:11、02:25 UTC 阻塞于 rolling smoke。P01C bootstrap.log 报 Python 3.11 required；watcher 使用 3.11.14，但 PATH 裸 python3 解析为系统 3.12.3。

根因：workflow launcher 设置 ZF_CLI_CMD，set_default_zf_cli_cmd 的提前返回跳过 PATH 初始化。Run Manager/Autoresearch 没有解除本次集成阻塞，仍呈现旧 P01B 的人工诊断。修复仅覆盖可复现的 PATH 缺陷；旧诊断相关性与人工实物验收另外跟踪，不能以此放宽产品门禁。

证据：项目 state_dir 下 artifacts/rolling-smoke-receipts/6c733b9dc0da837e205c99614c397bf330a36123101d16f12de7710b9331a75f.json、a190abdaf39281db3b47ed80e26f2edaf73a2665ac0cf58d472e9d4d76f40ade.json。原 blocked operation 以受控 task-integration 恢复，失败历史保留。

## 验收

- 显式 CLI 的启动入口也初始化 PATH -> verify: 子进程 python3 解析到 runtime；原代码回归失败，修复后通过。
- 保留显式 CLI 与幂等初始化 -> verify: 重复调用不重复前置目录、不改变配置命令。
- 检查真实调用边界 -> verify: CLI start、workflow runtime launcher、候选集成恢复、确定性 mock E2E 与 premerge sentinel。
- 部署后从断点继续 -> verify: 新 watcher 环境、原 Run 身份、task-integration 恢复事件及新 smoke 收据；不伪造完成状态。

## 验证记录

原代码 tests/test_cli_command.py：1 failed, 4 passed，新测试实际返回 system 而非 runtime。

定向回归：test_cli_command、test_cli_start、test_workflow_runtime_start、test_task_pipeline_integration_retry、test_task_pipeline_v4_runtime_noise_mock_e2e 共 74 passed；test_cli_restart、test_cli_web、test_candidate_incremental、test_candidates 共 74 passed。premerge sentinel：50 passed，1 skipped。

dev-verify plan 把共享 CLI 入口扩展到 141 个文件并建议全量 deterministic；依据 AGENTS.md 的小范围变更禁止无依据全量测试规则，本次执行上述直接入口、候选与恢复闭包，而未执行全量 suite。
