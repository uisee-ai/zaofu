> 状态: done — `6becdc8a` fix: reuse operation inputs within goal dossier builds

# 终态 Dossier operation 投影单次快照复用

本项已获主监工批准，依据用户持续监工、遇问题修复并记录的授权。基线 dev `b5c68f2c`。仅处理已实测的 Dossier operation 重复投影成本；主监工是唯一 dev merge owner。

## 证据与范围

LS-v1 234632 事件终态前缀的纯 build 在 180 秒总预算内未完成（账本读取 4.456 秒、builder 175.220 秒，峰值 RSS 3.85 GiB、0 写入尝试），停在第 14 个任务的 operation 投影。已经重复解析 role_sessions.yaml 78 次、完整 workflow reducer 86 次；原始采样在 `/tmp/zf-dossier-profile-20261113/`。这不是正常完整运行时间，后续不得与完整优化结果计算严格加速比。

当前唯一 active 项：在 `goal_dossier._operations` 的实际 caller 中复用一次角色会话读取、dispatch/workflow operation 事件索引和完整 workflow reducer。保留独立投影 caller、不跨构建缓存、不裁历史、不更改任何身份、授权、终态、合同、claim/source 校验或未知语义。不改 watcher 调度或运行态。

## 验收

- step: 完整小型 fixture 的旧路径与新 batch 路径逐字段对比 -> verify: taskless/active_dispatch_id、共享 dispatch、workflow 准入、重复 operation、顺序与 redaction 相同；仅动态生成时间可规范化。
- step: 实际 Dossier caller 接入单次快照 -> verify: 一轮 YAML load 与 workflow reducer 各一次；完整 Dossier 对等，非只验证次数。
- step: 验证下一次构建与异常数据 -> verify: 新事件/角色会话更新立即可见；缺失/损坏 YAML 的原回退保持，终态/当前 overlay 边界保持。
- step: focused closure + sentinel + 相关确定性 mock -> verify: dev-verify 解释实际闭包；直接 Web/CLI caller、goal_dossier owner delivery、operation/progress 和共享 workflow contracts 通过；不宣称 full suite 或真实 provider E2E。
- step: 优化后同真实 terminal 前缀纯 build -> verify: 8 GiB/180 秒/nice19/写护栏下完整返回，核对结构、全部 task 责任、terminal/source 引用，记录完整耗时与原 cutoff 的口径差异。
- step: 源码/文档/任务提交与归档 -> verify: implementation hash 入 done，worktree 干净，交付 merge-ready；不 merge/push/restart。

说明文档：[Dossier operation 单次快照](../../docs/impl/dossier-operation-projection-snapshot.md)。

## 验证记录

- 红例 8 failed：7 项新 API/接线缺失，实际 Dossier caller 的 YAML load 为 3 次而非 1 次。最后新增流式消费、空 task ID、输出隔离及新 admission 变化；9 项专项通过。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -B -m pytest tests/test_dossier_operation_snapshot.py tests/test_provider_progress_operation_projection.py tests/test_workflow_operation_projection.py tests/test_goal_dossier.py tests/test_goal_dossier_owner_delivery.py tests/test_workflow_operation.py tests/test_run_scope.py tests/test_goal_completion_receipt.py tests/test_tick_services.py tests/test_sidecar_refs.py tests/e2e/test_sqlite_event_ref_operation_mock_e2e.py tests/e2e/test_design199_recovery_mock_e2e.py -q --no-cov` -> 115 passed（7.97 秒），覆盖实际 caller、完整投影、共享身份/结果准入/sidecar 合同、owner delivery 及 mock E2E。
- `bash scripts/dev-premerge-gate.sh` -> manual docs currentness ok，53 passed（3.49 秒）。
- `dev-verify.py plan --base dev` 经 Web server 的传递 caller 扩展为 106 个 test files，机械阈值建议全套。遵循 AGENTS 的实际影响闭包规则，使用上述显式闭包，不因文件数触发全量；未改 selector。`dev-verify.py run --base dev --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-dossier-profile-20261113/verification-receipt.json` 如实记录两宽泛自动 tier 为 not_run；receipt 的 passed 不代表全套测试通过。未做全套 pytest/真实 provider E2E。
- 最终真实纯 build：`/tmp/zf-dossier-profile-20261113/after-final/build_readonly.py`，nice19、180 秒、8 GiB、写入/子进程/网络护栏；固定 terminal 前缀 234632 条（ID 摘要 `b28a5b61afde5f8cb72413558ffbbe9d105ac69fe99051961b7b86c5234a0cd0`）。builder 137.859 秒、总 147.984 秒、峰值 RSS 4188692 KiB，完整返回，0 次禁止写入尝试。
- 与原生产简报对账 11 项全部一致：terminal、source fingerprint、66 task 顺序、92 contract、26 claim 矩阵、全部 event IDs/artifact refs/digests、586 operation IDs/完整 timelines、delivery readiness；原 incomplete/blocked 保留。当前 store/角色字段是实时 overlay，未声称历史完整状态被冻结。小 fixture 完整内容对等，不以次数代替正确性。
- 原 175 秒是带 cProfile 的未完成截断，不能计算严格加速比。137.859 秒仍较长，不宣称 Watcher 延迟根治。剩余阶段与每 operation 的 Store 读取是待独立分阶段测量的问题，本项不扩大源码范围。

所有命令和原始结果位于 `/tmp/zf-dossier-profile-20261113/`，不进入运行态目录。源码仅两个模块，文档由本 task 引用，未修改 MAIN 的他人 AGENTS/index dirty 文件。
