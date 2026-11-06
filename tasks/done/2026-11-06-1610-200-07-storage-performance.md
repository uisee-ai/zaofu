# 200-07：存储、监督与容量资格

> 状态: done

来源：200 §4.2/§4.3/§6.1/§12/§13；199 R14/A05/B02/C03/C04。
授权：2026-11-06 用户要求在 199 worktree 完成 200，并在测试通过后用真实 Codex 和 v5 YAML 验证、修复与清理。

## 实施

隔离 Event/Store/sidecar 故障窗口；有界成对性能测量；独立宿主监督尊重停机意图；覆盖现有 1/1、3/2、8/8、12/4 调度和成果复用。

## 验收

- 中断持久化、阻塞调用、终止隔离控制面并恢复 -> verify: 证据不丢失或明确 unknown，单有效实例，监督可推进；记录 p50/p95、RSS 和执行机会，不通过扩容或新增总时限伪造进展。
- 实际入口及影响闭包 -> verify: 对应调用方测试与运行证据通过，未达成验收不归档 done。

## 证据

实施/测试/真实运行证据持续记录于 [200 实施记录](../../docs/impl/200-agent-recovery-implementation.md)。

## 完成回执

实现：`b1b39758 feat: bind agent recovery steps, guidance, and shared effects`。
后续修复：`768adb08 fix: retain resident ownership across controller restarts`。

验证：Event/Store/sidecar 故障窗口、容量组、成对 p95/RSS、serial 10 passed；真实 host 回归两类场景分别通过，最后含 resident 的 1 passed（14.22s）。

实际效果与范围：SIGKILL 后唯一控制面、resident 不重复；pause 阻止重启，正常 stop 清掉进程组。修复 768adb08；8+8 故障吞吐未作为本轮实机资格。

证据归档：`f3fe9ec4 docs: record design 200 real workflow validation`；见 [验证报告](../../reports/2026-11-06-200-recovery-validation/README.md) 与 [31 项对账](../../reports/2026-11-06-200-recovery-validation/design-traceability.md)。
