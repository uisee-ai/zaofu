# 产品交付入口保留任务局部必读端口

> 状态: active — 2026-11-10；根代理授权独立最小修复，不作为当前 FINAL 发布前置；根代理独占 dev 合并。

基线 `71f33b0e` 的实际 `ingest_task_map_to_kanban` 已在临时状态复现：global 声明 `task_execution_input`，task-local 声明 `task_execution_input/local_detail`，ingest 成功但 TaskStore 仅剩 global，丢失 local_detail。LS 当前 writer 路线本来保留正确，本批不改其代码或运行态。

验收：

- 保留全局与 task-local 声明 -> verify: actual ingest→TaskStore 得到稳定并集，无局部端口丢失，不修改源 TaskMap。
- 实际输入选择仍绑定原政策 -> verify: ingest 后 current_plan_port_sources 保留局部正文/选择；真实缺失 port 明确失败，不创造必读端口或修改选择。
- 原校验与兼容行为 -> verify: 非法名/重复声明及选择绑定继续由既有 validator 拒绝；全局/局部单独声明、旧无声明路线保持。
- 完成影响闭包 -> verify: product delivery caller、input selection/sidecar/schema 回归、sentinel、相关 mock E2E 通过；记录自动 dev-verify 选择与实际执行范围。
- 完成实现及归档提交 -> verify: done 含实现 hash，清洁 worktree 冻结交根代理；不 merge/push/删除 worktree。

source-index 修复落 dev `fa5c78f8` 后，已通过本工作分支的 `0c34e0a4` 合入，并在现有 `docs/impl/plan-candidate-input-selection.md` 增补独立段落。incoming 四个路径与本批自有 dirty 无重叠，merge 前后 source/test/task 的 SHA256 一致，未 stash 或操作 dev。`rows/scenarios` 和 #205 不扩大。

实施：原 `evidence_contract.required_plan_ports = global` 改为 global + task-local 的稳定 canonical 名称并集；只保留现有声明，重合别名沿用 `canonical_plan_port_name`，不改 selection。非法/单层重复仍在既有 TaskMap 验证阶段拒绝，未在合并器中放宽。

验证记录：

- 原 `71f33b0e` actual ingest 在 `/tmp/zf-product-required-repro-20261110-*` 状态复现成功返回却丢 local_detail；原输入逐值不变，临时状态已清理。
- TDD：修正夹具（实际 alias 是 prd_ref/requirement_spec，完整选择所需 global 还包含 task_map）后 7 failed / 7 passed；修复后 `tests/test_product_required_inputs.py` 14 passed，覆盖 actual ingest→TaskStore→current_plan_port_sources，包含两个 worker profile、未选择但已声明的局部端口缺失、原 policy 保留与非法/重复/未绑定拒绝。
- `/home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py plan --base dev` 选择 32 个相关文件；`run --base dev --workers 4 --receipt /tmp/zf-product-required-verify-20261110.json` -> 自动确定性层 601 passed / 7 skipped，66.11s，未使用 skip-tier。其中含 artifact package、collaboration loop mock E2E 及真实 product delivery 调用方；不是全 pytest 或真实 provider E2E。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_task_plan_consumption.py tests/test_task_scoped_plan_inputs.py tests/test_task_map.py tests/test_plan_artifact_ports.py tests/test_plan_candidate_input_selection.py tests/test_gap_task_input_delivery.py -q --no-cov -m 'not perf and not host and not real_provider' -n 4 --dist loadfile --tb=short` -> 111 passed，2.48s。先前命令误列不存在的 test_task_map_required_ports.py，xdist exit5/无测试执行；改为现有 TaskMap 测试后以上述结果为准。
- `bash scripts/dev-premerge-gate.sh` -> 53 passed，4.14s；Ruff 与 `git diff --check` 通过。
- 合入 `fa5c78f8` 并补文档后最终验证：`dev-verify.py plan --base dev` 选择 33 个相关文件；`/home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run --base dev --workers 4 --receipt /tmp/zf-product-required-final-verify-20261110.json` -> 609 passed / 7 skipped，64.86s，无 skip-tier，未触发全量或真实 provider 层。
- 最新基线输入与恢复共享回归：上述六个输入/TaskMap 文件加 `tests/test_control_actions_candidate_rework_source_index.py`，同一 pytest 参数 -> 123 passed，2.78s；最终 `bash scripts/dev-premerge-gate.sh` -> 53 passed，3.83s；Ruff 与 diff-check 再次通过。

额外只读输入限制：尝试将当前 R2 原始 66-task TaskMap 整体送入临时 product ingest，旧入口的 completeness gate 先因 9 个保留任务只有 allowed_paths、未写 scope 而拒绝，未进入本次合并逻辑。没有改原体或绕门禁，临时目录已清理。该入口适配问题不在本批，不能宣称真实 R2 全图经 product ingest 成功。LS 当前实际 writer 路线本来保留正确，本修复未改其运行态。

实现与最终验证完成，待实现/归档提交。未写 LS event/state 或重启进程。
