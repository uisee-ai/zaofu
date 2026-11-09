# SDD 生命周期与交付方法验证

本批实现说明：[规格生命周期与可复现交付](../impl/spec-lifecycle-and-delivery.md)。
独立 worktree：`/home/min/workspace/zaofu-worktrees/sdd-lifecycle-20261031`。
分支：`wip/codex-20261031-sdd-lifecycle`。基于 dev `61f6e034`，接入前批
`4ea97802` 的 SDD readiness 成果；只在本分支实施，在线 ls/FMS 未操作。

## 运行时 RED/GREEN

新增 `tests/test_spec_promote.py` 在改实现前运行：5 failed。真实 Judge handler/
回调使用临时 EventLog/TaskStore，存在和不存在的 spec_ref、声明的验证层级均被覆盖；
原实现会错误声明 completed，或把层级为空当成没有验收证据。

修复后 `tests/test_spec_promote.py tests/test_wave5_batch.py
tests/test_workflow_graph_runtime.py`：69 passed。完整测试使用 fixture 的已接纳终态
门，验证正常 Task 完成与规格更新声明分离；不是新的真实 Provider E2E 证明。
本批未实现自动规格更新服务，没有真实更新准入协议时不发布 completed。

## 影响闭包

先运行 `uv run python scripts/dev-verify.py plan --base dev --workers 2`，显式补充
spec_promote、wave5、workflow_graph_runtime 和 Kanban approval mock E2E。
规划器因 reactor 文件的广泛依赖选中 264 个测试文件，再因数量阈值扩大为全套。
按 AGENTS 的变更责任/影响闭包要求，未执行这一全套和不相关的 serial 锁测试；
没有修改选择器或弱化门禁。自动 receipt 如实保留这两个 tier 为 not_run。

执行 `dev-verify.py run` 同一计划并传入 `--skip-tier deterministic --skip-tier serial
--keep-going`：Web typecheck/unit passed；premerge sentinel 51 passed；flow smoke
10 passed（包括 task pipeline v4 runtime noise mock）。

独立执行聚焦 Python 闭包：376 passed, 1 skipped，17.43s；两个进程、300 秒上限，
排除 perf/host/real_provider/serial。文件集如下，可按同样集合重放：

```text
tests/test_spec_promote.py
tests/test_wave5_batch.py
tests/test_workflow_graph_runtime.py
tests/test_orchestrator_lifecycle_handlers.py
tests/test_runtime_orchestrator.py
tests/test_workflow_managed_task_lifecycle.py
tests/test_auto_ship_on_judge.py
tests/test_auto_ship_wiring.py
tests/test_goal_completion_gate_runtime.py
tests/test_completion_honesty.py
tests/test_judge_convergence_gate.py
tests/test_event_contracts.py
tests/test_events_log.py
tests/test_event_writer_schema.py
tests/test_plan_readiness_skills.py
tests/test_skill_provenance.py
tests/test_instruction_stack_contracts.py
tests/test_config_profiles.py
tests/test_adapter_skill_resolver.py
tests/test_module_gap_plan.py
tests/test_module_gap_plan_runtime.py
tests/test_goal_dossier_owner_delivery.py
tests/test_task_plan_consumption.py
tests/test_plan_content_profile.py
tests/test_plan_content_profile_cache.py
tests/e2e/test_terminal_kanban_convergence_mock_e2e.py
tests/e2e/test_kanban_plan_approve_mock_e2e.py
```

命令尾部：`-q --no-cov -m 'not perf and not host and not real_provider and not serial'
-n 2 --dist loadfile --tb=short`。覆盖变更模块、终态直接调用者、自动交付、Event/Store
边界、gap handoff、技能物化、前批 Preview/缓存回归和确定性端到端入口。
本批没有 Web 实现改动，未重复前批 Docker 浏览器用例；没有全 pytest、真实
Provider、在线项目部署或真实产品验收声明。

## 技能校验

三个变更 skill 的九份 canonical/provider 元数据通过 Kernel `read_skill_metadata`；
真实角色依赖物化、参考文件复制、分发一致性和 instruction/provenance 回归通过。
单独聚焦运行 49 passed, 1 skipped（已包含于后续闭包，不能加总成独立用例数）。
Ruff 检查变更 spec_promote 模块和新增测试通过。

最终两处方法澄清及评估归档后，再运行 `dev-verify.py run --paths` 精确指定六个
变更技能入口/参考和三份新增说明/记录，并加 `--tests tests/test_plan_readiness_skills.py
--workers 2`。自动闭包运行 instruction_stack_contracts、plan_readiness_skills、
skill_provenance、structure_discipline：61 passed, 1 skipped；receipt 为
`final-docs-skills-verify.json`。此轮只证明结构、分发与接线，不替代语义评估。

已运行通用 skill-creator `quick_validate.py`：三份均因仓库既有的 stages/tags/
auto_inject/load_on_demand（以及 readiness 的 dependencies）扩展字段不兼容而拒绝。
保留 Kernel 支持的元数据，不删除功能字段来迎合通用校验器；不能声称该校验器通过。

## 独立方法评估

按 skill-creator 的独立前向测试方法，在
`/tmp/zf-sdd-lifecycle-eval-20261031-1209/` 使用冻结合成输入和独立输出目录评估。
评估者只获得原始案例、新方法与必要代码，不读取父会话、仓库测试或预期答案。
输入目录有逐文件 SHA-256 manifest；不连接外部服务或在线 runtime。
完整输入、报告、方法摘要、脚本、指南和收据保存在
[独立评估快照](2026-10-31-sdd-lifecycle-forward-evaluation.md)。

- 规格修订样例：识别 24→72 小时对 API、客户端、UI 和验证证据的传播；复用
  未变列表的规范审查，保留新候选执行义务；不重新索取已有授权或改写旧批准版本。
- CLI 样例：16 次实际调用，10 次正常/拒绝断言成立，3 次暴露产品失败（取消后
  列表反证归于同一取消缺陷），2 次证明初始化缺失，1 次证明未授权入口可达。
  输出有界修复建议和可复现指南，保留正常行为、未知范围及交付 owner 责任。
- 评估发现三处方法歧义：规范结论复用与 current 运行收据、分类重叠与修复去重、
  诊断指南草案与独立最终验收。已补窄范围说明，评估者复核确认文档歧义解决；
  没有以复核文字声称重新执行样例。
- 输入摘要执行前后未变，临时数据清理完毕。指南作者的自检不是另一人的独立最终
  Verify；没有证明真实 Planner/Critic 的语义结果、真实候选准入或任何在线产品通过。

原始验证计划/receipt、聚焦日志与独立输出保留在上述临时证据目录。
这些是评估证据，不是在线工作流状态或部署结果。
