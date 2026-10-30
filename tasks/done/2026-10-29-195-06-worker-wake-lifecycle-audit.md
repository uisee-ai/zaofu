> 状态: done — c205ffe8 feat: add worker execution envelope and wake routing

# 195-06 Worker Wake 与生命周期对账

## 目标

把 Kimi background notification、TowerSend、TaskTerminated 和 `unknown != idle` 的借鉴
固定为 ZaoFu 的可验证接线，避免 Task Pipeline worker 完成后 OA/WRC 不被唤醒，或 idle
状态被错误推断。

## 范围

- envelope 的 `wake_contract` 显式列出 stage success/failure、`worker.stuck`、
  `worker.respawn.failed` 和 `worker.probe.idle` 路由。
- 校验这些事件仍注册在 known types/wake surface；只读审计不新增第二个唤醒器。
- 补充 worker completion、resume、idle probe 和 unknown gate 的 focused tests。
- 文档明确 CoT 仅为 sidecar/诊断，typed result/evidence 才能进入 admission。

## 验收

1. 每个 stage envelope 的 wake contract 与现有 wake surface 一致 -> verify: focused envelope/wake tests。
2. worker result、stuck、respawn failure 和 idle probe 均能触发既有 EventWatcher route -> verify: `tests/test_watcher_wake_worthy.py`、相关 worker lifecycle tests。
3. unknown worker 不满足 integration/merge/delivery idle gate -> verify: lifecycle/dispatch gate tests。
4. 不新增 polling service 或绕过 EventLog -> verify: static source audit and sentinel gate。

## 非目标

不更改真实 provider 的取消时延和不把 periodic sweep 改成第二 scheduler。

## 实施记录

- 将 Task Pipeline generation、verify completed/failed、acceptance completed/failed 加入既有 `WAKE_PATTERNS`，让 typed stage result 直接唤醒 Kernel/WRC；没有增加 polling service。
- envelope 校验 known event 与 wake surface 一致，并保持 `unknown_is_not_idle=true`、unknown state 为 hold；既有 idle probe、stuck 和 respawn failure 路由保留。
- 验证：`uv run pytest tests/test_wake_surface.py tests/test_worker_execution_envelope.py tests/test_task_pipeline_capsule.py tests/test_task_pipeline_generation.py tests/test_task_pipeline_briefing.py tests/test_wire_reactor_registry.py tests/test_task_pipeline_worker_liveness.py -q --no-cov` -> 84 passed。
- `bash scripts/dev-premerge-gate.sh` -> 51 passed；结构、事件合同和文档 currentness 通过。
- dev-verify deterministic 广域运行记录 13,319 passed、20 skipped、28 deselected；5 个失败来自当前 checkout 缺失 `tools/export-public.sh` 和 prompt fixture，与本批变更无关；real-provider/host tier 未执行。
