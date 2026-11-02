# LS 身份、设计和依赖图的受控计划修订

> 状态: active

## 证据与范围

LS 的 `docs/records/2026-11-02-identity-design-complete-requirement-mapping.md` 核对原稿、PRD 和冻结 R2；用户决定 `evt-0fb9e2b35adb` 尚未进入该快照合同。设计 runner `c08f04c`、窄屏修复 `d2ffbf6` 未进入正式 Candidate；不得用重试旧合同代替修订。

## 验收

1. 原需求与用户决定逐项对账 -> verify: 身份、SESSION、GATEWAY/POLICY、ASSEMBLY 的任务级与最终验收各有 owner 和来源；本地用户用于普通测试，最终真实认证授权及适用目录决定保留。
2. 核查设计共享工具 owner、六类预览与最终验收及依赖图 -> verify: 仅保留有证据的依赖，旧新任务映射完整，runner/样式候选通过独立验证并受控集成。
3. 新计划任务级输入切片经 Planner/Critic 独立审查 -> verify: 来源版本、必要条款和候选全量验收可追溯；不修改在途 manifest/receipt/TaskStore。
4. 受控恢复 -> verify: 新 generation、实际 operation/attempt、独立 Verify 和 Candidate 收据可追溯；未获得证据前不声明任务完成。

## 恢复前核验（2026-11-02）

- 现有 Planner operation `wop-prd-plan-planner-trig-evt-698cbb36-rework-5954c54f40a6` 仍有活动租约，`zf recover workflow --dry-run` 将其标为 `no_action`；不能重派或伪造新代审批。
- 观察到 Planner 已从此前候选 13 继续读取 candidate 15 的产品验收规格；读取/heartbeat 不等于 Plan、Critic 或任务完成。原身份、设计两项仍需新的准入合同及独立验证。
