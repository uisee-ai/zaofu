# 使用 install.sh 安装 ZaoFu

根目录 [install.sh](../../../install.sh) 是 **source checkout** 安装入口，参考 LoopForge 的
“依赖检查 → 配置 → 启动 → 就绪检查”体验。ZaoFu 沿用本机 Provider CLI、tmux 和 Workspace
Dashboard，不要求 Docker；此入口不安装云端 Controller 或 Keycloak。

## 首次安装

在 Linux/macOS 的普通用户下执行。系统需有 bash、Python 3、curl、Git、tmux 和 tar；
Ubuntu 缺失时可先执行 `sudo apt install python3 curl git tmux tar`。

```bash
git clone https://github.com/uisee-ai/zaofu
cd zaofu
./install.sh
```

安装器的提示、菜单、错误和辅助脚本输出使用英文。菜单含四种选择：

| 选择 | 安装结果 | 是否需要 Controller |
| --- | --- | --- |
| standalone（默认） | 本机完整工作台，之后从 Web 添加/打开 Project | 不需要账号、Keycloak 或注册码 |
| observed | 本机工作台 + 外部实例注册 + 授权 Project 的心跳、Session、tokens 等观测 | 需要已批准的外部 Workspace 和注册码 |
| cloud | 提示前往门户注册、申请云端实例，本机不安装 | 由 Controller 审核和创建实例 |
| remove | 停止当前源码安装拥有的服务，移除依赖环境和构建产物 | 不自动注销 Controller 身份 |

脚本会安装缺少的 uv、Herdr 到 `~/.local/bin`，缺少或低于 22 的 Node.js 到
`~/.local/share/zaofu/node`，使用 `uv sync --locked`、`npm ci` 和生产 Web 构建。
Node 下载使用官方 SHA256 清单验证；uv 遵循[官方安装机制](https://docs.astral.sh/uv/getting-started/installation/)，
不修改 shell profile。缺少 Python 3.11+ 时由 uv 管理项目解释器。不会调用 sudo、修改系统包、
安装开机服务或复制其他机器的模型登录。后续直接调用 uv 时可将 `~/.local/bin` 加入 PATH。

Web Terminal 的 Herdr 随安装一起准备：首次下载固定
[Herdr 0.9.5](https://github.com/herdrdev/herdr/releases/tag/v0.9.5)，核对固定的官方 SHA256，
然后复用 ZaoFu adapter 检查版本、API schema、observe/control 和 Tab 重命名。
下载、校验或协议检查失败时，不发布新二进制、不启动 Dashboard。已有兼容版本会复用；
旧版、损坏或不兼容版本需先由用户修复，脚本不会覆盖已有 Herdr 或修改 Provider hooks。
`--skip-deps` 仍执行协议检查；若缺失 Herdr，去掉该参数重新安装。安装输出包含实际版本、
路径和二进制 SHA256，便于记录部署证据。此检查不等于真实模型登录或终端端到端验收。

```bash
# 自动化安装：必须显式选择模式，或用 --yes 选择默认 standalone
./install.sh --mode standalone
./install.sh --yes --no-start

# 使用已有依赖和构建，启动在另一空闲端口
./install.sh --mode standalone --skip-deps --port 8002
```

默认监听 `127.0.0.1:8001`，远程机器可用 SSH 转发：

```bash
ssh -L 8002:127.0.0.1:8002 user@your-node
```

需要局域网直连时显式传 `--host 0.0.0.0`。有写权限的 URL 保存在
`<workspace-home>/install-web-<port>.log`（0600），不会写入终端安装输出；只在可信设备查看。
安装成功的含义是 Herdr 能力检查通过、Web 进程存活且 Workspace API 就绪。开始真实 Agent 工作前仍需用户自行安装、
登录 Codex / Claude Code，完成 Web Bootstrap，并运行对应的 provider doctor。

## 将本地节点注册到 Controller

先在 Controller 完成登录、准入和 **external Workspace** 审批。安装器不申请管理员权限，
不把邮箱/用户密码传给节点，不代替后台审核。获取 Controller HTTPS origin、Keycloak realm
issuer 和已批准 Workspace ID 后执行：

```bash
./install.sh --mode observed \
  --endpoint https://controller.example.com \
  --issuer https://identity.example.com/realms/zaofu \
  --workspace-id YOUR_APPROVED_WORKSPACE_ID \
  --project YOUR_LOCAL_REGISTRY_PROJECT_ID
```

`--project` 可以重复；省略表示空项目授权，只采集实例心跳，不扫描所有项目。
`--model-mode self_managed_cli` 是默认模型接入方式，也可明确选择 `self_managed_api`
或 `managed_cpa`；模型来源不改变 observed 管理权限，也不会自动注入 CPA 凭据。

首次执行生成 `<workspace-home>/controller/zf.yaml` 和安装公钥。将公钥及明确的项目范围提交给
Controller 所有者签发注册码。未给注册码时退出码为 **3**，明确显示待注册，不启动进程，
也不声称节点上线。将注册码存入当前用户拥有的 0600 普通文件后继续：

```bash
# /private/enrollment-code 由用户安全保存，不在命令行粘贴其内容
chmod 600 /private/enrollment-code
./install.sh --mode observed --skip-deps --code-file /private/enrollment-code
```

若使用自定义 `--workspace-home` 或 `--workspace-config /path/to/zf.yaml`，每次都传相同路径。
配置只使用现有 WorkspaceConfig，不建立第二份安装模式配置。已有配置保持原样；改变目标、
计量模式或项目集合的冲突参数会被拒绝。注册码的消费、私钥、凭据和 ACK 由现有
`zf controller installation-key/enroll/status/sync` 处理；同步成功后启动 `sync --watch`。
注册失败、凭据失效、Controller 不可达或未确认同步均返回非零，不自动改为 standalone。

observed 只增加观测，不获得执行限额等全面托管权限。全面托管仍需服务端授权与本地显式
`takeover`，详见 [managed 生命周期](../../impl/204-controller-managed-runtime.md)。已有 managed
绑定、云端机器身份或受保护的系统配置会阻止本脚本启动，损坏绑定也不会被当成 standalone。

## 重复执行、停止和更新

- 重复安装保留 Workspace、配置、安装身份和 Provider 登录。不会拉取新源码、清理数据或覆盖配置。
- 默认不替换已有 tmux 会话或占用端口的服务；启动冲突会返回非零。`--no-start` 适合只更新依赖和构建。
- Web 使用 `zf-web-<port>`，observed Connector 使用 `zf-controller-<port>`；均是用户 tmux 会话，
  不提供开机自启动。停止采集不等于注销 Controller 身份。
- 要更换模式或撤销纳管，沿用 `zf controller drain/unenroll`，不要删除绑定文件来绕过权限。
- 本机断网后的 observed/managed 行为由现有 Connector 决定，安装器不实现新恢复策略。

```bash
# 只停止本次对应端口的服务；不删除数据或注销身份
tools/start-webkanban.sh --port 8002 --session zf-web-8002 --stop
tmux kill-session -t '=zf-controller-8002'  # 仅 observed 存在此会话
```

诊断使用 `<workspace-home>/install-web-<port>.log`、`webkanban-<port>.log` 和
`controller-<port>.log`。这些文件可能包含访问链接，应按私有文件处理。CLI/配置详细合同见
[部署 Connector](../../impl/203-controller-workspace-connector.md)。

## 卸载 ZaoFu

菜单选择 **4) Remove ZaoFu**，或在要卸载的源码目录中执行：

```bash
./install.sh remove                         # 先显示计划，交互确认默认 No
./install.sh remove --yes                   # 自动化：确认执行卸载
./install.sh --mode remove --yes            # 等价形式
```

自定义 Workspace 需传原来的 `--workspace-home PATH`。卸载会停止该源码目录启动并标记归属
的 Dashboard/Connector tmux 服务（包括不同端口），删除该目录的 `.venv`、`web/node_modules`
和 `web/dist`。源码、Project、Workspace、访问令牌、Provider 登录和共享的 uv、Node、Herdr
全部保留；不会删除 checkout 或执行系统包卸载。重新运行 `./install.sh` 可以恢复运行环境。

卸载不下载/安装依赖，重复执行不会删除保留数据。非交互缺少 `--yes` 时仅输出计划并返回
非零；交互选择 No 时没有停止/删除副作用。正常完成才输出 `ZaoFu runtime removed`。

为保护仍在使用的安装，发现活跃 Project runtime、未 Stop CLI 的 Web Terminal、其他使用该
checkout 的进程、符号链接目标或项目/Workspace 数据落入待删目录时会拒绝删除。状态目录按
Project `zf.yaml` 解析，不假设 `.zf`。旧启动器创建且没有归属标记的服务应先明确停止，再卸载；
脚本不会仅凭相似的 session 名称终止进程。

managed/cloud 绑定仍需先完成既有 Controller 生命周期；本地卸载不绕过纳管边界。observed
卸载只停止本地采集，保留注册凭据和待发送数据，不会自动撤销 Controller 中的身份。
