> 状态: done — `e65843db` fix: avoid repeated list scans in dossier source refs

# Dossier 来源引用的线性成员判断

主监工依据用户持续监工、遇到问题修复并记录的授权批准唯一修复项。基线 `e5d03f89`，独立 worktree 实施，主监工唯一 merge owner；不 merge/push/restart。

## 实测与范围

一次低开销纯 build 分阶段计时：同 `evt-0a9994064383` 的234632事件前缀，builder128.201秒，其中 `_source_refs`60.098秒（46.9%），产出79136refs/3325digests；当前函数逐个使用增长列表成员判断。operation已为4.808秒；TaskStore535次get仅1.266秒，均不在本项范围。证据 `/tmp/zf-dossier-phase-20261113/{summary.json,diagnosis.md}`。

只给 refs、digests 各增加 seen set，仍向原输出列表按首次出现追加。保持原 `_walk` 深度、`_strings`、secret key过滤、digest优先级和来源指纹；不改 redaction、Store、跨构建缓存、完整事件范围或状态机。

## 验收

- step: 原算法oracle与完整返回对比 -> verify: 重复、refs/digests同值分域、首次顺序、字典/数组、空白、深度、secret及digest优先级一致。
- step: 大量唯一引用回归 -> verify: 用字符串比较次数约束消除列表的二次增长，不用墙钟阈值代替正确性。
- step: 实际Dossier caller及共享证据contract闭包 -> verify: focused、sentinel及相关mock通过，记录dev-verify选中范围与未执行的广泛tier。
- step: 同terminal前缀真实完整build -> verify: 只跑一次，nice19/180秒/8GiB/禁写护栏下完成；11项生产对账相同，包括全部事件引用、66task、92contract、26claim、586operation timelines。分开记录原profiler截断、前次完整优化构建和本次phase计时口径。
- step: commit、归档与clean handoff -> verify: done绑定实现hash、工作树干净、不改MAIN及LS运行态；明确本修复不证明当前live tick延迟根治。

实现说明：[Dossier投影单次快照与引用去重](../../docs/impl/dossier-operation-projection-snapshot.md)。

## 验证与边界

- 原算法oracle与完整Dossier两项先通过；复杂度红例2048个唯一引用触发2096128次比较。修复后3项专项全部通过，保留完整输出及输入不变，不用性能指标代替正确性。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -B -m pytest tests/test_dossier_source_refs.py tests/test_dossier_operation_snapshot.py tests/test_provider_progress_operation_projection.py tests/test_workflow_operation_projection.py tests/test_goal_dossier.py tests/test_goal_dossier_owner_delivery.py tests/test_workflow_operation.py tests/test_run_scope.py tests/test_goal_completion_receipt.py tests/test_tick_services.py tests/test_sidecar_refs.py tests/e2e/test_sqlite_event_ref_operation_mock_e2e.py tests/e2e/test_design199_recovery_mock_e2e.py -q --no-cov` -> 118 passed，8.06秒。
- `bash scripts/dev-premerge-gate.sh` -> manual docs currentness ok，53 passed，4.02秒。
- `dev-verify.py plan --base dev` 传递caller选中103个test files，机械阈值建议全量；按AGENTS实际影响闭包规则采用上述显式caller/共享证据contract/mock闭包。未改selector；`dev-verify.py run --base dev --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-dossier-refs-20261113/verification-receipt.json` 两广泛自动tier如实not_run，不表示全套通过。未执行全套pytest或真实provider E2E。
- 只运行一次最终真实低开销phase build：`/tmp/zf-dossier-refs-20261113/build_phases_readonly.py`，同234632事件terminal前缀，nice19/180秒/8GiB/禁写护栏。`_source_refs`4.080秒（前一相同phase口径60.098秒），仍返回79136refs/3325digests；完整builder74.083秒、总82.821秒、峰值RSS4194052KiB、0禁止写入尝试。
- 与原生产简报11项对账全部一致：terminal/source fingerprint、66task顺序、92contract、26claim矩阵、全部event IDs/artifact refs/digests、586operation IDs和完整timelines、delivery readiness；原incomplete/blocked不变。原cProfile175.220秒是未完成截断，前次137.859秒是无profiler完整构建，本次是phase计时，不计算跨口径严格加速比。
- 源码仅`goal_dossier._source_refs`，没有改redaction、TaskStore、状态、授权、控制面或Worker。实时没有新增terminal时的tick长周期，不能用本离线样本解释，仍需单独live采样。

原始结果：`/tmp/zf-dossier-refs-20261113/{closure.log,sentinel.log,summary.json}`；红绿日志在`/tmp/zf-dossier-phase-20261113/{refs-red.log,refs-green.log}`。当前任务验证输出不进入运行态，交付后由主监工决定merge与加载。
