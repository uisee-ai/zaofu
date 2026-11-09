# Inbox 审核与授权收敛实施记录

日期：2026-11-09。基线：`052e6a40`，独立分支 `wip/codex-20261109-inbox-review`。
设计入口：[196 Inbox 人类审核与授权中心](../design/196-inbox-human-decision-center.md)。

## 实施范围

Inbox v3 只接纳 Plan 审核、具有现有重规划动作合同的 approval，以及能校验身份、版本和
sidecar digest 的 Workflow 提案。移出四类消息及其历史、计数；不改写源账本、不代答或解除等待。
Workflow 从 Inbox 进入同一 Request 的原审核页面，仍审阅 Raw / 配置并经过原受控提交路径。
已读、批准与执行进度保持分离。新 revision 替代旧待办，旧阅读回执不能覆盖新版本。

Run Manager 请求、owner_attention、合同不完整的旧 approval 在 Traces → Operations（旧 Runtime 路由兼容） 可发现。
有原处置合同的请求沿用受控动作，acknowledged 显示 processing；没有合同的不生成按钮。
原 Channel / Kanban 问题由原会话回答并续接。前端 Operations 切换项目与刷新响应做身份隔离，
防止旧请求覆盖新项目或处置后的结果。

没有新增 canonical 审批存储、通用权限平台或通知队列；删除了不再使用的 Inbox 问答适配器。

## 自动验证

在 worktree 执行，均使用 `--no-cov`，不是全量 pytest，也不将 mock 视为真实 Provider：

1. 主影响闭包：206 passed。覆盖 Inbox、阅读、Plan 预览 / 身份 / CLI、通知来源、Channel
   闭合、Task Workflow 输入、Workflow 提案 / 审批 / 配置应用、浏览器夹具及 Kanban mock E2E。
2. 扩展合同闭包：173 passed。覆盖重规划暂停/续接调用方、Web server、Sidecar、EventLog、
   Channel execution approval、Web 快照及项目隔离；其中 mock E2E 与上一组重叠，不能相加当独立用例。
3. 最后投影回归：38 passed（`test_operator_inbox.py`、`test_inbox_decision_center.py`、
   `test_operator_workflow_approvals.py`）。
4. `bash scripts/dev-premerge-gate.sh`：53 passed；文档 currentness 检查通过。
5. `npm --prefix web run build`：typecheck、构建、bundle budget 通过。
   `dev-verify run` 的 Web 自动层包含全部 Web unit tests，通过。
6. Docker Playwright 的 Inbox / Workflow 回归：17 passed，包括迟到响应、项目隔离、已读竞争、
   重规划预览版本和 1440/390/320 宽度筛选。新增导航入口后，Traces 的 2 项回归也通过。

验证规划器先运行 `uv run python scripts/dev-verify.py plan --base dev`。删除的旧模块与新增 E2E
支持代码通过显式 `--tests tests/test_operator_inbox.py --tests tests/e2e/test_inbox_browser_fixture.py`
补齐映射。通用 `read_model.py` 规则保守展开到 123 个文件并升级整套 pytest；依仓库分层测试约定，
本次用上述模块、直接调用方、EventLog / sidecar / API 合同闭包替代整套扫描。
自动层命令保留 `--skip-tier deterministic --skip-tier serial`，并非这些合同未经验证：

```bash
uv run python scripts/dev-verify.py run --base dev \
  --tests tests/test_operator_inbox.py \
  --tests tests/e2e/test_inbox_browser_fixture.py \
  --skip-tier deterministic --skip-tier serial \
  --receipt /tmp/zf-inbox-review-evidence-20261109-082217/verification-auto.json
```

原始日志：`/tmp/zf-inbox-review-evidence-20261109-082217/`（合同闭包、sentinel、规划回执），
`/tmp/zf-inbox-review-evidence-20261109-082741/`（最终构建、17 项浏览器回归、38 项投影回归）。

## 实际 Web/API 验收

可复跑入口：

```bash
npm --prefix web run build
.venv/bin/python tests/e2e/run_inbox_review_acceptance.py
```

实际 API、受控动作、持久化账本与 Docker Chromium，4 项通过：

- 两条 Plan 的未读/待办计数独立；阅读不批准，批准/拒绝进入历史。
- 移出的运行请求在 Operations 可发现，Diagnose 到真实后端后显示 processing，仍不进入 Inbox。
- Channel 问题在来源回答并记录 resolution，不产生 Inbox 待办。
- Kanban 原问题收到答案后出现下一项提案，不产生 Inbox 待办。

最新证据：`/tmp/zf-inbox-review-evidence-20261109-084518/`；包括 Playwright trace、视频及事件副本。
夹具本身含 seeded 事件和替代 Provider，不作为真实模型或任务交付证明。各场景结束写入
`simulation.done` 并清理临时服务和状态。

## 真实 Provider 验收

隔离目录：`/tmp/zf-inbox-provider-20261109-0857/`，实际创建时间为 08:18 UTC 左右，目录尾缀
不是执行开始时间。项目 `project-2be77a0e`，Task `TASK-349699`，
Request `prepare-TASK-349699-d846e1f7bd0a1dac`。实际事件时间为准。
使用本 worktree 代码、Codex、源自 `zf-prd-v5.yaml` 的 V5 5.0.1 配置，临时端口 47951、
独立 `.e2e-state` / workspace / tmux。为限制验收资源，fixture 将 lanes 与对应池改为 1，
保留 blocking / on_demand、独立 Verify/Judge 和质量门；这不证明默认 8 lanes 性能。

实际输入要求创建 Python 标准库费用导出工具，先通过问题卡选择 CSV/JSON，回答后创建 Task、
准备 V5 Workflow、等 Owner 审核。完整 prompt 在 `evidence/kanban-input.txt`。

已观察：真实 Codex 提问；选择 CSV 后刷新保留未提交选择；提交答案后生成任务提案；
批准创建 Task；真实 Synthesis 生成提案；Inbox 出现同 Request/r2 的 approval；
从 Inbox 进入原页面核对 Raw，再提交启动；API 返回 202 accepted，runtime_start=running；
Task 进入 in_progress，产品与技术扫描实际派发。Inbox 同项关闭为 approved，未声称任务完成。

后续运行、终态与独立产物检查：验收中，最终归档前补齐。

## 验收中发现的问题

- **已修复的入口缺口**：原 Operations 页面可通过旧 Runtime 深链进入，但新版 Traces 没有
  可见入口。直接访问 URL 的测试没有覆盖可发现性。现已在 Traces 顶部增加绑定当前项目的
  Operations 链接，真实浏览器改为 Inbox → 侧栏 Traces → Operations → Diagnose 全程导航。

- 夹具初次把项目改为 1 lane，但注册目录仍提供 8 lanes 的源模板。真实 Synthesis 因约束冲突
  返回带 Markdown fence 的澄清 `plan_request`，当前解析器报“invalid JSON”。首次失败没有提案、
  Inbox 正确为空、没有 Run。保留 `workflow-preparation-first-failure.json` 和 Provider 原回复。
- 已校正**测试环境**：用受支持的 `ZF_EXAMPLES_DIR` 将隔离注册目录指向同一个 1-lane V5
  fixture，重启该临时 API，从原页面 Prepare Workflow → Recheck & retry preparation 续接同一 Task。
  没有伪造提案或 runtime 事件；第二次实际 Synthesis 成功。
- **未修复的既有问题**：Synthesis 的结构化澄清回复未被接入原问题流程，错误信息过于笼统。
  本次没有扩大到 Synthesis 协议修改；需要独立复现用例，覆盖约束冲突、question 返回、原处续接
  和有效 JSON 但 schema 不匹配的区分。
- 浏览器夹具最初两次失败来自断言：Channel 已回答的卡片应消失；Kanban 应等待真实 answer
  写入，不能用始终为空的 Inbox 作为同步条件。已修正，随后完整 4 项重复通过。

密钥、完整运行状态与原始 Provider trace 不进入 Git；本仓库保留方法、测试及证据位置。

## 真实验收追加的 Plan 回放修复

真实 Run 的第一轮规划已通过 Critic，但进入 `prd-lanes-impl` 时被机械内容围栏拒绝：
`product_acceptance_spec differs from the admitted package`。逐字段比较表明语义正文完全相同，
只有首次准入补齐的 `schema_version`、`workflow_run_id`、`flow_kind`、`plan_revision`、
`task_map_generation` 在原始显式正文中缺失。运行时已自动重规划，原始证据未被改写。

追加共享的 `bind_product_acceptance_identity`，首次准入与同代回放使用同一权威身份绑定，
随后仍比较完整 canonical JSON digest。修改用户旅程、断言、入口、资格要求等语义正文仍拒绝；
没有移除内容围栏，没有删除或修改已接纳包，也没有减少输入上下文。

先补实际 admission 调用方回归：原代码 **1 failed / 10 passed**（原样语义回放被拒绝）。
修复后 Plan 输入 / 输出 / Package / Ports / 产品验收 **50 passed**；再运行直接调用方
Reader/Writer、Plan Review/Rework 和相关 mock E2E，**385 passed**。
日志位于真实测试目录 `evidence/plan-replay-before-fix.log`、`plan-replay-after-fix.log`、
`plan-replay-impact.log`。首次影响闭包命令误写不存在的 `test_fanout_writer.py`，未执行任何测试；
更正为 `test_writer_fanout_admission.py`、`test_writer_fanout_runtime.py` 后得到上述结果。

通过正常 `zf restart --include-run-manager` 加载修复，未直接编辑运行状态。重启首次遇到
Web 父进程尚未回收已退出 watcher、PID guard 仍视为存活的问题；停止并重新启动该临时 API
后，僵尸子进程正常回收，再以 `zf start` 启动，随后 `zf recover workflow --dry-run --json`
返回一个 `redrive_interrupted_reader_operation` checkpoint；按该精确 checkpoint 调用
`--resume-pending --checkpoint-id`，收到 applied=1、rejected=0，从原请求续接被中断的 Critic。
该进程回收问题未在本次扩大修改。

追加修复的 `dev-verify plan --base HEAD --tests tests/test_plan_input_replay.py` 保守展开到
260 个文件；自动层保留回执，deterministic/serial 以已执行的 50 + 385 项闭包替代，
未重复未变化的 Web 测试，也未启动整套 pytest。

## 后端闭包的复跑命令

```bash
uv run pytest tests/test_operator_inbox.py tests/test_inbox_decision_center.py \
  tests/test_operator_workflow_approvals.py tests/test_operator_plan_preview.py \
  tests/test_owner_visible_delivery.py tests/test_goal_dossier_owner_delivery.py \
  tests/test_plan_content_profile.py tests/test_web_operations_observability.py \
  tests/test_plan_approval_identity.py tests/test_plan_approval_candidate_identity.py \
  tests/test_plan_approval_cli.py tests/test_channel_collaboration_closure.py \
  tests/test_task_workflow_plans.py tests/test_workflow_proposal.py \
  tests/test_workflow_requests.py tests/test_workflow_approval_requirements.py \
  tests/test_workflow_config_apply.py tests/e2e/test_inbox_browser_fixture.py \
  tests/e2e/test_kanban_plan_approve_mock_e2e.py -q --no-cov --tb=short

uv run pytest \
  tests/test_reader_fanout_runtime.py::test_plan_synth_owner_decision_holds_then_resumes_same_run \
  tests/test_web_server.py tests/test_sidecar_refs.py tests/test_events_log.py \
  tests/test_channel_execution_approval.py tests/test_web_observability_snapshot.py \
  tests/test_web_project_scoped_observability.py \
  tests/e2e/test_kanban_plan_approve_mock_e2e.py -q --no-cov --tb=short
```
