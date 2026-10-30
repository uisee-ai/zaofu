# 有界、无损的上下文交付与返工预算优化

> 状态: active

用户批准实施 1–4 项优化，要求不丢失上下文信息。来源为
2026-10-30 Kanban 完整生命周期验证：返工 Operation 在 1,572,266 / 1,500,000
tokens 阻塞；首次返工编辑前消耗 1,197,933 tokens。10 个必读切片有 4 对正文
重复，批量去重输出因 8192 字符限制失败；返工复用长会话并全量重读。

## 范围

1. 受控批量读取支持有界续页，跨页保持别名去重、完整正文和独立来源授权。
2. 返工上下文减少冗余交互；任何会话或交付优化须保留全部当前合同、版本、失败项、
   代码与证据来源，不能将旧读取记录当作当前理解或静默省略材料。
3. Skills 按阶段和触发条件加载，方法全文仍保留并可恢复；实现/测试/提交硬规则不降级。
4. 预算监控拆分新输入、缓存、输出、定价状态；在预算耗尽前提供可用的机械信号和
   安全处理路径，保留硬上限和累计账本，不原地扩大默认额度。

## 验收

- 多来源、别名、Unicode 和大正文分页 -> verify: 拼接内容与原完整切片逐字一致；
  所有必读凭证完整，单页有界，缺页不能通过结果准入。
- 篡改、旧游标、不同 attempt、越权别名 -> verify: 拒绝交付且不追加成功读取凭证。
- 返工/恢复 -> verify: 当前身份、合同、代码目标及失败项完整；不可证明安全时全量读取，
  保留旧产物与事件，不伪造当前材料消费。
- Skill 方法 -> verify: 源包和 Codex/Claude 分发一致，明确按需触发且原质量要求保留。
- 预算 -> verify: 真实 caller 覆盖拆分用量、提前处理和硬限额，重复 tick 不重复通知，
  未定价不能显示为免费。
- 交付 -> verify: focused impact closure、sentinel、相关 deterministic E2E、归档用量证据
  回放；报告真实 Provider 验证是否执行及其范围。

独立分支：`wip/codex-20261030-context-delivery`；基线 `d41ebdac`。
此前 Kanban worktree 的未提交改动保持原样；本批次本地提交，不 push/merge。

## 实施与边界

- 批量必读入口新增绑定 manifest/必读范围/正文摘要的 `--cursor`，单页最多
  8192 字符；别名单独鉴权和记账，缺页不能通过既有读取门禁。
- 返工 briefing 只去掉头部已有且逐值相等的重复字段；完整失败项、当前身份和
  全部必读正文保留。读取顺序聚焦失败复现和相关代码，避免重新全仓发现。
- Dev 方法改为按实现、复杂增量、故障、外部事实、Git 写入和 handoff 触发；
  源包与两套分发全文一致，方法依赖均保留。
- `zf cost --operation <id> --json` 从当前启动 baseline 读取分类用量和余额，
  未定价金额为未知，终态 operation 拒绝借用当前角色计数。真实 budget sweep
  追加 50%/80% 去重观察事件；开工/心跳指令要求固化续跑说明，既有熔断保留。

为满足无信息丢失要求，本次不自动压缩/更换 Provider 会话、不把旧读取收据
当作新 attempt 已读证明。预算信号与续跑说明已接入；自动会话切换仍须另行
证明跨 Provider 的完整恢复和当前理解，不在本次实现中声称已解决。

## 验证记录

使用 `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest`，
参数均为 `-q --no-cov`，主要 focused closure 另排除 `host/real_provider/perf`：

```text
tests/test_artifact_read_batch_cli.py tests/test_artifact_read_cli.py
tests/test_artifact_read_ledger.py tests/test_artifact_read_capability.py
tests/test_plan_read_aliases.py tests/test_task_local_plan_ports.py
tests/test_workflow_budget_guard.py tests/test_workflow_budget_status.py
tests/test_workflow_resume_budget.py tests/test_budget_sweep.py
tests/test_cost_budget_enforcement.py tests/test_cli_cost.py
tests/test_cost_no_double_count.py tests/test_cost_tracker.py
tests/test_task_pipeline_capsule.py tests/test_task_pipeline_briefing.py
tests/test_task_pipeline_rework.py tests/test_task_context_recovery.py
tests/test_context_recovery_continuity.py tests/test_context_delivery.py
tests/test_event_problem_registry.py tests/test_task_pipeline_role_instructions.py
tests/test_skill_provenance.py
```

结果：233 passed、1 skipped。最后补充临界值不进位及 worker 自定义 state_dir
CLI 测试，并调整提示语后，重跑 budget_status、budget_guard、capsule、briefing：
44 passed。单独核对 Skills 三份分发字节一致；`git diff --check` 通过。

`scripts/dev-verify.py plan --base dev` 发现原 dev 与本批基线的无关差异，随后用
`--paths` 明确本批全部改动并传入直接调用测试，计划无错误。事件注册表保守
传递闭包扩大到 514 个测试文件并建议全套；按 AGENTS 的 focused tier 规则，
`run --base dev --paths <本批路径> --skip-tier deterministic` 配合上述显式闭包，
没有运行全量 pytest。自动 serial tier 10 passed；premerge sentinel 51 passed，
manual docs currentness 通过。receipt：`/tmp/zf-context-verification-receipt.json`。

`bash scripts/run-flow-smoke.sh`：10 passed，包含三流固定 smoke 与 Task Pipeline
runtime noise mock E2E。未启动新的真实 Provider/browser E2E。

归档回放：`/tmp/zf-context-replay-20261030-1006/replay.py`，输入为上次真实运行的
`/tmp/zf-kanban-full-e2e-evidence-20261030-0836/runtime-state.tar.gz`。首轮与返工
均为 10 项必读、6 份唯一正文、6 页；每份正文逐字一致，全部 10 项凭证满足。
正文由 39,713 减至 22,044 UTF-8 字节，减少 44.49% 的重复正文；这不是实际
token/费用降幅。回放报告 `report.json` 留存，临时恢复的 runtime state 已清理。
原 1,572,266 / 1,500,000 超限样本仍判越限，未定价美元余额显示 null。

此验证证明上下文交付、调用接线和硬预算回归，不证明原 Kanban 全生命周期
已经最终 done；原真实运行的 blocked 记录不改写。
