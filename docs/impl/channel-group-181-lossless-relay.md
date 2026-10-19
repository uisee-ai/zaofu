# Channel Group 181 实施与验证

> 日期：2026-10-19。依据：[设计 181](../design/181-channel-group-roles-lossless-output-reliable-handoff.md)。
> 范围：独立 worktree 的实现、确定性回归、真实 Codex 接力及浏览器回读；尚未部署到 8001。

## 接力影响评估

只删短回复 Skill 会留下多 item 覆盖、普通输入 latest-only、摘要上下文和恢复窗口，
因此本批次同时调整这些路径。Handoff 的受控入口、权限、预算、请求身份、generation、
questions/freeze 及签收协议保持既有权威。完整正文增加输入量；超过来源预算时明确阻塞，
不会继续用被截掉的上下文执行。来源完整不等于模型已理解，语义覆盖仍须验收。

## 最终行为

| 路径 | 行为与代码 |
| --- | --- |
| 角色/方法 | participant Skill、system/response prompt 解除句数和正文摘要约束；专业方法按需，机器合同保留。[prompt](../../src/zf/runtime/channel_provider_prompt.py) 与 canonical Skill/两份分发副本一致。 |
| Codex 输出 | [collector](../../src/zf/web/codex_public_text.py)按 native item 聚合，记录 turn/item/phase，commentary 单独处理，完成快照仅替换本 item；重复完成及迟到 delta 不重复正文。 |
| 原文证据 | 完整正文和公开 item 存在已脱敏的 message sidecar；事件保留预览、ref/digest。失败已有正文保存为 `incomplete` 证据，不进入成功合同路径。 |
| 合同/续写 | 保留 Markdown + 隐藏合同格式；完整替换合同只替换旧机器后缀，保留此前正文。continuation 文本在合并边界确认后补入 live stream，避免 JSON 残片泄露。原始 item 继续可审计。 |
| 来源 | 普通 relay 完整交付同线程截至触发消息的语义来源；不读未来消息。初轮盲答和 adaptive blind 仅交付用户来源，移除同伴观点及其索引/问题/频道摘要/报告投影，并使用独立 provider 会话，避免旧历史绕过盲答。文件缺失、digest 不符或超预算明确失败。 |
| 排队 | 每个不同请求保留独立身份和处理责任；取消隐式 latest-only。不同目标的成功与失败分别保存。 |
| 交接恢复 | requested/accepted/context-pack 已落盘但 reply 未生成时，housekeeping 在锁内补全同一意图；已存在 pack 严格回读，不按新角色绑定重建。 |
| 结果恢复 | provider 完成已落盘、合同尚未应用时，复用同一 dispatch claim 与已有合同处理器补应用；不再调用 provider。旧 generation、关闭讨论及已处置结果不重复消费。 |
| 未知执行 | ACK 丢失、提交后断连、运行中超时而缺少可靠终态时，标记 `delivery_unknown`，禁止自动重投，并阻止同成员后续执行；其他成员继续。仅有保存正文、尚无运输层完成记录的窗口也保守进入核对。 |
| Web | 长文本保留保护阈值，提供分页与完整下载；Unicode 代理对不跨页拆开。刷新重新读取持久正文，正文缺失展示明确错误，不将预览当全文。 |

`channel.agent.reply.completed` 仍是运输层事实，不能推导业务签收。合同 rejection、
贡献/评审/签收事件仍决定各阶段业务结果。对 `delivery_unknown`，操作者须先核对/隔离旧
provider 会话，再通过既有恢复动作处理；不通过手改事件或原 sidecar 强行解锁。

## 自动化验证与复现

使用 `uv sync --extra dev --extra web` 建立 worktree 虚拟环境，frontend 使用自己的
`node_modules`。测试子进程需要 `PATH="$PWD/.venv/bin:$PATH"`，否则依赖裸 `python`
的候选质量门禁测试会报环境错误。

```bash
python scripts/dev-verify.py plan --base dev --tests tests/e2e/test_channel_181_relay_mock_e2e.py --json
python scripts/dev-verify.py run --base dev --tests tests/e2e/test_channel_181_relay_mock_e2e.py --skip-tier deterministic
```

planner 因共享调用方选中 370 个测试文件，超过 80 文件阈值而建议整个 `tests/`。
本批次直接执行其 `selected_tests` 列表，4 个 worker，10 分钟上限，排除
`serial/perf/host/real_provider`；没有修改 selector 或把未映射源码当作免测。
其余 serial、Web typecheck/unit、premerge sentinel 和 flow-smoke 由上述 run 执行。
新 E2E driver 通过显式 `--tests` 绑定测试文件。最终计数见归档任务。

| 设计验收 | 对应测试/证据 |
| --- | --- |
| 多 item、Unicode、重复快照、ACK 丢失 | `tests/test_codex_public_text.py`，包含真实子进程协议模拟 |
| 合同替换、JSON 分片、续写、partial、独立失败 | `tests/test_channel_public_output.py`、`test_channel_reply_stream.py`、`test_channel_reply_deadline.py` |
| 来源尾部/异议、跨线程、盲答、digest/预算 | `tests/test_channel_relay_integrity.py`、`test_channel_typed_contracts.py` |
| 意图/接受/pack/正文/完成后的进程退出 | `tests/test_channel_relay_integrity.py`，故障注入在 durable append 后抛出异常，重新投影并调用实际 housekeeping 入口；不宣称覆盖每个 OS kill 时点 |
| 忙碌成员多个输入、旧 generation、并发 claim | `test_channel_formal_reply_queue.py`、`test_channel_router.py`、`test_channel_reply_turn.py`、`test_channel_adapter_parallel_dispatch.py` |
| 盲答原生会话隔离 | `test_channel_provider_retry_session.py` 通过实际 headless 调用方验证 conversation、blind-1、blind-2 三个不同会话，均不携带旧 session ID |
| Other、多轮提问、Owner、综合、签收及旧绑定 | `test_channel_setup_other.py`、`test_channel_skill_materialization.py`、typed contracts/模板/工作流影响闭包与 flow-smoke |
| 五角色贯通 | `tests/e2e/test_channel_181_relay_mock_e2e.py`、真实运行下述 driver |
| 长文、全文导出、刷新、Kanban 共享展示 | Docker Playwright：`channel-long-message`、`channel-relay-evidence`、`agent-session-json-rendering`、`agent-session-contribution`、`agent-session-layout` |

## 真实 Codex 与 CLI 对照

运行入口：

```bash
python tests/e2e/scripts/channel_181_relay.py --root /tmp/zf-channel-181-final-20261019 --real --cli-baseline
ZF_WEB_ACTION_TOKEN=channel-181-isolated-test python tests/e2e/scripts/channel_181_relay.py \
  --root /tmp/zf-channel-181-final-20261019 --serve-only --serve 8018
```

上面的 token 仅用于此隔离模拟，不是生产凭据。真实接力由 driver 调用 runtime handoff
入口；浏览器验收读取真实 API 和 UI、刷新，再检查五份结果。没有将 UI fixture 冒充真实
provider，也没有声称浏览器从 Other 开始完成了整套真人审批流程。

双方使用本机 `codex-cli 0.162.1`、新会话、同一工作目录、同一 PM 冻结来源包、
system prompt 与协作合同。核对六个 native `turn_context`：均为 `gpt-6-astra`、
`high`、`danger-full-access`；应用逻辑权限为 `read_only`，不等于 OS 沙箱。
沿用现有执行策略，测试要求不调用工具、不修改文件；未以本轮证明 OS 文件隔离。

| 真实成员 | 正文字符数 | 完整消息来源数 | 核验 |
| --- | ---: | ---: | --- |
| 产品经理 | 572 | 1 | 30 天、24 小时、离线设备未决异议 |
| 架构师 | 641 | 2 | 同上，并保留原始来源 |
| Critic | 674 | 3 | 同上，并区分建议与确认要求 |
| UI/UX | 807 | 4 | 同上，状态反馈不代替验收证据 |
| 后端 | 845 | 5 | 同上，保留失败路径与未决边界 |
| 原生 CLI 产品经理对照 | 578 | 同一 PM 来源包 | 同样保留三项要求/异议 |

逐项检查稳定 ID `REQ-181-A`、`REQ-181-B`、`DISSENT-181`，并人工阅读原文确认：
未将 30 天改为“到期立即删除”，未将 24 小时改为“重新联网起算”，未把离线异议判作已解决。
字数是观测数据，不是质量分数。此小样本不证明所有任务、模型或 provider 语义等价。

Docker 使用 `mcp/playwright:latest`、host networking、UID 1001、可写的临时 XDG
配置/cache 目录，选择镜像已有 Chromium `/ms-playwright/chromium-1226/chrome-linux64/chrome`。
UI 监听 `0.0.0.0:8019` 并代理 `8018`，通过 `ZF_CHANNEL_181_PROJECT` 选择测试报告中的项目。
未安装宿主浏览器。最初的 Docker group、镜像入口/浏览器版本和工作目录字体路径问题已解决。

## 补充验收：修改前后对比与 PRD 聚合

2026-10-19 后续验收未修改产品源码，使用 Docker `mcp/playwright:latest`、host
networking 和隔离状态；未安装宿主浏览器。

| 验收 | 结果与限制 |
| --- | --- |
| 修改前后同输入对照 | 基线复现首条公开 item 被覆盖、接力缺失原文中间要求、20 万字符以上尾部不可访问三项缺陷；当前版本对应三项及格式兼容检查全部通过。8 次执行中基线三项标记为预期失败，不能将汇总 `8 passed` 解释为基线满足新要求。 |
| 新一轮真实接力 | 五个真实 Codex 会话依次获得 1/2/3/4/5 份完整来源；要求、30 天、24 小时及离线异议全部保留。真实页面与刷新回读 1 项通过。 |
| 五角色聚合路由 | 确定性 provider 下，产品、架构、critic、UI/UX、后端分别作为 synthesizer，5/5 通过；每次 6 份来源、5 份贡献。运行中重复指定其他成员沿用既有请求，只生成一份 PRD。 |
| 真实 PRD 聚合 | Playwright 点击 Synthesize，由真实 Codex 产品经理聚合上轮五份真实回复和原始需求；首次合同通过，生成 10,672 字符 PRD。五方观点有具体处置记录，消费来源 digest 为 6、贡献 ref 为 5。 |
| PRD 回读及门禁 | 预览、Source、完整下载、刷新及禁止 Process 的浏览器用例通过；下载逐字等于 canonical PRD。三个未决问题使其保持 `draft` / `needs_owner`，未生成共识、签收、Task 或 Workflow。相关后端回归 78 项通过。 |

矩阵证明各角色可被受控入口指定为聚合者；一次只运行一位，UI 使用讨论指定的
synthesizer。本轮真实聚合仅由产品经理执行，不代表五次真实模型质量对照，也未完成
Owner 回答后的再聚合、全员独立复审与最终确认。multi-lens 初轮须有五人直接回应同一
原始要求的贡献；仅有纯串行接力的五份消息不满足该门槛。

**已知未修复：问题卡来源跳转。** 聚合产生的三张 Owner 问题卡显示
`Review question · source unavailable`。问题事件/投影缺少 `source_run_id` /
`source_message_id`，同一产品成员有原始贡献和聚合两条 run，前端
`channelDecisions.ts` 无法唯一绑定来源。PRD 与问题正文仍完整，但不能从问题卡跳回
聚合回复。后续应补明确来源身份及 API/UI 回归；此处记录不代表问题已修复。

详细证据保留在本机 `/tmp/zf-channel181-ab-20261019T123306Z/REPORT.md` 和
`/tmp/zf-channel181-synthesis-20261019T130752Z/REPORT.md`，含日志、输入清单、
截图与 trace。首次聚合浏览器脚本对 consensus 和 request_id 投影的错误假设已修正，
失败记录保留，最终回读通过；未因此重新调用模型或修改产品代码。

## 部署与边界

- 本批次不更新真实项目的成员/profile 绑定；新 Skill 版本须通过既有受控操作绑定。
  在途输入和历史 sidecar 继续使用原冻结版本。
- 原有历史 latest-only 合并不会被本次迁移自动复原为待处理任务。
- 旧匿名 provider item 无稳定 native ID 时，按单一 legacy item 兼容，不能凭空恢复身份。
- live delta 属追加投影；provider 非前缀修订以最终持久快照为准，无法撤回已展示片段。
- 未知执行是可见阻塞，目的是避免重复外部执行；本批次没有伪造 exactly-once 或自动判定
  所有 provider 接收状态。没有移除预算、权限、盲答隔离、机器合同及人工审批。
- 首批及 A/B 验收的临时服务和运行状态已清理。后续按用户要求保留 8002 回放与
  8003 PRD 查看服务及其隔离状态；停止时应记录 `simulation.done` 并清理。
  合并代码不等于重启部署；8001 尚未重启到本版本。
