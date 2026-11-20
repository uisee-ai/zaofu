# 8×8 真实验证：Reader 重试后受控恢复的双层身份绑定

> 状态: done

实现：`e5f1d26b fix: bind reader recovery to retried transport and durable call`。

来源：用户授权发现问题修复并评估。真实 Run `workflow-055abbd5196f67c6`
两个扫描 Reader 的自动重试失败后，`reader_rebuild_error` 只读预览返回
`reader rebuild operation binding is stale`。原始账本保留于本轮报告。

## 根因与修复边界

基础设施重试保留 durable call 的 active_attempt_id，transport dispatch 和
TaskAttempt 可有新身份。旧准入却要求失败的 run_id（transport dispatch）等于
call active_attempt_id，错误拒绝正确绑定。不得为通过检查改写原始 Operation。

修复沿最新 kernel fanout.child.dispatched 校验失败派发，以及 nested call 中
Operation、workflow Run、request hash、durable attempt。无实际派发的环境等待
仍沿原始 call 身份核验。保持暂停、终态 Run、过期失败和请求 sidecar 校验。

## 验收

- 真实 reader 自动重试并自然耗尽 -> verify: 修复前相同错误可复现。
- 同进程或重建 Orchestrator 后 RM child-rebuild -> verify: 一次新派发、新 Operation，旧失败不变，重复消费无重复派发。
- 伪造/陈旧 transport、call、role、Run、hash -> verify: 全部拒绝。
- 当前真实账本只读复验 -> verify: 合法绑定可准入；未修复宿主之前不得实际派发。

实施说明：[验证记录](../../docs/impl/eight-by-eight-unattended-qualification.md)。

## 已执行验证

- TDD 复现：2 failed / 7 passed，错误与真实账本一致。
- 修复后聚焦链路：40 passed。
- dev-verify plan/run 影响闭包：3,516 passed；不是全量测试。
- dev-premerge-gate：53 passed；启动/重启/多 lane/恢复三链 mock E2E：9 passed。
- 两份真实失败 Reader 的只读绑定检查均通过，原始账本未修改。
- 宿主缺陷未解除，真实恢复与 8×8 后续仍未验证。
