> 状态: active

# OA 上下文引用在提交前校验

用户授权：持续监工 LS-v1，发现、修复并记录问题；沿用本地合并授权，不 push。

真实事件 `evt-284a319c82fd` 已获 call-result 准入并结算 operation，随后
`evt-b12fb2a55160` 因旧 task map 引用不在当前 manifest 而拒绝。dev
`152c6d30` 对同一真实结果的提交前校验仍 passed。当前为 shadow，不阻塞 Impl。
完整证据保存在 LS-v1 `docs/records/2026-11-08-oa-context-route-preflight/baseline.json`。

## 范围与验收

- 对 `run_plan.context_routes.required_sources` 复用当前 operation 的冻结 ref/sha
  边界 -> verify: 旧版本、未知 ref、相同路径错误 digest 在提交结算前拒绝。
- 返回可操作诊断，保持原 operation 可修正 -> verify: validate 无状态写入，错误
  submit 无 canonical result/settled，原 operation 修正后只准入一次。
- 保留 apply 时防线 -> verify: 历史越界结果仍被拒绝，合法来源仍可编译/派发。
- 简报明确复制旧计划时重核当前来源 -> verify: 简报及相关调用者聚焦测试。
- 记录原因与边界 -> verify: 更新实现文档；不篡改真实历史拒绝，不重跑全局计划。

根因归属：OA 复制旧引用；ZaoFu preflight 只覆盖恢复 directives，遗漏 run-plan
routes；Kernel apply 正确拒绝。监工此前误把 call-result 准入描述成审核成功，
已纠偏，并补观察脚本漏掉的 semantic rejection 类型。

实现说明见 [上下文引用提交前校验](../../docs/impl/oa-context-route-preflight.md)。

## 验证记录

- 当前 HEAD 真实结果只读回放：原 preflight passed，但95条来源越界；修复后
  failed、95条准确诊断。历史结果/事件未修改。
- 新回归先 RED：3 failed（未知ref、错误摘要、旧版本均未拒绝）。
- 聚焦15 passed；`dev-verify.py plan/run --base dev` 选择46个测试文件，
  deterministic 771 passed / 7 skipped；sentinel 53 passed；mock flow 10 passed。
- 未运行全量 pytest 或真实 Provider E2E。业务 worker 独立继续，不计作本修复验证。
