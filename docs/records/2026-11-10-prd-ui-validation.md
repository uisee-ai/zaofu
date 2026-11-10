# PRD 到完整 Plan 的 UI 实测

两条 fake provider 旅程通过；真实 Codex 已实际调用，但两条真实旅程均未完成到完整 Plan。
不能把本报告作为真实 Provider 全链路通过或产品实施通过的证明。

## 范围与执行入口

候选分支：`wip/codex-20261110-prd-ui-journeys`，基线 `cf4499e6`。
每次运行的 `evidence/run.json` 保存实际基线、候选 diff 摘要、测试源码摘要与运行模式；
报告之后的提交不是早先运行时的 HEAD。

方法见 [PRD UI 验证 Skill](../../skills/zf-verify-zaofu/references/prd-ui-journeys.md)。
执行器为 `tests/e2e/prd_ui/run.py` 和 `web/tests/prd-ui-journeys.spec.ts`，
使用 Docker `mcp/playwright:latest`。模型只负责回复和产物，真实 Kernel 完成派发、
结果准入与审批请求。业务写操作经过 UI，只读 API、账本和产物提供独立核验。

测试需求固定为项目根目录下 `cat README.md` 的正常读取和缺失报错。
配置从 PRD v5 / `plan_approval: true` 开始；终点为待审批 Plan，不批准实施。
该需求用于控制流程基线，不覆盖数据库、数据导入或其他产品的业务功能。

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey kanban
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider fake --journey channel --skip-build
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey kanban --skip-build --timeout 1800
PYTHONPATH=src:. .venv/bin/python tests/e2e/prd_ui/run.py --provider real --journey channel --skip-build --timeout 1800
```

## 分层结果

| 入口 / 模型 | 实际到达 | 结果与边界 |
| --- | --- | --- |
| Kanban / fake | 多题澄清 → 持久 PRD → Task → v5 YAML → Scan/Planner/Critic → Plan Preview | 通过；独立 Kanban，没有 Channel 替代 |
| Channel / fake | 建群 → conversation → multi_lens → 签署和 Owner 确认 PRD → Task → v5 YAML → Plan Preview | 通过；当前 PRD 的 digest、revision、Channel 身份绑定到 Task |
| Kanban / real Codex | 澄清 → 实际 PRD 文件 → Task → v5 YAML → Start 请求 | `consumer_config_stale` 拒绝，未进入实际规划 |
| Channel / real Codex | 四角色分析 → 问题归并 → 交叉审查 → 综合请求 | 测试等待超时；未完成 canonical PRD、Owner 确认或规划 |

fake 检查包含：一次 Workflow invoke、同一 Run 的 Plan 审批请求、完整性显式声明、
PRD/SDD/TDD 正文、下载与产物逐字一致、刷新后仍可查看，以及审批前未派发 writer。
实际规划角色为 `product-scan`、`tech-scan`、`planner`、`plan-critic`。
测试通过 operation 关联解析派发角色，不能从没有 role 字段的 attempt 事件猜测。
fake Plan 只有一页；切换阅读模式已覆盖，多页导航的条件分支尚无本轮通过证据。

最终 fake 证据目录：

- `/tmp/zf-prd-ui-fake-kanban-20261110-112012/evidence/`
- `/tmp/zf-prd-ui-fake-channel-20261110-111722/evidence/`

其中保存截图、trace/video、PRD、下载的 Plan、lineage、事件副本和 `audit.json`。
独立审计有错误 Plan、摘要冒充全文、截断、正文改变及审批前 writer 派发的负例测试。
最后一次 Kanban 回归修正了测试竞态：账本完成事件可能先于澄清卡片渲染；
测试现在依据 `waiting_input` 等待卡片再逐题回答，不再用即时可见性判断跳过问题。
该竞态的原始失败证据保留在 `/tmp/zf-prd-ui-fake-kanban-20261110-111722/evidence/`。

## 真实 Kanban 的产品阻塞

证据：`/tmp/zf-prd-ui-real-kanban-20261110-110158/evidence/`。
PRD 原文为 `source-prd.md`；完整状态诊断在 `diagnostic-project.tar.gz`。

- Task：`TASK-7CFAB7`。
- invoke：`evt-ef50756d2727`，2026-11-10 11:07:59 UTC。
- rejection：`evt-3e00c84a4f46`，11:08:00 UTC。
- 原因：`consumer_config_stale: load the approved execution policy before new admission`。

真实 Workflow Synthesis 编译的配置改变了 verify width、质量命令等执行策略，且模板
重新带入 resident Run Manager / Autoresearch 默认值。已有 runtime 仍持有旧快照。
`ensure_project_runtime` 复用已有 PID，消费者据此拒绝新 invoke。
预先打开 Plan 审批不足以避免这个问题；并非只有修改审批开关才会触发。

保护校验保持有效。待补齐的是配置应用与已有 runtime 的受控生效路径，以及 UI 的
恢复说明；必须同时保护在途 Run 和并发启动。已记录本地 proposed backlog：
`backlogs/2026-11-10-1440-workflow-stale-consumer.md`，未将该未批准候选提交入库。

## 真实 Channel 的超时归因

证据：`/tmp/zf-prd-ui-real-channel-20261110-105219/evidence/`。
这是 assisted run：在浏览器中额外发送一次 Owner 澄清，明确入口是系统 `cat`，
不是新增程序或修改 ZaoFu 文件预览。额外交互有独立截图、trace 和 JSON 记录。

- 10:59:15 UTC：开始四角色 multi_lens。
- 11:08:36：综合角色依据 Owner 澄清归并重复问题。
- 11:12:13：`channel.cross_review.completed` / `channel.question.resolved`。
- 11:12:15：`channel.synthesis.requested`，事件 `evt-0921e8deff7c`。
- 11:12:19：真实综合 Provider 启动；尚未返回最终结果。
- 约 11:14:15：测试的固定 900 秒阶段等待耗尽，随后清理隔离运行。

账本有持续进展，不能据此认定 Channel 死锁或 Provider 结果被拒绝。
已把测试的两个“半程预算”改为共用整轮超时，并将预算配置到测试及 teardown；
初始需求也提前明确读取入口和交付阶段。修改后 fake 回归，真实路径仍需重新验证。
没有把延长等待或额外 Owner 回复作为全链路通过证据。

## 本轮修复与验证

1. 自然讨论完成后允许通过已有受控动作切换为 multi_lens；在途回复时仍禁用启动。
2. Channel 新 Task 默认走 `prepare_workflow`，并携带可信项目 `target_root`，消除旧路由提示冲突。
3. 综合 Plan 准入保留已审核结果的显式 `plan_content_profile`；缺失或摘要声明不会自动升级。

定向闭包命令：

```bash
PYTHONPATH=src:. .venv/bin/python scripts/dev-verify.py run --base dev \
  --tests tests/test_channel_task_plan.py --tests tests/test_prd_ui_provider.py \
  --tests tests/test_prd_ui_audit.py --tests tests/test_plan_content_profile.py
bash scripts/dev-premerge-gate.sh
npm --prefix web run build
```

结果：56 个模块的定向 Python 闭包 **1127 passed / 8 skipped**；Web typecheck、全部
Web unit 和构建通过；sentinel **53 passed**。最后补充目标目录非法输入及其直接调用者的
定向回归 **97 passed**。这项最后调整只阻止显式非法值被默认目标掩盖，不改变已通过的 UI 正常路径。
这不是全仓 pytest、覆盖率验收或真实模型全链路通过。

隔离模拟均发出 `simulation.done` 并停止自己的 Web、runtime、容器及 tmux。
诊断归档不保留 provider home、凭据目录；真实 dev 的 8001 与其他会话不在清理范围内。
任务保持 active：真实两条路径的完整 Plan 验收仍未完成。
