# 199-03：逐请求责任与 resident 活性

> 状态: done
> 实现: `8666bfdf fix: make recovery scoped, durable, and nonblocking`

来源：[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)；关联 A01；B05；R12。

授权：2026-11-06 用户要求基于 199 在新 worktree 生成 backlog 并实施。
基线：`cff2a8d1`；范围仅 ZaoFu，真实 Provider/宿主部署验收独立记录。

## 实施

区分常驻空闲和绑定请求未履行；历史观察不能答复新请求，重启能恢复当前责任。

## 验收

- 旧观察后新请求无人响应、空闲对照、重放 -> verify: 请求超时可定位并进入既有恢复入口，空闲不误报。
- 修改通过实际 runtime/CLI/briefing 入口生效 -> verify: 调用方回归与影响闭包通过。

## 记录

- 实施：按当前 request ID、receiver、expected_output 对账义务；历史观察不能履行新请求；暂停时长排除，重发不重置期限，超时有既有恢复出口。
- 对应验证：`tests/test_resident_request_obligations.py tests/test_run_manager_resident.py`；最终命令/结果见 [实施记录](../../docs/impl/199-worker-recovery-implementation.md)。
- 本轮关闭的是上述工作包验收，199尚存缺口不计作完成；针对性回归及调用方检查通过；广域验证无新增失败、仍有6项隔离复现的基线失败，详见实施记录；实现与验证证据已记录；真实 Provider/宿主及完整199剩余义务按覆盖矩阵单独跟踪。
