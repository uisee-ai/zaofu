# Channel 设置的 Other 回答与恢复

Other 是问题卡片内提交的自由回答：选择后聚焦输入框，空白不可提交，多行原样保留；切回固定选项不会携带旧的 Other 草稿。原生 Codex 问题接受 `isOther`，同时保留已有的 `allow_other` / `allowOther` 兼容字段。

参考 [Codex App Server 的 requestUserInput](https://learn.chatgpt.com/docs/app-server)：自由回答属于原问题，答复后结束对应等待。本实现对齐交互语义，不声称复刻 Codex 客户端的像素布局。

## 设置单续接

- 固定选项仍按精确配置走受控 Apply。action-bound Plan 的 Other 只提交修订意图；即使请求含顶层 `submit_action`，也强制得到空 action、空 payload 和 `continue`，不会执行旧选项。
- 记录 `kanban.agent.plan.answered`，恢复同一 durable turn；新版设置单沿用 request id、递增 revision，durable interaction 同步 revision。旧问题不会悬挂，也不会因默认 revision 1 与新版内容冲突。
- 当修订需要先注册缺少的 Channel Profile，提案绑定原设置单。用户批准且受控注册成功后，Web 继续原设置流程，要求 Agent 生成新版确认项；独立注册不自动生成群组。Other 和注册成功均不等于新群已经创建。
- 默认项目的 API 和兼容动作入口每次读取当前 `zf.yaml`，注册成功后下一轮立即看到新 Profile，避免启动时的配置缓存造成重复注册。运行中若修改 `project.state_dir`，要求重启 Web，不将新配置混入旧状态目录。
- 保留 Chat about 作为提问入口；与提交 Other 回答区分。已用 Other 或非执行型 continue 选项回答的当前设置单可继续修订，支持确认候选后注册角色再续接。续接重新校验账本中原回答对应的执行语义；已选择执行项的设置单不可再次讨论或应用。

## 操作输出

仅对最终专用 JSON envelope 容忍缺少 Markdown 结束围栏；JSON 本体必须完整，尾随说明和正文中的例子不会被提取。恢复后仍经过语义 intent、payload、token 和提案绑定校验，前端展示确认卡片并隐藏控制 JSON，保留说明正文的换行。损坏的专用提案输出显式失败，可重新生成，不再伪装成正常完成。

## 问题整理恢复

Kernel 的 Channel reply housekeeping 检查仍处于 phase2 的最新 requested 整理请求。超过 60 秒且没有 reply request 时，重放原请求到既有 reactor/router；复用消息、保持 request id，不铸造新一代。已经派发的回复继续由 reply remediation 管理；新请求、已关闭讨论、已解决问题及被替换请求不重启。单次最多补派 8 个请求。

外部／内置 Channel Skill 按正文 SHA-256 写入 `project.state_dir/runtime-skills/<name>/<digest>/`，整目录原子发布。新增成员不会覆盖已有成员绑定的 Skill 文件，旧版非版本化路径也保持原样。项目自有 Skill 仍直接绑定项目源文件，变更后严格校验会要求重新绑定。上下文诊断将 `skill_guidance_*` 与 `profile_snapshot_*` 区分；历史共享文件已经漂移时，通过受控 `channel-invite-member` 重新绑定原成员，保留成员 ID、Profile 和权限，然后由原整理请求的恢复路径继续执行，不绕过哈希校验。

## 验证

- `tests/test_channel_setup_other.py`：TestClient 调用实际 API / durable store / event / controlled action 路径，模拟 provider 返回，覆盖 Other → 新版确认 → multi_lens 群创建、注册角色后续接，以及未闭合和损坏提案。
- `tests/test_channel_dedup_recovery.py`：实际 `Orchestrator.run_once()` 与 router，覆盖零消息和已写消息两个崩溃窗口、重复检查与终态排除。
- `web/tests/agent-plan-options.spec.ts`、`web/tests/webchat-interaction-parity.spec.ts`：卡片内 Other、聚焦、空白校验、草稿切换和提交；使用 Docker Playwright 执行。
- Web model / projection 单测覆盖 `isOther`、多行答案、固定选项隔离及恢复提案的展示。

浏览器用例使用 UI fixture，不调用真实 provider。独立终端在工作树的 `web/` 目录运行 `npm run dev -- --host 0.0.0.0 --port 8008`，再于工作树根目录运行：

```bash
docker run --rm --network host \
  -v "$PWD:/work" -w /work/web \
  -e ZF_WEB_BASE_URL=http://127.0.0.1:8008 \
  --entrypoint /bin/sh mcp/playwright:latest \
  -lc 'for browser in /ms-playwright/chromium-*/chrome-linux64/chrome; do export ZF_E2E_CHROMIUM_EXECUTABLE_PATH="$browser"; break; done; node node_modules/@playwright/test/cli.js test tests/agent-plan-options.spec.ts tests/webchat-interaction-parity.spec.ts --reporter=line'
```

这些检查分别验证 API 流程和 UI 交互，不能替代连接真实 provider 的完整浏览器端到端验收。执行后停止临时 UI 服务，保留 8001 给真实开发会话。
