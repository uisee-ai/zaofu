# Gap 准入失败进入有界恢复

> 状态: done

实现：`e20431d6 fix: recover rejected gap amendments through bounded reader retries`。

来自用户2026-11-07修复卡顿并记录的明确授权；产品监工总记录见
`tasks/active/2026-11-07-0200-two-gap-supervision.md`。

## 故障证据

`evt-5d3a7ecf2bfa` 和 `evt-96bc459ca094` 的 task_map.amend.failed 分别记录
assembly owner冲突与rolling_smoke_command_missing。原reader看见gap.ready即停止
恢复；Run Manager动作receipt通过但产品不推进，最终orphan升级和静默。

## 验收

1. 明确因果链准入失败 -> verify: 原reader有界重试包含拒绝reason与原gap refs，实际RunManager apply发出一次恢复trigger。
2. 无恢复额度 -> verify: 真实reactor入口走既有cap升级，不等待orphan，不放宽额度。
3. 防重复与隔离 -> verify: pending/admitted不重试，显式跨run拒绝，子trace不误当canonicalrun，已接管旧失败不复活，无关新计划不吞掉本gap。
4. 原语义/门禁保持 -> verify: 不修改TaskStore和不可变Plan，不把gap提案当产品完成；全部既有验证门禁保持。

## 实现与验证

待focused回归、sentinel及相关deterministic mock E2E完成后记录实现commit并归档。

验证已完成：初始RED为4个失败，新增真实reactor入口RED为5个失败；实现后直接闭包328 passed，最终接线23 passed，sentinel 50 passed/1 skipped，三流deterministic mock smoke 10 passed。
`dev-verify plan --base dev --paths <本批5个源码/测试路径>`保守选择230文件并扩大至全量；按仓库测试约束未执行全量，以上直接闭包替代该tier。`dev-verify run`同paths显式`--skip-tier deterministic`执行其余自动tier：serial 10 passed、sentinel及mock smoke通过，收据 `/tmp/zf-supervision-20261107/gap-verification.json`。这是focused验证，不是全量或真实provider E2E。

真实历史只读回放：两个amend拒绝均可识别；原历史已耗尽stage额度不绕过，当前operator接管后均no-op，保持当前Planner唯一修订所有权。
