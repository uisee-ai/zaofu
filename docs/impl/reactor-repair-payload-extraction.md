# 自修复派发分类归属与 reactor 结构门

任务：[2026-11-07-1120](../../tasks/active/2026-11-07-1120-reactor-repair-payload-extraction.md)。

2026-11-07 main 的结构门实测失败：`orchestrator_reactor.py` 6981 行超过冻结 cap 6977（1 failed / 15 passed / 1 skipped）。隔离分支缺少 main 两项既有兼容 hunk，单独在分支跑绿不能证明 main 修复完成。

本片将 `_autoresearch_failure_class` 原函数体移入既有 `repair_authorization.py`，名称为 `repair_dispatch_failure_class`。原 confirmed-candidate handler 从该模块导入并调用它；派发 payload、causation 和授权流程均不变。两个函数体改名后的 AST 完全相同。现有授权层的 `_failure_class` 会归一化并识别更多类别，不能直接替换派发侧字段映射；`configured_repair_mode` 也多出 strip，故本片不顺便合并这两组不同语义。

reactor 净减少 17 行：隔离分支 6959，精确补丁应用至 main 后应为 6964，低于 6977。main 的 suspended 生命周期兼容及 `interrupt_completion_operation` hook 均保留；不调整冻结 baseline，也不通过删空行压缩。`git apply --check /tmp/zf-supervision-20261107/reactor-structure-source.patch` 在 main 原脏工作区通过，未实际应用或修改 main。

验证：

- main 原始结构 RED：1 failed / 15 passed / 1 skipped。
- 抽取前新增真实 caller 参数化边界与授权原测试：24 passed，固定旧 payload 行为。
- 抽取后 focused：`test_supervisor_autoresearch_repair_bridge.py`、`test_repair_authorization.py`、`test_autoresearch_triggers.py`、`test_repair_dispatch.py`、`test_autoresearch_self_repair.py`、`test_self_repair_process.py`，均在 `tests/` 下，**77 passed / 1.64s**。
- Sentinel：**50 passed / 1 skipped**。
- 自动 contract 重跑：**50 passed / 1 skipped**；deterministic flow smoke：**10 passed / 1.87s**。
- `dev-verify plan --base dev` 包含前批未合并工作并选 788 文件；本片 `--base HEAD` 因 reactor 映射选 222 文件，均升级全 deterministic。行为保持的小范围抽取以以上 focused 闭包替代 broad deterministic/serial；`dev-verify run --base HEAD --skip-tier deterministic --skip-tier serial` 执行 contract/mock_e2e，不声称全 pytest 或真实 provider 验证。

日志：`/tmp/zf-supervision-20261107/reactor-structure-{red,before,focused,sentinel,auto,plan-dev}.log`。首个 focused 命令含不存在的测试路径，零用例执行；改用上述实际存在的路径后取得 77 pass。

本片不改变运行态、不提交、不重载；由主会话审核精确补丁、提交并在 main 同步后独立运行结构门确认。
