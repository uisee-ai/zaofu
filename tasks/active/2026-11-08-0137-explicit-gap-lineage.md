> 状态: active

# R63：恢复计划尊重显式空替代集合

用户授权持续监工、发现问题即修复。LS-v1 44 项恢复计划在审批前对账失败：终端消费者已经独占声明替代关系，但 `_enriched_gap_tasks` 仍将同 parent 的汇总替代集合复制给前置任务；补测还发现显式空列表也被覆盖，导致新增错误替代身份及四条下游依赖。未审批错误候选，历史工件保留。

- [x] 复现真实受控 gap 恢复入口 -> verify: 生成的前置任务保持空替代，终端消费者独占替代，原下游只连终端。
- [x] 保留旧格式兼容 -> verify: 缺省字段的 parent 匹配和单任务 envelope 回退继续通过；输入对象不被修改。
- [x] 核验影响闭包 -> verify: focused、dev-verify、sentinel、相关 mock E2E 通过。
- [ ] 合并并恢复 -> verify: dev 部署后新受控候选与独立审核稿逐对象相同，正常审批、实际派发，原在途 Verify 保留。

未纳入 R62 历史诊断投影修复。不得修改运行时工件或读取回执来伪装一致。

验证记录：原 HEAD 两项新增回归均失败（真实受控 caller 已复现，不是仅 helper）。修复后影响闭包 196 文件 2792 passed；最终复用既有替代身份集合 helper 后再跑 focused+mock 91 项及 sentinel。dev-verify 自动把超过 80 文件升级全套，与仓库只按影响闭包规则冲突，本轮 `run --skip-tier deterministic` 保留 serial 层，另行执行所选 196 文件；没有声称全 pytest 或真实 Provider E2E。首次 sentinel 发现 module_gap_plan 增至 1002 行，改用已有 `_superseded_task_ids` 去除重复集合提取后 999 行，门槛未调整。
