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
- 当前环境是否支持基础 network namespace probe。

如果输出 `sandbox: unsupported`,普通 Codex sandbox 可能在启动前失败,常见错误类似:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

## 2. E2E 策略

真实 E2E 不允许自动降级 fake provider。预检失败时只有两种选择:

1. 修宿主机 namespace / sandbox 权限,再重跑预检。
2. 在报告中显式记录风险,并仅对本次真实验证使用 Codex sandbox bypass。

示例:

```bash
codex exec --dangerously-bypass-approvals-and-sandbox --json "$PROMPT"
```

该 bypass 只适合临时 E2E 或受控 smoke,不应成为生产 worker 默认权限。

Channel / Kanban Agent 的真实 Codex headless 默认使用 `workspace-write`
或 `read-only` sandbox。如果预检显示 `sandbox: unsupported`,Web 侧会在
启动真实 Codex turn 前返回 `sandbox_unsupported`,避免等待超时。若这是
本机受信任项目里的临时写入验证,可以显式配置:

```bash
export ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX=danger-full-access
```

然后重启 WebKanban。该配置会关闭 Codex headless 的普通 sandbox,只能在
受信任本地项目和短期诊断中使用;长期修复仍应优先恢复宿主机
namespace / bubblewrap 能力。

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
