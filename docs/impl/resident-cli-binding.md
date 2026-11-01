# 常驻 Run Manager 的 CLI 绑定

常驻 Run Manager 的初始与重绑定 briefing 使用 `zf_cli_cmd()` 解析启动时的
`ZF_CLI_CMD`，状态查询和观察事件提交使用同一入口。多个 checkout 共存时，
不依赖 provider shell 中的裸 `zf` 指向当前 harness。未设置绑定时保留 `zf` 默认值。

命令中的状态目录、actor 和 JSON payload 分别按 shell 参数引用；绑定本身可以
包含解释器、脚本路径和启动参数。此修复只改变后续生成的提示，不覆写已派发输入，
也不改变 Run Manager 的确定性裁决与受控动作边界。

实现：`src/zf/runtime/run_manager_resident.py`。验证：
`tests/test_run_manager_resident_cli.py` 以旧 CLI 位于 PATH、当前 CLI 带路径和参数
的隔离环境实际执行生成的 status/emit 命令，覆盖普通与含引号的状态目录；
`tests/test_run_manager_resident.py` 覆盖启动、保留、重绑定与恢复调用者。
