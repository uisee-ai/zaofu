> 状态: active

# 安装入口与 standalone / Controller 选择

来源：用户要求参考 LoopForge/start.sh，生成 install.sh，并提供 claw02 用于安装验证。

- 安装模式 -> verify: 交互菜单和非交互参数支持 standalone、observed、cloud；cloud 不在本机启动实例。
- 首次部署 -> verify: 用户目录安装 uv/Node，锁定 Python/npm 依赖，复用 Workspace Dashboard launcher，HTTP 健康检查成功才报告就绪。
- 注册与鉴权 -> verify: 复用 WorkspaceConfig 和安装公钥、私有 code-file、enroll/sync/watch；未签发注册码返回 pending，不宣称上线。
- 重复执行与权限 -> verify: 不覆盖配置/模型登录/安装身份、不降级已有 managed、不重启已有进程、不打印访问令牌。
- 验证 -> verify: 脚本行为测试、配置合同与直接 CLI 调用方回归；claw02 用户目录 standalone 安装和 HTTP 验证单独记录。

云端创建、全面托管 takeover、机器自启动和 Provider CLI 登录继续使用各自现有入口；本脚本不实现第二套 IAM 或实例生命周期。

## 验证记录

- 候选基线 `54e4ecf8`；实现修改位于 `wip/codex-20261117-install` 独立 worktree。
- `uv run python scripts/dev-verify.py plan/run --base dev --tests tests/test_install_script.py --tests tests/test_start_webkanban_proxy.py --tests tests/test_controller_workspace.py --tests tests/test_cli_web_access.py --tests tests/test_controller_managed_kernel.py`：6 个文件共 79 passed。
- 重复启动日志保留修复后，`uv run pytest tests/test_install_script.py tests/test_start_webkanban_proxy.py -q --no-cov`：24 passed；`bash -n`、ruff 和 `git diff --check` 通过。
- claw02：Ubuntu 24.04 x64，`/home/uisee/zaofu` 为基线源码快照加本次安装文件；用户级 Node 22.23.5、锁定 Python 依赖、`npm ci`、Web typecheck/build/bundle budget 通过。
- `./install.sh --mode standalone --port 8002` 真实安装通过；`--skip-deps` 重启通过；重复启动非零退出，原 tmux pane PID、私有访问链接保留，Dashboard 和 Workspace API 仍为 200；未创建 Controller 注册目录。
- Docker `mcp/playwright:latest` + host networking，通过临时 SSH 转发读取真实节点，断言 HTTP 200、Bootstrap `STEP 1/4` / `PROVIDER` 可见、无 pageerror。镜像缺少默认 headless-shell 1222，改用同镜像已有且与 Playwright 匹配的 Chromium channel；未安装宿主浏览器。
- 浏览器初次探测与测试服务重启重叠，出现 fetch 失败；服务稳定后重跑正常。最终断言针对稳定运行的节点，不将该次重叠探测视为产品通过证据。
- 安装器的 observed 注册链路使用隔离命令替身测试，原 CLI 的凭据、注册、ACK 和 managed 边界使用现有 wire fixture 回归。本轮未连接真实 Controller（未提供地址/注册码），未调用模型、未验证 Provider 登录或真实 tokens 消耗。
- 远端保留用户安装和 `zf-web-8002` 服务；本机临时 SSH 转发及浏览器容器在验证后清理。既有远端 `tail` tmux 会话未改动。
