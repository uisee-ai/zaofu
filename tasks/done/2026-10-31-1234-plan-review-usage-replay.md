> 状态: done

实现：`5462925f fix: pin usage observations across plan review replay`

# Critic 延迟派发的用量快照重放

监工复现真实 dl Critic 请求因 Run 暂停而延迟，第二次准备仅 `usage_observations.unattributed_project_samples` 从55变56，即造成 request_hash_divergence；旧操作保持 blocked。用户授权修复、合并 dev、恢复并继续监工。

- 同一操作首次用量快照固定 -> verify: 两次 prepare_call_operation 的 source manifest 和 request hash 相同，可重放；不是忽略所有输入差异。
- 保留语义身份保护 -> verify: task graph/候选等非用量内容变化仍拒绝，原证据篡改拒绝，新操作看到当前用量。
- 接入实际准备调用入口 -> verify: 直接调用者、Event/Operation/Artifact契约、mock流程和合并哨兵通过；不放宽预算、不改写旧blocked。

验证：基线1失败2通过；修复后4项重放/新操作/内容变化/篡改回归通过。18文件调用者与 Event/Operation/Artifact/Context/mock OA闭包230项通过，自动10 serial +51 sentinel通过；Ruff、diff check通过。保守选择器触发222文件全套回退，本次以显式影响闭包替代deterministic全套层，未运行全量pytest。同步将旧 generation 不固定为新输出身份的既有测试更新为真实可读取Package fixture，保持原断言。

真实证据：两个Critic source manifest仅执行评审源不同；两个评审正文唯一差异为未归属用量样本55→56。旧操作 `wop-prd-plan-synth-trig-evt-d22c30da-c673ee748cb1` 保持blocked，不覆盖既有摘要或失败结论。日志 `/tmp/zf-review-usage-{baseline,closure,auto}-20261031.log`。
