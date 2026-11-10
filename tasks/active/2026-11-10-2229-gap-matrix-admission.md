# 增量 Plan Package 的现代矩阵闭合准入

> 状态: active — 2026-11-10；监工 #205 已复现，根代理明确授权在隔离 worktree 实施，根代理独占 dev 合并。

依据：LS-v1 `docs/records/2026-11-10-post-core-final-dependencies/gap-matrix-admission-audit.md`；dev `4c446537`。已准入 blocking package 的 readiness 声明检查通过，但原生 cross-port helper 报出 16 AC / 8 命令缺失。差量入口走公共 package 准入，未消费该 helper。

范围：在公共新 package 准入接入既有机械矩阵闭合检查；仅针对明确 required、ready/fulfilled 的现代完整矩阵合同，保持 legacy 无 enrichment 与字符串 AC、off/shadow/blocking、同 generation 复用。不得增加业务推断、自动映射/waiver 或改变运行中的 LS。`rows/scenarios` 形状盲点另账，本批不扩 schema、不改数据。完整 Planner preflight 的 Markdown/角色等路线规则不引入此路径。

验收：

- 用公共 caller 和增量入口复现矩阵遗漏 -> verify: 原代码缺陷测试红；修复后在 canonical materialization 前拒绝并保留具体错误。
- 完整矩阵修订准入 -> verify: 显式和继承端口按实际有效正文检查，补齐 AC/命令/能力关联后通过，不修改旧 generation。
- 保持既有模式/兼容语义 -> verify: off 不新增检查，shadow 只记诊断，blocking 拒绝；旧格式、部分矩阵与同版 replay 正例通过。
- 验证影响闭包 -> verify: dev-verify plan/run、sentinel、相关 deterministic mock E2E 通过，精确记录测试范围；不声明真实 provider E2E。
- 完成代码与记录提交 -> verify: 实现 commit + 含其 hash 的 done 归档 commit，显式 pathspec，worktree 干净后交根代理合并，不 push/merge/删除 worktree。

实施结果：`plan_artifact_package.prepare_plan_artifact_package` 在有效显式/继承端口合并后调用 `plan_package_matrix_admission.validate_package_matrix_closure`。新 helper 93 行，原 caller 仅 6 行接线；已有错误 code/field/message 沿原拒绝事件返回。只适用三 required + ready/fulfilled 完整矩阵；全字符串 AC 且未声明 canonical commands 保留旧路线，混合/损坏 AC 明确拒绝。可选矩阵、同版复用和旧模式保持。

验证记录：

- TDD：原代码在实际公共 caller/controlled gap/bridge 回归 8 failed / 6 passed；首轮另有 3 个 fixture 初始化问题（空 required 列表与缺 git 来源绑定），纠正后才计功能红。初版接线 14/14。补充混合/损坏 AC 防降级 3 红后修正；最终本文件 20 项包含在下列影响闭包中通过。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_plan_package_matrix_admission.py tests/test_plan_artifact_package.py tests/test_plan_candidate_preflight.py tests/test_plan_output_lineage.py tests/test_plan_acceptance_rework.py tests/test_plan_candidate_input_selection.py tests/test_plan_input_lineage_preflight.py tests/test_gap_task_input_delivery.py tests/test_gap_plan_baseline.py tests/test_gap_explicit_replacement.py tests/test_module_gap_plan_runtime.py tests/test_module_gap_task_declarations.py tests/test_writer_fanout_admission.py tests/test_control_actions_recovery.py tests/test_task_plan_consumption.py -q --no-cov -m 'not perf and not host and not real_provider' -n 4 --dist loadfile --tb=short` -> 245 passed，7.05s。
- 同 Python/markers 跑 `tests/test_task_pipeline_generation.py tests/test_task_pipeline_mixed_bases.py tests/test_plan_artifact_ports.py tests/test_sidecar_refs.py tests/test_instruction_stack_contracts.py` -> 45 passed，2.84s。
- 同 Python/markers 跑 `tests/e2e/test_doc152_artifact_package_mock_e2e.py tests/e2e/test_replan_checkpoint_closure_mock_e2e.py tests/e2e/test_kanban_plan_approve_mock_e2e.py` -> 7 passed，9.02s。首次命令误列不存在的 `test_task_pipeline_fanout.py`，collection exit 4、无测试执行；随后改用实际 generation/mixed_bases 测试，未计为产品失败。
- `bash scripts/dev-premerge-gate.sh` -> 53 passed，3.36s；Ruff 三个 Python 路径、`git diff --check` 通过。
- LS 实际 immutable 只读回放：新 helper 对原 package `03143d9c…` 41 ports 拒绝恰好 16 AC / 8 命令；对 R2 冻体 `675a91dc…`、task_map `f3a4c89f…`、9 显式 + 继承共 42 ports 通过。只调用 hydrate/helper，无 prepare writer/emit/store；冻结 hash 前后一致。回执 `/tmp/zf-gap-matrix-real-replay-20261110.json`，helper hash `b8d6c4aef349cfd2ed4e10bc372d895c4c9f486ef431f448356f106472a3fdbc`。只证明既有矩阵 helper 的覆盖维度。
- `python scripts/dev-verify.py plan --base dev` -> 自动选择 273 测试文件，工具超过 80 后扩大为全 `tests`。本批 backend/docs 边界未满足仓库 full suite 条件；`--tests` 实为并集，不能限制该扩大。根代理批准 `run --base dev --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-gap-matrix-auto-tiers-20261110.json` 留痕，再以上述真实闭包替代。两自动 tier 为 `not_run`（excluded by --skip-tier），不将工具 receipt 外层 status 表述为自动测试通过；未改 selector。

限制：未跑全 pytest、真实 provider/host E2E；`rows/scenarios`、PAS journey/task 引用闭合和 `product_delivery` 的 task-local required 覆盖另账。本批只改善新 package 机械准入，不重验历史已准入包、不停止活跃 worker、不证明业务覆盖充分。

待提交实现、填写 hash 后归档；不 merge/push/删除 worktree。
