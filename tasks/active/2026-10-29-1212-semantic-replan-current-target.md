> 状态: active

# 恢复重规划绑定当前候选与已接纳 Plan

监工预检发现 P03/P16B 重规划：Task Map 路径已新，但 package/generation 被旧失败 Task 回填，candidate head 仍为 0984aee；当前已集成发布接口为 1451f1b。旧目标没有 BUSINESS_CONTRACT.md，不能用于判断上游修复是否成功。尚未触发错误重规划。

- 从当前 Run 已接纳 package 取得重规划 Plan 身份和对应 source event 的 map -> verify: 未接纳新草稿和旧失败 Task 不能覆盖。
- 从同 Run 已完成集成收据固定扫描提交 -> verify: 其他 Run 不串入；旧失败 snapshot/ref 保留用于根因证据。
- 实际受控 tick 发送正确 discovery -> verify: 回归、影响闭包、sentinel、mock E2E、真实只读预检后再触发。

验证记录：新增回归先 RED（target_ref 错绑 base），修复后当前目标及分支保护两用例通过；原 triage + package 72 passed；相对已验证堆叠基线 c10c7cc 的 dev-verify 影响闭包 769 passed；sentinel 50 passed / 1 skipped。相对 dev 的计划另存审计，前序堆叠补丁已分别验证。真实只读投影确认 P03/P16B 均绑定 1451f1b、已接纳 generation 6b9d432 与 package eeb49df；原失败合同保持不变。
恢复/语义控制 mock E2E 7 passed。
