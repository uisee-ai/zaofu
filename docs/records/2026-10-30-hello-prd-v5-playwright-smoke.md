# Hello World v5 PRD：真实 Playwright 验收记录

2026-10-30；基线 `b33b6b9f`；结论 **BLOCKED，尚未闭环**。

任务：[Hello World PRD 真实浏览器闭环验证](../../tasks/active/2026-10-30-0604-hello-prd-playwright-smoke.md)。

## 执行范围

采用用户确认的最小需求：Python 标准库 `hello.py`，无参数输出 `Hello, world!`，`--name ZaoFu` 输出 `Hello, ZaoFu!`，退出码均为 0，提供 unittest，只有一个实现任务。

本轮使用真实 Codex CLI 0.162.1、Docker `mcp/playwright:latest` 和新构建的 Web。API/UI 绑定 `0.0.0.0:8002`。项目创建、Kanban 消息、提案修订、建单确认、Workflow/Raw/澄清面板全部通过浏览器操作；没有通过 API POST 或 CLI 代建单、代启动或改写运行时真相。

| 身份 | 值 |
| --- | --- |
| 分支 | `wip/codex-20261030-hello-prd-smoke` |
| 项目 | `hello-prd-v5-smoke` / `project-b8ad9c68` |
| Task | `TASK-5CB1FC` |
| Request | `prepare-TASK-5CB1FC-bcace73e47b63f32` |
| Synthesis Operation | `wop-workflow-synthesis-select-flow-fa3866debd67` |
| 项目路径 | `/tmp/zf-hello-prd-20261030-060443/project` |
| 状态目录 | 项目配置的 `.zf-hello-prd-v5-smoke` |
| 业务 Run | 未创建 |

## 观察与断言

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 浏览器新建项目 | PASS | [创建页面](hello-prd-v5-smoke-20261030/01-create-project.png)、HTTP 201 |
| Kanban 真实需求提交 | PASS | [提交页面](hello-prd-v5-smoke-20261030/02-kanban-submitted.png)、真实 Provider turn |
| 项目内 PRD | PASS | [Agent 生成的 PRD](hello-prd-v5-smoke-20261030/generated-prd.md) |
| 受控 Task 创建 | PASS，经过修订 | [建单提案](hello-prd-v5-smoke-20261030/04-create-task-proposal.png)、`task.created` |
| 自动 Workflow preparation | PASS | `workflow.intake.ready`、`workflow.synthesis.queued/requested` |
| 指定 v5 的配置装配 | BLOCKED | [澄清页面](hello-prd-v5-smoke-20261030/07-v5-clarification.png) |
| 完整 YAML 预览和启动 | 未到达 | Raw 无候选 YAML，Playwright 断言 `workflow-approve-run` 数量为 0 |
| Scan / Plan / Impl / Verify / Judge | 未执行 | [结果 JSON](hello-prd-v5-smoke-20261030/result.json) 中业务阶段事件为空 |
| 产品验收 | 未执行 | 没有 `hello.py`；独立验收脚本已准备，未冒充运行成功 |
| 空闲退出 / 同 session 恢复 / 多 lane 并行 | 未覆盖 | 单任务主链尚未启动 |

真实合成事件顺序：

```text
06:16:13 UTC  task.created / workflow.intake.ready
06:16:14 UTC  workflow.operation.started / workflow.synthesis.requested
06:20:07 UTC  workflow.synthesis.result.prepared
              workflow.operation.settled (admitted_call_result)
              workflow.synthesis.clarification.required
```

合成 operation 的结算仅说明合成结果被接纳，不代表产品 Run 启动或交付成功。

## 确认阻塞

页面提出：当前合成合同无法绑定指定 v5 模板，`bounded-direct-v1` 不在允许的执行 Profile 目录，需要先经受控配置流程引入完整 v5 配置。

只读复核结果：

- 项目初始化得到 Issue/PRD/Refactor 默认配置，允许的执行 Profile 只有 `direct-v1`。
- 仓库 `zf-prd-v5.yaml` 本身可加载，展开 29 个逻辑角色，使用 `bounded-direct-v1` 和 WRC blocking。
- `src/zf/core/profile/flows.py::_catalog_entries` 只枚举 `examples/prod/*.yaml` 与 `controller/*.yaml`，不会发现根目录 v5；`_entry_from_catalog` 还要求正数 `roles`，v5 metadata 未声明该字段。
- `workflow_synthesis_support.admission_catalog` 基于当前项目配置建立角色和执行 Profile 准入集合；文本中指定 v5 文件不会自动安装该配置。
- 本轮未替换为 v3/v4，也没有直接写入项目 `zf.yaml` 来绕过浏览器装配路径。

这些证据表明“v5 YAML 可解析”与“新项目可从 Kanban 完整选用 v5”之间仍缺入口接线。本轮不能证明 v5 的后续执行阶段有故障，也不能证明它们通过。

## 干预和额外观察

- Web 在前端构建完成前启动，静态资源尚未挂载；构建完成后重启了本次 8002 服务，恢复正常。8001 未重启。
- 首轮 Kanban Agent 做了过多仓库检索并生成 PRD；监工通过页面停止该轮，要求提交已有提案。后续没有代写产品实现。
- 第一个建单提案触发 `intent.source_quote must occur verbatim in the user semantic context`，确认按钮被禁用。经浏览器要求引用当前消息原文后，提案可确认并创建 Task。
- Workflow 顶部显示 `Task：未绑定`，而 Request 的输入来自真实 Task。仅记录为投影待核实项，本轮未修改它。
- 未调整模型、生命周期、执行权限或预算来绕过 v5 准入；未要求 Provider 伪造验证结果。

## 验证与后续复跑

- `npm run build`：TypeScript、Vite 和 bundle 检查通过。
- Playwright：真实页面操作完成；明确断言 v5 阻塞提示可见、启动按钮不存在。
- 配置与事件只读检查：当前执行 Profile 为 `direct-v1`，v5 未出现在候选目录，未出现业务阶段执行事件。
- 本轮没有修改产品源码；没有运行 pytest 或产品 unittest，不能将准备好的验收脚本算作已执行。

下一轮先补齐注册模板到受控配置候选的接线、项目路径/Git/验证命令适配和 Profile 准入，再从新项目重复同一 PRD。继续保持真实 Scan/Plan/Impl/Verify/Judge，完成后运行 [独立验收脚本](hello-prd-v5-smoke-20261030/verify_hello.py)。

证据索引：[运行清单](hello-prd-v5-smoke-20261030/manifest.json)、[浏览器请求轨迹](hello-prd-v5-smoke-20261030/browser-mutations.jsonl)、[结果与关键事件](hello-prd-v5-smoke-20261030/result.json)。临时 runtime 在证据导出后停止，原状态单独封存用于诊断；没有推送或合并。
