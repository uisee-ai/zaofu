> 状态: done — ed2f6a81 feat: bind OA profiles and frontier ownership

# 195-02 正常前沿 owner 护栏

## 目标

把 195 的“正常执行由 WRC/KERNEL 推进、Run Manager 只负责恢复”落成一个可审计的配置契约；本批次只建立 owner declaration 和 fail-closed 诊断，不提前迁移现有 Run Manager continuation 实现。

## 范围

- 在 `workflow.orchestration` 增加 `normal_frontier_owner`，值为 `run_manager` 或 `wrc`，默认 `run_manager`。
- 提供单一 resolver，供正常 frontier/continuation 入口检查 owner。
- 当声明 `wrc` 但尚无 WRC consumer 时，Run Manager 不得静默执行 continuation，而是写入可重放的 blocked/diagnostic event。
- 根配置保持 `run_manager`，避免在 WRC consumer 尚未落地时改变生产路线。

## 验收

1. 配置只接受 `run_manager`/`wrc`，默认兼容 194 -> verify: loader config tests。
2. owner resolver 是唯一读取点，未知值 fail closed -> verify: focused owner contract tests。
3. `wrc` 声明不会被 Run Manager 静默消费 -> verify: read-only continuation regression test 检查 diagnostic event 和无执行 event。
4. 当前默认路线仍由既有 Run Manager 运行 -> verify: 194 continuation mock E2E 与 focused runtime tests。

## 实施记录

- 已在 loader/schema 增加 `normal_frontier_owner`，只接受 `run_manager`/`wrc`，默认保持 194。
- Run Manager 对 `wrc` owner 只投影并记录幂等 `workflow.normal_frontier.owner_blocked`，不执行 continuation。
- 验证：owner fence 与既有 read-only continuation tests 通过；默认 Run Manager 路线回归通过。
