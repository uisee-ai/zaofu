---
name: zf-verify-zaofu
description: 为 ZaoFu 自身开发执行项目级验证，选择启动、重启恢复、多 lane 和 Dashboard 的测试与证据；不用于其他产品的业务验收。
dependencies: [zf-harness-verification-checklist]
---

# ZaoFu 项目验证

采用 `zf-harness-verification-checklist` 的项目验证方法。通用方法决定如何验收，
本包给出 ZaoFu 的实际入口和证明边界；不改变 Verify/Judge 或 Kernel 的责任。

## 开始

1. 读当前仓库 `AGENTS.md`，核对候选 commit、base 和 dirty paths。
   默认在候选工作区执行，保护其他会话改动；真实项目状态位置从其 `zf.yaml` 的
   `project.state_dir` 解析，不从当前会话环境猜测另一个项目。
2. 初次使用或执行三条基线时读 [场景覆盖表](references/scenarios.md)；按变更选择
   对应测试。调用模板见 [本轮 Prompt](references/prompt.md)。
3. 依赖缺失时按仓库规则 `uv sync --extra dev --extra web`，前端需要时在 `web/`
   执行 `npm ci`。已有环境先复用；依赖错误记录为环境问题。
4. 日常变更使用现有入口，不另建 runner：

   ```bash
   python scripts/dev-verify.py plan --base dev
   python scripts/dev-verify.py run --base dev
   ```

   当前无 diff 时使用场景表中的基线命令。影响闭包之外的必要验收通过明确的
   `--tests` 加入；按仓库规则运行 premerge gate，不把 focused 结果称为全量通过。

## 三条链的签收

- 启动：冻结提案被受控提交后，检查实际 transport dispatch 和产物准入，
  不能把 accepted/pending_consumer 当成运行完成。
- 恢复：重建 runtime 后从持久化事实继续，检查新派发、最终交付与重放幂等。
  Run Manager 到 redrive 的故障交接单独验收；进程死亡和真实 Provider 需要其专属层。
- 多 lane：证明两个符合条件的任务同时在途，分别在隔离 worktree 执行并校验产物，
  最终候选包含两个结果，Verify/Judge 引用同一候选版本。

测试内的 scripted worker 是明确的 mock 边界；真实 Git、Store、事件和准入并不
把模型能力也变为已验证。三个场景的现有深度和缺口均列在场景表。

## PRD 到 Plan 的用户路径

模拟 Kanban Agent 或 Channel Group 讨论需求、生成 PRD、启动 PRD v5 Workflow 并
预览完整 Plan 时，读 [两条 UI 旅程](references/prd-ui-journeys.md)。先 fake provider，
再在已授权范围内运行真实 provider；每条路径保持独立来源与证据。

## Dashboard 与真实执行

UI-only 改动遵守仓库的前端验证层级。浏览器用 Docker `mcp/playwright:latest`，
API/UI 监听 `0.0.0.0`、容器使用 host networking，临时端口使用 `8002+`。
显式设置 `ZF_WEB_BASE_URL`，8001 留给真实 dev 会话；浏览器版本与项目 Playwright
版本不匹配时按环境失败处理，不安装 host browser 来掩盖问题。

先区分 `page.route`/fixture 浏览器测试与真实 API 的用户路径。只有前者通过时，
报告 UI 行为通过、后端闭环未验证。必要时沿受控 API 查询同一 Run 的实际事实。
视觉场景可接 Midscene，现有确定性断言和候选绑定继续保留；未安装/未配置模型时
标记该层未执行。加载本 Skill 不授权操作真实项目、启动付费 Provider 或发布。

真实流程观察可复用 `tests/e2e/five_workflow_terminal_runner.py`；先查看 `--help`，
从明确项目与 Run 生成绑定 manifest。它是外部观察器，不替操作者创建任务或补事件。
产物验收使用项目对应的独立 oracle，不能只数终态事件。

## 交接

按条款记录实际执行层级、退出码、JUnit/日志和产物证据；保留真实 Provider、浏览器、
进程恢复等未覆盖项。worker 使用 briefing 的结果 profile；普通维护不发任务事件。
临时模拟使用 `/tmp/zf-<purpose>-<utc>/`，按仓库规则结束模拟并清理自建资源。
