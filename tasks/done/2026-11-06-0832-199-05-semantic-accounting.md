# 199-05：语义失败计数与恢复身份分离

> 状态: done
> 实现: `8666bfdf fix: make recovery scoped, durable, and nonblocking`

来源：[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)；关联 §5/§6；R02；B04。

授权：2026-11-06 用户要求基于 199 在新 worktree 生成 backlog 并实施。
基线：`cff2a8d1`；范围仅 ZaoFu，真实 Provider/宿主部署验收独立记录。

## 实施

以实际准入失败核算返工；operation generation 保持身份用途，协议修复/传输重试不能算产品失败。

## 验收

- 构造身份代次跳跃、协议修复、真实语义失败 -> verify: 耗尽依据真实失败证据且历史可追溯。
- 修改通过实际 runtime/CLI/briefing 入口生效 -> verify: 调用方回归与影响闭包通过。

## 记录

- 实施：按已准入实际语义失败去重计数；generation 只作身份；环境/传输输入错误不消耗产品返工。跨 Task ID 同根因继承仍属199剩余义务。
- 对应验证：`tests/test_task_pipeline_failure_accounting.py tests/test_task_pipeline_impl_exhaustion.py tests/test_task_pipeline_reconciler.py`；最终命令/结果见 [实施记录](../../docs/impl/199-worker-recovery-implementation.md)。
- 本轮关闭的是上述工作包验收，199尚存缺口不计作完成；针对性回归及调用方检查通过；广域验证无新增失败、仍有6项隔离复现的基线失败，详见实施记录；实现与验证证据已记录；真实 Provider/宿主及完整199剩余义务按覆盖矩阵单独跟踪。
