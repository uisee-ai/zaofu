# 局部Reader恢复计数与升级不能污染整个Run

> 状态: done — 21daa832 fix: isolate task reader recovery caps and escalation

用户授权监工修复。LS真实evt-07deea6b2c05是FMS17的单文件范围发现，但_same_replan_scope只比Run/feature与stage generation，把同Run其他任务失败计入cap。evt-a4308aa39609升级后evt-78a34107f8f5阻断整Run，取消无关FMS12 Reader。原缺口已通过canonical CAS修复，运维审核后用官方goal set active恢复evt-2b756c530dcd；该操作不证明通用缺陷修复。

- 从已准入原Task Pipeline失败及其rework因果链识别局部恢复 -> verify: 同Run不同Task/operation失败不互相消耗cap，不能以报告自由文本或affected IDs伪造作用域。
- 同一失败链重复Reader失败仍有界 -> verify: 原cap与legacy计划准入行为保留；其他任务成功不能清零本任务计数，跨Run/缺少准入/因果环不授予局部边界。
- 局部cap升级保留operator请求但不terminalize整Run -> verify: 通过实际Orchestrator入口，其他任务/父Task仍可运行；真正全局Plan准入耗尽仍阻断。
- focused/影响闭包/sentinel/mock验证后合并部署 -> verify: 固定历史回放与现场原任务恢复，记录代码效果和手动干预边界。

关联[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)、[实现说明](../docs/impl/reader-task-recovery-scope.md)。

已复现并通过focused 60项，包含实际Orchestrator与周期收敛入口。真实153771条事件前缀只读回放：旧cap_exhausted，新task-local attempt 1。线上运维恢复已实际拉起7Impl+1Verify，通用代码尚待影响闭包、合并与部署。

验证：自动planner选择293个影响测试文件，但因>80规则尝试扩大为全套；已停止误用skip-tier的启动，按仓库规则显式运行这293文件，4066通过7跳过（97.27秒），不是全套。dev-verify run --skip-tier deterministic执行serial10通过。末次RM side门禁变更后247定向（含199/terminal mock E2E）和sentinel53通过。源码无provider/host行为变更，未跑真实provider E2E。

交付：实现21daa832合并dev96566a8c；合并后23定向通过。Watcher与Web已加载96566a8c，Web健康/UI200，受控重载保持10个在途operation/attempt/dispatch/pane。详细证据位于LS docs/records/2026-11-08-reader-recovery-scope/。本轮实际业务恢复在部署前由运维完成，代码通过真实历史只读回放及入口回归证明，尚无新自然cap耗尽样本。

批次审计：主仓库8项既有候选均为defer，无待清理proposed；保持其具体触发条件，不因本修复宣称它们完成。未push。
