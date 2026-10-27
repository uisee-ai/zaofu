# 实跑规划缺口修复与 BMAD 复评

> 状态: active

用户已授权修复 fms-v1 实跑确认的缺口，完成后对照 BMAD 再评估。基线 dev `50eb6ba1`；保留真实项目的人工 Plan 审批停点。本批不合并、不推送，也不授权产品实施。

## 范围与验收

- 子进程隔离：修复 Autoresearch / E2E runner 的父项目环境继承 -> verify: 真实临时子进程初始化后，父账本与 TaskStore 字节不变；自定义子状态目录生效。
- 回收恢复：复现并修复已完成 on-demand reader 回收失败 -> verify: 完成→回收→下一次派发自动成功；活动任务仍保留自己的 skill treatment 与恢复合同。
- 审批追踪：对齐已绑定工件的来源/覆盖表示与 Preview -> verify: API及Docker Playwright能读取非零真实映射、保留未覆盖项、拒绝损坏摘要，不从目录猜测或补造完成证据。
- 通用规划方法：动作生产者闭包、最小权限/竞态前态、早反馈、小故事和 assembly本地检查 -> verify: 来源保留与真实对照评估；不将语义质量写成Runtime项目硬规则。
- BMAD复评及收尾 -> verify: 记录本地版本、适用差异、实际改善与未证实项；focused closure、sentinel、相关mock E2E；实现及完成记录本地commit。

## 证据

前轮证据目录：`/home/min/workspace/public/zaofu-evidence/fms-v1-20261026T161210Z`。
已有 `bmad-followups` 分支为独立未合并工作，不修改或重复纳入整批内容。

## 实施进展（2026-10-26）

环境隔离、idle reader 恢复、Review ports/Preview、skills 方法修订均已实施。
评估见 [BMAD 复评](../../docs/records/2026-10-26-planning-gap-bmad-assessment.md)。
机械回归及同输入旧/新代码对照已完成；真实模型 A/B 因 provider workspace routing
失败未取得输出，该项未完成，因此本任务保持 active，不以静态方法评审冒充效果证明。
最终验证收据及实现提交在收尾时记录；无 merge/push/产品批准。

## 验证收据（2026-10-26）

- 144 项初轮 closure；提取恢复模块后 53 项复验；最终 Task Map/方法合同 88 项通过。
- sentinel 50 passed/1 skipped；三流 mock E2E 10 passed。
- 前端 build、typecheck、unit suite 通过；Docker 浏览器 10 个场景通过（长文延长等待复验）。
- 同输入代码前后对照及原 Plan 正文不变证明见外部证据目录 `planning-gap-20261026T2319Z`。
- 真实 provider A/B：两组均 routing discovery failed，无可评分结果。保留 active，未归档 done。

## 2026-10-27 A/B 补跑

容器 CA/用户问题与旧模型不可用已分别复现并修复评估入口。冻结 `50eb6ba1` /
`767afe0d` skills，使用同一 gpt-6.1-terra、同输入，AB/BA/AB 六次评审及一次匿名评分
均完成，输入未改动。严格命中 22/24→24/24，但两条语义边界会使宽松评分持平；
无误报和需求弱化。耗时均值低 7.2%，未缓存输入高 16.5%，不宣称全面更优或更省钱。
评估器 focused 验证 43 passed/1 skipped。

完整方法与限制见[真实 A/B 报告](../../docs/records/2026-10-27-planning-gap-real-ab.md)。
先前 provider 阻塞已关闭；产品新 Plan 生成及 Impl/Verify 不在本次评审实验证明范围。
