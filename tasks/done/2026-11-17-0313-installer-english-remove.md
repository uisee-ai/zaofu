> 状态: done
> 实现提交: `d65b44b3` — `feat: add safe runtime removal and English installer prompts`

# 安装器英文界面与卸载入口

来源：用户要求 install.sh 全英文、增加 remove 选择，并明确要求验证卸载生效。

- 英文交互 -> verify: 安装器及其配置/Herdr/卸载 helper 不含中文，原安装分支行为回归通过。
- 卸载选择 -> verify: 菜单与 `./install.sh remove --yes` 可达；预览/取消不产生删除，卸载不安装依赖。
- 安全范围 -> verify: 只停止当前源码安装拥有的服务，移除 `.venv`、`web/node_modules`、`web/dist`；保留源码、项目/Workspace 数据、凭据及共享依赖；纳管绑定、活跃工作、未知服务与数据路径重叠阻止删除。
- 真实生效 -> verify: claw02 隔离安装的进程/端口退出、安装文件消失，数据标记与共享工具保留；重复卸载幂等，重新安装可用；既有 8002 实例不受影响。

## 实施与验证

- 安装器及配置、Herdr、卸载 helper 全英文；菜单第四项和 `remove`/`--mode remove` 路由可用。
- 新启动服务记录 tmux 安装目录/Workspace 归属，卸载按 session ID 停止服务；真实节点发现 tmux 3.4 对 `set-option -t =name` 的兼容问题，已改为从 session 列表精确获取 ID，并加入回归覆盖。
- 卸载先检查托管绑定、项目实际配置的状态目录、活跃 runtime/Terminal、其他进程及路径重叠，再停止服务和删除三个生成目录。保留源码、用户数据和共享依赖；不调用 Controller 远程注销。
- `uv run python scripts/dev-verify.py plan --base dev` / `run --base dev` 均附带以下显式 `--tests`：`test_install_script.py`、`test_install_herdr.py`、`test_remove_install.py`、`test_start_webkanban_proxy.py`、`test_web_terminal_runtime.py`、`test_controller_workspace.py`、`test_workflow_runtime_start.py`、`test_workspace_projects.py`（路径均在 `tests/` 下）。自动闭包含 instruction contracts，共 **172 passed**。
- 最终类型标注整理后重跑安装/Herdr/卸载三个测试文件：**53 passed**；`ruff check`、`bash -n install.sh`、`git diff --check` 通过。验证范围为上述定向闭包与真实安装宿主测试，未运行全量 pytest 或真实模型调用。
- claw02 `/tmp/zf-remove-validation-20261117/source`：真实安装至 8003，创建带自定义状态目录的注册 Project；预览不停止/删除，执行卸载后会话消失、端口关闭、三个目录不存在，数据标记和令牌不变；重复卸载正常返回。重新安装后 API 健康，注册项目可打开、原令牌保留；随后再次实际卸载完成清理。
- 原 `/home/uisee/zaofu` 的 8002 服务 PID 与健康状态始终不变。已同步新版安装脚本和 helper；核对该旧服务的源码路径/启动命令后补充归属标记，`--skip-deps --no-start` 检查通过。
- 两轮模拟结束均发出 `simulation.done`；测试 tmux、端口、临时目录与 SSH 控制连接已清理。证明 JSON、验证脚本和不含访问令牌的安装/卸载日志保存在 `/home/min/workspace/.verification-archives/zaofu-remove-install-20261117/`；私有 Workspace 日志和凭据不归档。

范围：本分支提交交付，未合并 dev、未推送；主 checkout 的既有修改保持不变。
