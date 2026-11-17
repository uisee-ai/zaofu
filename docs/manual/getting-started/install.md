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

菜单含三种选择：

| 选择 | 安装结果 | 是否需要 Controller |
| --- | --- | --- |
| standalone（默认） | 本机完整工作台，之后从 Web 添加/打开 Project | 不需要账号、Keycloak 或注册码 |
| observed | 本机工作台 + 外部实例注册 + 授权 Project 的心跳、Session、tokens 等观测 | 需要已批准的外部 Workspace 和注册码 |
| cloud | 提示前往门户注册、申请云端实例，本机不安装 | 由 Controller 审核和创建实例 |

脚本会安装缺少的 uv 到 `~/.local/bin`、缺少或低于 22 的 Node.js 到
`~/.local/share/zaofu/node`，使用 `uv sync --locked`、`npm ci` 和生产 Web 构建。
Node 下载使用官方 SHA256 清单验证；uv 遵循[官方安装机制](https://docs.astral.sh/uv/getting-started/installation/)，
不修改 shell profile。缺少 Python 3.11+ 时由 uv 管理项目解释器。不会调用 sudo、修改系统包、
安装开机服务或复制其他机器的模型登录。后续直接调用 uv 时可将 `~/.local/bin` 加入 PATH。

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
安装成功的含义是 Web 进程存活且 Workspace API 就绪。开始真实 Agent 工作前仍需用户自行安装、
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
