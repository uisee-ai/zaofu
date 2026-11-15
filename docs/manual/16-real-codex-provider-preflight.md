# 真实 Codex Provider Preflight

> 适用对象: 运行真实 Codex E2E、Channel Codex provider、真实 provider smoke 的操作者。

## 1. 先跑预检

```bash
uv run zf doctor provider --backend codex
uv run zf doctor provider --backend codex --json
```

预检只读取环境,不会启动 ZaoFu worker,也不会写 runtime truth。它检查:

- `codex` CLI 是否在 `PATH`。
- `codex --version` 是否可执行。
- 在隔离临时目录内运行当前 Codex 自身的 `sandbox --permission-profile :workspace` 命令。
- 启动探测不调用模型、不验证账号或业务；未知 CLI 接口/超时/权限失败返回非零，不把 `unshare -n` 结果当 Codex 资格。

如果输出 `sandbox: unsupported`,普通 Codex sandbox 可能在启动前失败,常见错误类似:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
Codex sandbox unsupported for sandbox=workspace-write:
unshare: unshare failed: Operation not permitted
```

## 2. E2E 策略

真实 E2E 不允许自动降级 fake provider 或以关闭 sandbox 代替正常权限验证。
预检失败先修复宿主，再以实际运行 Provider 的普通用户重验；不要用 root 的结果代替。

Ubuntu 24.04 的新节点可执行仓库内受限安装脚本：

```bash
bash scripts/setup-codex-sandbox.sh --check
bash scripts/setup-codex-sandbox.sh --apply
```

`--check` 不提权。`--apply` 仅在 Ubuntu 24.04 安装发行版 bubblewrap、apparmor-profiles、
apparmor-utils，加载发行版 bwrap-userns-restrict profile；最后以原用户执行预检。
已安装包不重复安装，已有相同 profile 可重复加载；定制 profile、符号链接或缺失发行版源文件
会停止并保留现场。不会关闭全局 AppArmor/userns 限制，不安装 setuid helper，不修改 Codex 账号配置。
自动包安装不重启业务服务；如果 profile 或内核不支持，应由管理员调查，而不是自动放宽。
其他发行版先按包管理器安装 bubblewrap，再运行 doctor；自动修复脚本不会假装已资格化这些平台。

官方前提见 [Codex sandbox prerequisites](https://learn.chatgpt.com/docs/sandboxing)。
2026-11-16 在 Ubuntu 24.04.2、Codex 0.164.0、bubblewrap 0.9.0 验证：专用 profile 允许 bwrap
建立 namespace，受限子进程仍不能写工作区外的测试文件，userns 限制保持1。
安装包本身未解决该宿主问题，加载专用 profile 后才通过。跨节点不能只检查包是否存在。

### 2.1 WebKanban 启动模式

Channel 的 `permission_profile` 是 ZaoFu 的语义写入约束，不再选择 provider OS
sandbox。所有 Codex headless turn 默认使用 `danger-full-access`，所有 Claude
headless turn 默认使用 `bypassPermissions`；Kernel 的 write-policy、artifact
路径和 controlled action gate 继续独立执行。

可信本地开发机应使用 canonical launcher：

```bash
tools/start-webkanban.sh --host 127.0.0.1 --port 8001
```

该 launcher 会加载 action token、Workspace provider 环境，并在未显式覆盖时为
可信本地 WebKanban 设置：

```text
ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY=never
ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE=bypassPermissions
```

检查实际启动策略：

```bash
tools/start-webkanban.sh --port 8001 --status
```

历史可信开发默认可能显示 `codex_headless_sandbox: danger-full-access`、
`claude_headless_permission_mode: bypassPermissions`、`tmux: running` 和
`api: ok`；这不属于受限权限验收。新节点受限运行须显式选择 `workspace-write` 并先通过 doctor。`zf doctor provider` 检查的是
host capability，不是 ZaoFu 是否已显式 bypass。

直接 `uv run zf web ...` 也会使用相同的代码默认值；以下变量仅用于显式记录或
覆盖 launcher 进程：

```bash
export ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
export ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY=never
export ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE=bypassPermissions
uv run zf web --host 127.0.0.1 --port 8001
```

`danger-full-access` 与 `bypassPermissions` 会关闭 provider 原生的常规权限门。
它们只适用于明确受信任的本地代码库和网络；共享主机、非可信项目或
production-like 环境不应使用此默认部署模型。

## 3. Channel 失败判断

Channel / Kanban Agent 使用真实 Codex provider 时,如果 sandbox 或 app-server 启动失败,期望事件是:

- `channel.agent.reply.started`
- `channel.agent.reply.failed`

Web 应显示失败原因,而不是把 provider 环境问题伪装成 agent 已复核或 task done。排查时查看:

```bash
uv run zf events --last 80
uv run zf doctor provider --backend codex --json
```

Codex app-server 可能在 stderr 输出:

```text
Codex could not find bubblewrap on PATH ... Codex will use the bundled bubblewrap ...
```

这条本身是非致命 warning,不应被当作失败根因。若 channel 显示
`timeout`,优先判断是否是 Codex app-server 在 channel provider budget 内
没有继续输出事件。Codex turn 没有总时长上限:持续有 token / tool /
status 流式事件就会续期。默认普通静默预算为 1800 秒;检测到工具调用
尚未完成时,静默预算切换为 7200 秒。可按本地场景显式覆盖:

```bash
export ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S=3600
export ZF_CODEX_HEADLESS_TOOL_TIMEOUT_S=14400
```

旧的 `ZF_KANBAN_AGENT_HEADLESS_TIMEOUT_S` 仍会被兼容读取,但 channel 场景
优先使用 `ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S`。

## 4. 脱敏要求

不要打印真实 token。检查环境变量时只输出 key 或脱敏值:

```bash
env | grep -E 'CODEX|OPENAI|ZF_' | sed -E 's/(=.).+$/=***REDACTED***/' | sort
```
