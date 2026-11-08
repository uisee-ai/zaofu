# 连续部分交付恢复：保留已验证的合成基线祖先链

> 状态: done；实现 `989e8118` — `fix: preserve partial work across repeated recovery rounds`，dev合并 `2d376f00`。

LS-v1 FMS14-03于10:16:27真实派发失败evt-aa13d2fd494f。上一轮partial b060的base230b本身是Candidate与获准partial合成的未发布基线，不能直接作为当前Candidate祖先。现有partial_successor_proof拒绝合法第二轮部分交付。用户已授权持续监工、修复并记录。只在独立worktree修改ZaoFu，不修改LS canonical状态。

- [x] 固定真实事件与Git来源复现 -> verify: 当前HEAD proof为空及dispatch_deferred证据。
- [x] 逐层证明相同Run、正式失败、完整单前驱、合成父提交和树内容，收集全链获准partial补丁 -> verify: 两轮恢复真实Git测试、伪造树/父/跨Run/缺失准入/范围回归均fail closed。
- [x] 真实dispatch与Candidate integration调用复用证明 -> verify: 新Candidate依赖、两轮partial及最新worker delta均保留，partial未提前发布，冲突仍拒绝。
- [x] 影响闭包、sentinel、mock E2E通过后提交归档合并dev，受控重载并恢复真实任务 -> verify: 实际派发18 partial路径与8 producer路径完整；记录自动恢复缺口和验证边界。

资料：LS docs/records/2026-11-08-recursive-partial-recovery/baseline.json；运行说明见docs/impl/recursive-partial-recovery.md。不开全局replan，不减AC，不push。

## 验证

旧HEAD新用例1fail/7pass；实现后54聚焦通过，追加真实dispatch/集成后11通过。dev-verify九文件影响闭包97passed；partial continuation与mock flow/design199 E2E共18passed；sentinel53passed。日志/tmp/zf-recursive-{red,focused,caller,verify,extra,premerge}.log，receipt /tmp/zf-recursive-receipt.json。未运行完整pytest或真实Provider E2E。

真实LS Git预检14个获准partial提交，18 partial文件和8 producer文件保全，生成未发布合成对象16a064cb，所有refs未移动。pending真实加载及派发，不提前声明恢复完成。

## 实际部署及派发

10:27:10–10:27:48受控控制面重载，watcher1760463→1802374，加载2d376f00。10:27:45 Kernel自动派发evt-2859e8396cff，operation wop-impl-tp-FMS-14-GAP-CONTINUE-03-0c8bdc-3cb6d67d6473，attempt ta-e347950219325d8b31a9。实际工作区base16a064cb，父提交952a7a7f与b060afa；18 partial路径与8 producer路径全等，partial未发布进Candidate。最终49聚焦+53sentinel合并后通过。

producer完成到恢复派发698.948秒包含发现、源码修复、验证及部署时间，不能称无阻塞自动派发。回执LS docs/records/2026-11-08-recursive-partial-recovery/controlled-reload.json和work-node-revision-recovery/consumer-actual-dispatch-check.json。

operator初版检查误将producer原commit必须为Candidate祖先；Candidate采用cherry-pick，已改为准入integration receipt绑定与逐文件blob比对，记录为监工探针误判，不算产品丢代码。本任务done只指运行时修复和实际恢复，LS作业本身仍实施中。
