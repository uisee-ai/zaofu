> 状态: done — c205ffe8 feat: add worker execution envelope and wake routing

# 195-05 Worker Execution Envelope

## 目标

借鉴 Kimi Agent/Tower 的 admitted worker briefing，把 role/profile/model/thinking、
worktree、result contract、resume 和 wake 语义冻结为一个可校验的
`worker-execution-envelope.v1`，接入现有 Task Pipeline dispatch 和 briefing。

## 范围

- 新增 provider-neutral envelope builder/normalizer/digest。
- dispatch event、operation payload 和 worker briefing 使用同一 envelope。
- envelope 不能把 `unknown` 当作 `idle`，不能缺少 exact target、result contract 或 evidence 要求。
- 不新增 scheduler、Task schema 或独立 canonical store。

## 验收

1. `build -> normalize -> digest` 可重放且 digest 稳定 -> verify: `tests/test_worker_execution_envelope.py`。
2. 缺失 identity、workspace、result contract、wake contract 或将 unknown 视为 idle 时 fail closed -> verify: envelope contract tests。
3. 实际 `dispatch_task_pipeline_stage` 把 envelope 写入 `task.pipeline.stage.dispatched` 和 operation request -> verify: `tests/test_task_pipeline_capsule.py` caller assertions。
4. worker briefing 展示与 event 相同的 envelope -> verify: `tests/test_task_pipeline_capsule.py` caller assertions。

## 非目标

不把 raw CoT 写入 envelope；不改变默认 `normal_frontier_owner`；不启动真实 Provider。

## 实施记录

- 新增 `worker-execution-envelope.v1` 的构造、稳定 digest 和 fail-closed 校验，绑定 identity、routing、workspace、result contract、resume 与 wake contract。
- Task Pipeline operation payload、`task.pipeline.stage.dispatched` 和 worker briefing 使用同一 envelope；transport context 传递 envelope digest。
- 验证：`uv run pytest tests/test_worker_execution_envelope.py tests/test_task_pipeline_briefing.py tests/test_task_pipeline_capsule.py tests/test_task_pipeline_generation.py tests/test_watcher_wake_worthy.py tests/test_proactive_idle_dispatch.py -q --no-cov` -> 59 passed；扩展 lifecycle/wake 闭包 -> 84 passed；ruff、compileall 通过。
