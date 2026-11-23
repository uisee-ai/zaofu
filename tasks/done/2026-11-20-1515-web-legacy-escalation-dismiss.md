# 8×8 真实验证：接通旧升级记录的受控关闭入口

> 状态: done

实现：`87456cd1 fix: expose guarded legacy escalation dismissal through Web`。

来源：本轮用户已授权修复实际阻塞并从断点继续。宿主沙箱修复后，原始 Run
`workflow-055abbd5196f67c6` 仍有 `evt-529fed0673db` 升级记录阻塞恢复。
该记录没有 checkpoint 或 approval_command，属于已有 legacy dismiss 兼容范围。

## 根因与边界

ControlledActionService 已要求精确事件、操作者授权引用、处理理由；但 token-gated
Web 入口先拒绝所有没有合同字段的升级记录，未接入已有安全路径。
只为 dismiss 委托已有实现；其他批准动作仍拒绝。保留鉴权、时效和重复提交检查，
关闭记录不代表任务恢复成功，也不改写旧失败或批准新的修复动作。

## 验收

- 从真实 Web 受控入口提交完整旧记录关闭 -> verify: 一次 acknowledge，RM 消费后不再 pending。
- 错误 token、错误/缺少事件、缺少授权引用或理由 -> verify: 拒绝且无 acknowledge。
- legacy 记录用于批准恢复、重复关闭 -> verify: 拒绝。
- 成功关闭 -> verify: 不产生 run.resumed、repair.action.requested 或 task.done。
- 当前真实宿主故障已修复 -> verify: doctor 普通用户探针通过；只关闭精确旧记录，继续观察实际新派发。

实施说明：[8×8 验证记录](../../docs/impl/eight-by-eight-unattended-qualification.md)。
测试日志：`reports/eight-qualification-20261120T122929Z/legacy-dismiss-*.log`。

## 验证结果

- 修复前真实 Web 入口回归：1 failed / 5 passed，复现 contract verification 拒绝。
- `dev-verify.py plan/run --base dev --tests tests/test_web_legacy_escalation_dismiss.py`：
  4,565 passed，包含 RM 恢复 mock；Web typecheck 与 unit 全部通过。这是影响闭包，不是全量测试。
- `scripts/dev-premerge-gate.sh`：53 passed。
- 真实关闭回执 `evt-fe84326b9e9c`；RM 消费 dismissed 后 pending 清空，并自动启动
  新诊断 `rmd-6a493af283c90661`。该入口修复已生效，Reader/业务完成另行验收。
