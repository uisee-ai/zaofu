> 状态: active

# Broad resume 保留批准的增量前置

用户已授权修复两个业务卡点、恢复并行执行。监工复查本轮固定 Planner 工件时发现，
旧失败及下游诊断的13项选择只映射到两个替代末端，漏掉已批准图中3个新前置。
该任务只修机械选择，不改运行态、计划字节、任务验收或并行/重试上限。

## 证据与原因

- 固定图：`fanout-prd-plan-evt-d89a3d5a/prd-planner/artifacts/plan/task_map.json`，
  SHA256 `71c2b56315a138218159cc6075cfaa9fce630b34e363eba62c6c18c183d50b9c`。
- 图含42任务，26个已完成；增量5项替代2项，剩余16项。
- `amend.resume_scope=failed_children_and_downstream`，触发的 `task_ids` 精确等于
  历史 `failed_task_ids ∪ downstream_task_ids`；原选择器只支持 `gap_tasks_only`。
- 原loader选13项，遗漏 ALIGN-TRUTH、RETENTION-CONTRACT 和 MEDIA-REDACTION。
  临时副本显式选择批准gap与下游并集能选16项且通过原准入，详见
  `/tmp/zf-supervision-20261107/writer-selection-analysis.json`。

## 验收

- 明确 broad amendment 与有效 broad scope、诊断集合相同且
  `failed ⊆ superseded ⊆ diagnosis` -> verify: 保留历史选择并并入批准gap，再经
  既有替代映射得到前置、后继和下游；不纳入未选的done任务。
- 显式新任务子集、无关选择、scope不匹配、诊断缺失或越界替代 -> verify:
  不扩展选择；既有 `gap_tasks_only` 行为保持。
- gap声明缺失/重复/未知ID、pipeline根owner不满足 -> verify: 原门禁继续拒绝；
  不将broad scope伪装成gap-only来跳过完整图准入。
- 实际 `load_writer_task_map`、恢复直接调用与mock E2E -> verify: focused tests、
  premerge sentinel和相关确定性mock通过，记录精确层级。

## 实施记录

新增回归修改前5 failed / 21 passed；最小改动位于
`src/zf/runtime/writer_gap_diagnostic_selection.py`。后续验证及提交号由监工统一补齐，
未经实际完成不得归档done。架构说明：`docs/impl/task-recovery-20261105.md`。

## 已执行验证

- RED：`tests/test_writer_gap_diagnostic_selection.py` -> 5 failed / 21 passed。
- GREEN：该组及 `tests/test_writer_fanout_admission.py` -> 66 passed。
- `scripts/dev-verify.py plan/run --base dev --paths` 限定本批源码、测试、说明及任务，
  另显式列出 writer caller、module gap、task pipeline generation 与 runtime-noise mock：
  自动选择14测试文件，349 passed；未运行全量pytest或真实provider。
- `scripts/dev-premerge-gate.sh` -> 50 passed / 1 skipped。
- 同一固定提交Plan和原触发payload实际调用 `load_writer_task_map` -> 16任务，
  包含3个新前置，原schema/矩阵/pipeline检查通过；只读验证，未改不可变工件。
- `git diff --check` 通过；精确模块补丁对主checkout `git apply --check` 通过。
- 记录均位于 `/tmp/zf-supervision-20261107/broad-selection-*.log`。

代码尚未由监工提交或加载；因此本记录保持active，不宣称产品任务已完成。
