# PRD 到完整 Plan 的 UI 实测

真实 Channel 已完成业务闭环、独立审计和浏览器补验；真实 Kanban 独立复验整轮通过，
两条 fake 路径均通过。真实复验使用冷启动场景，不能据此宣称已有 runtime
配置刷新问题已修复，也不证明测试需求对应的产品实施已经完成。

## 范围与方法

工作分支 `wip/codex-20261110-prd-ui-journeys`。首批实现 `d7cabbef`，集成当前 dev
后的基线 `367b6586`，真实实测补丁为 `a640c5c1`（`fix: declare full Plan review
coverage and stabilize real UI tests`）。每轮 `evidence/run.json` 保存实际启动时的
HEAD、候选 diff 和测试源码摘要；不能用事后的 commit 冒充运行时版本。

方法见 [PRD UI 验证 Skill](../../skills/zf-verify-zaofu/references/prd-ui-journeys.md)。
执行器为 `tests/e2e/prd_ui/run.py`、`web/tests/prd-ui-journeys.spec.ts`，浏览器采用
Docker `mcp/playwright:latest`。业务写操作从 UI 发起；API、账本和产物用于独立只读
核验。fake 仅替换模型，真实 Kernel 完成派发、准入和审批请求。

基线需求是系统 `cat README.md` 的完整读取、缺失原生报错和文件保护，不新增读取
程序。测试终点为 PRD v5 / `plan_approval: true` 生成的待审批完整 Plan；没有批准实施。
这不是 PostgreSQL、数据导入等业务模块的功能验收。

`--workflow-runtime existing` 保留讨论 runtime；`cold` 在 YAML 预览后核对没有
在途 operation/Channel 回复，经 `zf stop --fast --include-run-manager` 正常停机，
然后由 UI Start Workflow 触发生产入口启动新 runtime。停机和启动身份有独立回执，
没有改写 canonical 状态或关闭 stale-consumer 保护。

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey kanban --workflow-runtime cold
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey channel --workflow-runtime cold --skip-build
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey kanban --workflow-runtime cold --skip-build --timeout 5400
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey channel --workflow-runtime cold --skip-build --timeout 5400
```

## 真实 Channel

证据根：`/tmp/zf-prd-ui-real-channel-20261110-122346/evidence/`。

- 12:29:42 UTC：UI 启动四角色 multi_lens；12:32:27 完成独立贡献。
- 12:46:29：真实 Synthesizer 完成正式 PRD r1；本次综合耗时约 14 分钟。
- 12:48:39：product_pm、arch、critic、synthesizer 同版签署，Owner 已通过 UI 确认。
- 12:51:43：UI 创建 `TASK-3B58D8`；12:54:26 启动 Workflow。
- 12:59:13：真实双扫描完成；13:12:04 Planner 产物准入。
- 13:20:21：独立 Critic 通过并显式声明完整性；13:20:23 生成待审批 Plan。

身份与摘要：

| 对象 | 值 |
| --- | --- |
| PRD revision | 1 |
| PRD SHA-256 | `d86608e6676066272bda68cd13beb13d7d732de9be2949ea318c82e17d33f74d` |
| Workflow Run | `prepare-TASK-3B58D8-06f1299a4e216c20` |
| Plan | `evt-f6ebd4bf6637` |
| Plan SHA-256 | `f3279d4914fdefac5b32e1f60becb463c091b6d8510ed4618ffc9fb01e232550` |

业务测试已完成全文、阅读模式、下载和刷新断言，最后写出 `lineage.json` 与
`events.json`；随后旧测试器导出 trace 时耗尽 Node 堆内存。因此原 `run.json`
仍为 **failed**，不能把它报告成原整轮 runner 绿色。

独立 `audit-after-trace-failure.json` 通过：只有一次 invoke、一个父 Task、同 Run 的
待审批 Plan、正确 PRD revision/digest、四角色真实签署，实际派发角色均为 reader，
没有 `plan.approved` 或实施派发。另以同一真实产物执行短时浏览器复验，
`preview-recheck-complete/result.json` 为 **passed**，保存完整 trace/video、源码截图、
逐字一致的下载，以及刷新后再次打开源码并逐字核对的截图。没有重启 Workflow。
最终分项结论保存在 `real-channel-verdict.json`。

该轮在 Critic 派发前更新了候选 Critic Skill，`candidate-update.json` 和
`critic-skill-provenance.json` 记录边界及实际物化摘要；不能声称整轮使用不变源码。
讨论、综合和签署对应实际 GPT-6-Astra / xhigh 会话；规划 worker 实际使用
GPT-6.1-Sol / medium，身份摘要见 provider provenance 文件。

人工核对 AC-01..05 从 PRD 到 Plan 保留：正常/空文件读取、真实缺失失败、原文件
权限与复读、禁止新增 reader、同版文档及审批顺序。PRD/SDD/TDD、Decision Register、
来源覆盖和独立验证责任均有正文；未来执行状态保持 NOT_RUN。

## 真实 Kanban 复验

最新独立运行：`/tmp/zf-prd-ui-real-kanban-20261110-130157/evidence/`。
本轮使用已修正的阶段限制提示、Critic 完整性方法和增量事件取证，Playwright 整轮
**1 passed（34.2 分钟）**，`audit.json` 通过，完整 trace（约 111 MB）正常导出。

- Task：`TASK-E2A428`。
- Run：`prepare-TASK-E2A428-ee67207aab737b15`。
- Plan：`evt-5bfc3220ba7c`，13:35:34 UTC 进入待审批状态。
- Plan SHA-256：`704bae965ee1942ddbbf00f7975f170be5c1bd828bbd11fb4f073f89e3b9a6dd`。
- 一次 invoke、一次 runtime 启动、一次 Plan 审批请求；无 Channel 替代，无额外 Owner
  决策检查点，无 Plan 批准或实施派发。

`preview-recheck-complete/` 另行通过严格刷新正文复验，确认刷新后实际 DOM 正文
逐字一致，并保存短时完整 trace。该补验没有重启 Workflow，也没有改写原通过记录。
人工核对 Plan 保留正常、缺失、不可读三种场景，原生输出/退出码、固定根目录、
文件恢复、禁止新增 reader 和阶段授权边界；产品执行状态保持 NOT_RUN。

此前 `/tmp/zf-prd-ui-real-kanban-20261110-120918/evidence/` 是 assisted run：
Owner 经 UI Reject 澄清 Git 禁令仅约束当前编写/预览阶段，Kernel 在同 Run 内重规划。
第二轮 Critic 通过，但遗漏 `plan_content_profile`，完整 Plan UI 断言正确失败。
该轮还发生旧 trace 导出 RangeError，原失败保留；不能用“审查通过”替代完整性声明。

## 已确认的失败与限制

| 场景 | 归因与保留证据 |
| --- | --- |
| 已有 runtime 启动真实 Workflow | `consumer_config_stale`：配置编译改变执行策略，但启动入口复用旧 PID；未修复。证据 `real-kanban-20261110-110158/evidence/`，Task `TASK-7CFAB7`，rejection `evt-3e00c84a4f46` |
| 首轮真实 Channel | 固定半程 900 秒等待截断综合；证据 `real-channel-20261110-105219/evidence/`，额外 Owner 澄清单独标为 assisted |
| 第二轮真实 Channel | 错误继承 Kanban 的 600 秒预算，native `turn_aborted` 与超时对齐；证据 `real-channel-20261110-115958/evidence/`。现显式使用 Channel 生产默认 1800 秒 |
| Owner 检查点预览 | rejected Critic 结果的候选 refs 未传到审批卡，UI 正文不可用；证据在 `real-kanban-20261110-120918/evidence/owner-checkpoint-before.png`。UI Reject 带澄清可触发原 Run 重规划，此引用缺口未修复 |
| 长时 trace | 全量反复读取事件，并把成功响应全文放入断言说明，导致取证膨胀；已改增量 cursor 和失败时才记录响应正文。原失败未覆盖 |

表内缩写目录均位于 `/tmp/zf-prd-ui-` 前缀下。三个未批准候选分别记录 stale consumer、
Channel 超时后的受控恢复、Owner 检查点引用缺口，仍为本地 proposed backlog，未入库。
冷启动验证不覆盖热配置刷新；本次生成文档没有分页选择器，多页导航条件分支没有实测覆盖。

## 修改与验证

1. conversation 回复完成后允许受控切换 multi_lens；在途回复仍阻止切换。
2. Channel Task 默认 `prepare_workflow`，绑定可信 target_root，保留显式值并拒绝非法值。
3. 聚合准入传播 Critic 的显式完整性声明；不从标题或缺失字段推断完整性。
4. 纯聚合 Critic Skill 和实际简报明确：完整性声明是 verdict 元数据，需根据确切来源
   和 PRD/SDD/TDD/Decision Register 覆盖审查返回，不属于应留空的结构化计划正文。
5. 测试补充冷启动身份交接、正常回收旧 PID、正确 Channel 预算、增量取证，以及刷新
   后等待正文渲染并逐字核对；不绕过任何业务门禁。

验证规划器因大型 fanout 模块的传递闭包超过 80 文件，自动扩大为全仓 pytest；已中止
该扩大步骤，保存中止记录，未作为完整验证。按其列出的 **233 个模块**显式执行，
结果 **3623 passed / 8 skipped**；另行串行层 **10 passed**、合并哨兵 **53 passed**、
mock flow smoke **10 passed**。Web typecheck、全部 Web unit、构建与 bundle budget 通过。
针对新 Critic 合同和完整性传播的独立检查为 **32 passed / 1 skipped**。
这不是全仓 pytest 或发布覆盖率验收。

fake 最后回归证据：

- Kanban（包含严格刷新正文检查）：`/tmp/zf-prd-ui-fake-kanban-20261110-132455/evidence/`。
- Channel（包含增量取证）：`/tmp/zf-prd-ui-fake-channel-20261110-130158/evidence/`。

已结束的临时运行经 `simulation.done`、停止自己的 Web/runtime/容器/tmux 后归档；
诊断包排除 provider home 和凭据目录。真实 dev 的 8001 和其他会话不在清理范围内。

## 后续修复

上述三个候选在后续获得批准，修复与验证见
[PRD UI 恢复闭环修复验证](2026-11-10-prd-ui-recovery-validation.md)。
本记录中的原始运行状态与当时结论保持不变。
