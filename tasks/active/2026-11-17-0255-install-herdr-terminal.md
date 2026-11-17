> 状态: active

# 安装脚本集成 Herdr 与 Web Terminal 验证

来源：用户确认 `install.sh 也要集成进去`，补齐首次安装遗漏的 Herdr。

- 首次安装 -> verify: 默认安装固定 Herdr 版本，核对 SHA256，协议检查成功后才发布到用户 bin。
- 重复安装 -> verify: 复用兼容版本；旧版、损坏或协议缺失明确失败且不覆盖；`--skip-deps` 不绕过能力检查。
- 入口闭包 -> verify: install.sh 调用真实 ZaoFu adapter 检查版本、schema、observe/control、rename，失败不启动 Dashboard。
- claw02 -> verify: 运行安装脚本，真实 Herdr + Web Terminal 创建、输入输出、重连、停止；使用隔离 Project，不调用模型，测试后清理。
- 说明与验证 -> verify: 更新安装手册/实现说明，运行安装器与终端影响闭包测试，记录真实宿主层边界。

## 验证记录

- 基线 `f531c4d9`，同一安装 worktree/分支增量；未修改 Kernel、Controller 授权或原有 Provider 登录。
- `uv run python scripts/dev-verify.py plan/run --base dev --tests tests/test_install_script.py --tests tests/test_install_herdr.py --tests tests/test_start_webkanban_proxy.py --tests tests/test_web_terminal_runtime.py --tests tests/test_web_terminal_routes.py --tests tests/test_web_terminal_usage.py --tests tests/test_terminal_reader_resume.py --tests tests/test_controller_workspace.py --tests tests/test_cli_web_access.py --tests tests/test_controller_managed_kernel.py`：11 文件，161 passed。安装器/终端子集另跑 105 passed；ruff、bash 语法和 diff whitespace 通过。
- claw02 执行 `./install.sh --mode standalone --no-start`：固定 Herdr 0.9.5 下载、SHA256、协议检查、Web typecheck/build/bundle budget 均通过。再次 `--skip-deps --no-start` 复用原二进制且校验通过。
- 真实安装 SHA256：`14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d`，路径 `/home/uisee/.local/bin/herdr`。
- 在节点 `/tmp/zf-herdr-install-20261117` 通过 `zf init --workspace-register` 建立隔离 Project/Workspace，临时 Web 8003 使用已安装的真实 Herdr；Provider 为独立回显 fixture，通过临时 PATH/Herdr shell 配置隔离，不读取模型凭据、不发送模型请求。
- Docker `mcp/playwright:latest`，Chromium channel，host networking + SSH 转发：未授权 POST 为 403，UI 创建 session 成功、Control 输入/PTY 回显、切换 Observe、刷新后同 session/history 恢复、Stop CLI 后状态 stopped，零 pageerror。此结果证明真实 Herdr/PTy/WebSocket/浏览器链路，不证明 Codex/Claude 登录、模型调用或真实 tokens 统计。
- 浏览器脚本最初使用 `networkidle` 等待刷新，在 Dashboard 持续连接下超时；改为 DOM/终端状态断言后通过，没有修改产品逻辑。Docker daemon 的 bind mount 与本机 `/tmp` 不共享，改用 `docker cp` 传递隔离测试文件；未安装宿主浏览器。
- 证据归档：`/home/min/workspace/.verification-archives/zaofu-herdr-install-20261117/`，包含 `result.json`、`terminal.png`、安装日志、浏览器/准备/清理脚本；不归档访问 token。
- 清理：Stop CLI 后关闭并删除测试专属 Herdr session，发出 `simulation.done`，结束临时 Web/容器，清理测试目录及 SSH 转发；节点原 `zf-web-8002`、`tail` 和用户安装保留，8002 仍返回 200。
