> 状态: proposed

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
