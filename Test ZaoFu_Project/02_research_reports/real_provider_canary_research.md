# ZaoFu 真实 Provider canary 调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/native_run.py`。Codex K547 与 Claude 原生 pipeline 按 K610 操作者要求跳过；保留方法，不重新检查安装/登录或自动运行。

> 状态: done

## 目标

为 ZaoFu 自身的 Provider/宿主机边界补充显式、可重复、不会进入默认回归层的真实 Codex/Claude canary。测试只验证 ZaoFu 能否正确管理 Provider 生命周期，不评价模型能力。

## 当前生产入口

- `src/zf/web/headless_agent.py::CodexHeadlessBackend.run_turn()` 是当前 Web/Kanban headless Codex 的真实入口。
- 每次调用启动 `codex app-server --listen stdio://`，执行 `initialize`，随后选择 `thread/start` 或 `thread/resume`，最后执行 `turn/start` 并收集流式通知。
- `HeadlessTurnResult` 提供 `status`、`provider_session_id`、`reply`、`messages`、`usage`、`resumed`、`fallback_reason`、`incomplete` 与 `completion_reason`，足以形成结构化 oracle。
- `run_turn()` 的 `finally` 总会调用 RPC client 的 `close()`；`close()` 先 `terminate()` 并等待，失败后再 `kill()`。
- request 或 turn 的 idle timeout 被 `_codex_failure_status()` 归类为 `status=timeout`。
- `src/zf/web/headless_agent.py::ClaudeHeadlessBackend.run_turn()` 是 Claude 的真实入口：
  通过 `claude -p --output-format stream-json --input-format stream-json --verbose` 启动
  子进程，以 `--session-id` 建立首轮会话、以 `--resume` 恢复同一会话，解析流式
  transcript 后将超时、incomplete 和 provider error 映射为结构化结果。
- Claude canary 使用 `HeadlessTurnResult` 的 `backend/status/reply/provider_session_id/usage/`
  `messages/resumed/completion_reason` 字段形成 oracle；`run_turn()` 返回前由
  `agent_session_process` 监管并结束子进程。

## 当前宿主机约束

- 本机 Codex CLI 可用；真实两轮一次性探针已经验证首轮和 resume 均能返回精确 marker。
- 默认 namespace sandbox preflight 在当前宿主机不受支持。仅在操作者显式设置 `ZF_RUN_REAL_CODEX_CANARY=1` 时，canary 才使用仓库已有文档建议的 `danger-full-access` 绕过；测试 prompt 明确禁止工具、文件和额外网络操作，并验证 transcript 中没有 tool message、隔离 workdir 没有变化。
- 当前 PATH 的 `claude` 可执行文件来自 Node 安装；旧的 2.1.273 原生扩展和新的
  2.1.275 原生扩展同时存在。tmux canary 已改为优先使用显式
  `ZF_REAL_CLAUDE_COMMAND`、其次 PATH、最后最新已知绝对路径，不再默认钉死旧版本。
  `ZF_REAL_CLAUDE_MODEL` 为空时不追加 `--model`，避免把宿主模型配置误判为生命周期失败。
- 2026-09-22：Claude headless start/resume/timeout 2/2 passed；tmux CLI 返回
  `Not logged in`/`unrecognized_model`，因此 tmux 真实 Provider 仍是环境阻断，不是 ZaoFu
  生产状态机通过或失败。

## 测试合同

1. 首轮启动：Provider 返回 `completed`、精确 marker、非空 session id、结构化 usage，并通过 callback 留下 transcript。
2. 原会话 resume：第二轮复用同一 provider session，`resumed=true`、无 fallback、返回第二个精确 marker。
3. 副作用边界：两轮 transcript 不得出现 tool use/result，隔离 workdir 前后保持为空。
4. 超时：以极短 idle timeout 启动真实 app-server，必须 fail-closed 为 `status=timeout`，不得伪装成功。
5. cleanup：正常与超时返回后，不得留下本测试新增的直接 `codex app-server` 子进程；临时目录由 pytest 隔离 fixture 回收。
6. 默认隔离：测试同时标记 `host`、`real_provider`、`serial`，并要求显式环境开关，默认 TestKit/S5 不运行。

## Claude canary 合同补充

1. 首轮 Claude 返回精确 marker、`status=completed`、非空 session id、`completion_reason=end_turn`
   和非空 usage，并通过 callback 留下文本 transcript。
2. 第二轮使用首轮 session id 调用 `--resume`，必须 `resumed=true`、session id 不变并返回第二个 marker。
3. 两轮都不得产生 tool message，隔离 workdir 不得有文件。
4. `timeout_s=0.001` 的真实调用必须返回 `status=timeout`，不能被包装为成功；返回后不得残留 Claude 子进程。
5. Claude 命令路径必须显式可执行；坏 shim、缺少凭据或限流应分类为环境/provider failure，不能伪装为 ZaoFu 业务通过。

## StreamJsonTransport 真实 SDK 复核

- 当前 `tests/integration/test_stream_json_resume.py` 原用例传入已删除的
  `StreamJsonTransport(default_model=...)` 参数，属于测试装配过时；按当前签名补齐
  `RoleSessionRegistry`、`RoleConfig`、`spawn()`、`poll_events()` 和 `shutdown()` 后才
  能进入真实 SDK 路径。
- 在 `RUN_REAL_CLAUDE=1`、`uv run --extra stream-json` 下，SDK 请求被当前宿主环境的
  `ANTHROPIC_BASE_URL=https://api.kimi.com/coding/` 与 `ANTHROPIC_MODEL=k3` 组合拒绝，
  错误为 `unrecognized_model`（query source=`generate_session_title`）。这是 provider
  配置/兼容性环境失败，不应记录为 ZaoFu 生产状态机缺陷，也不应通过自动重试隐藏。
- 该失败与 `ClaudeHeadlessBackend` 直接 CLI canary 的 2/2 通过并不矛盾：两条入口的
  SDK/CLI 参数和 provider 请求路径不同。完整 StreamJsonTransport 真实矩阵仍需在
  provider 支持的模型/endpoint 下重跑。

## Codex CLI 参数兼容性复核

- `CodexAdapter.build_command(RoleConfig(permission_mode="restricted"))` 当前生成
  `-a untrusted -s read-only`；这是 ZaoFu 生产命令构造的真实输出，不是测试替换。
- 当前安装的 Codex CLI `0.154.0` 在无副作用 `--help` 探针阶段即拒绝该值，返回
  exit 2，并列出可用值 `on-request`、`never`。Doc156 两次 live E2E 均观察到同一
  参数导致 delivery worker 未 ready；证据登记为 `ZF-TEST-048`。
- 该探针不连接 provider、不写项目状态，因此可在显式 host/real_provider tier 中
  稳定执行；修复参数映射前保留 strict xfail，不能把失败归因于模型能力或简单重试。
