> 状态: done

# 已替换计划不再保留审批等待

用户授权：持续监工 LS-v1、修复并记录问题，沿用本地合并授权，不 push。

dev `12b2721f` 真实只读回放仍列6条 pending；每条均有同Run、同包ref/digest
的 Kernel `plan.artifact_package.superseded`，及同cause/slot的后继包admitted。
证据：LS-v1 `docs/records/2026-11-08-plan-approval-retirement/baseline.json`。
当前Verify没有被阻塞，但RM failure-closeout与goal idle调用此投影，可能误等待。

- 消费显式Kernel替换事实 -> verify: 同Run/ref/digest及后继准入匹配后，旧请求
  不再pending；迟到旧请求也不复活；不同Run/摘要/路径/slot或未准入后继不能消除。
- 统一读侧与决定入口 -> verify: CLI pending、idle driver及锁内operator决定
  使用同一待审批集合；过期approve/reject不产生事件，不触发replan。
- 保留新请求、Owner未决及历史事实 -> verify: 新包未审批仍等待，跨Run保留，
  不伪造旧plan.approved/rejected，不修改任务/Owner状态。
- 聚焦回归、sentinel、mock flow -> verify: 合并后复验及真实LS6→0只读对账。

原因归属：Kernel发布了替换事实，但approval reducer只收approval/rejection和
同内容重复批准，没有消费package supersession；不是OA遗漏业务语义。本次
不通过手工reject旧请求清理，防止意外回到全局replan。

实现文档：[替换计划审批退休](../../docs/impl/superseded-plan-approval-retirement.md)。

## 验证

- 真实HEAD基线6条pending均有显式替换证据；修复后只读回放为0，Owner未决保持。
- 新回归先5 failed / 9 passed；随后聚焦38 passed，再补2个伪造来源/后继摘要负例。
- `dev-verify.py plan/run --base dev` 74文件影响闭包1349 passed。
- planner分类backend未自动附加门禁；按恢复/控制影响手动补sentinel53 passed、
  mock flow10 passed、串行恢复2+文档8共10 passed。
- 无全量pytest、无真实Provider E2E，未push。

实现提交：`e6d7e326 fix: retire approval waits for superseded plan packages`。
限定范围已实现并验证；Owner未决未改变。
