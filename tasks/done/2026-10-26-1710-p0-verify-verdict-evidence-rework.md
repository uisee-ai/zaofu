# P0 Verify semantic verdict 与 evidence gate 分层

> 状态: done

## 问题
Verify 已产出业务 rejected verdict 时，事件格式问题被 EventSchemaD 改写为 `discriminator.failed`，triage 反复归类为 evidence_payload_gap，导致真实业务返工没有进入新的 Impl generation。

## 范围
- 保留 admitted verification result 的 semantic verdict（passed/rejected）。
- EventSchemaD 失败只能作为 evidence defect，不得吞掉已 admission 的业务结果。
- rejected verdict 必须触发一次可追踪的 semantic rework 路由。

## 验收
- step -> verify: 构造 rejected verification result + malformed child event，验证 semantic result 仍可查询且 rework generation 被请求。
- step -> verify: 重放同一事件不重复创建 rework operation。
- step -> verify: 现有 schema gate、verification admission、task pipeline tests 全部通过。

## 完成记录
- 实现提交: `fb04e29 fix: close P0-P1 runtime recovery gaps`
- 验证: 聚焦回归 122 passed；resident sidecar 6 passed；全量确定性层 11632 passed、32 failed、19 skipped，失败为工作区既有 fixture/config/阈值问题。
