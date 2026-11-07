# R41：跨无关增量包恢复保留任务的输入继承

> 状态: active

真实新 Grant 后继派发 evt-4d5159e077e6 被 invalid task input port 拒绝：继承查询遇到仅保留旧 Grant 的附件增量包，误把当前 amend 必须声明该旧任务当作前提。原输入与显式替换边仍在更早冻结包中。

## 验收

- 复现真实结构 -> verify: Impl/Verify 经过两个无关保留包再替换，旧实现均 RED。
- 找到声明替换边的原包 -> verify: 精确 shared+任务分区、原ref/digest不变，冻结包不改。
- 不跨过丢失/语义变化/歧义任务 -> verify: 负例拒绝；原坏输入、错Run、错schema和循环回归保持。
- 实际入口及部署 -> verify: CanonicalHandoffResolver 真实 LS 只读预检，再控制面载入并观察实际派发。

与 R40 同工作树验证合并，两个问题独立记录；不更改业务 AC、不放宽权限或重发全计划输入。

验证：R40 首轮回归因测试预期异常类型不符修正为 RuntimeError 后通过；两次闭包命令引用不存在的测试文件未执行，已改为实际文件。最终20文件262 passed（含两个 deterministic mock E2E），sentinel53 passed。初始 sentinel 因模块从1000增至1002行失败，已将同属声明处理的既有可选字段代码移入新模块，未提高上限。dev-verify planner 自动闭包192文件触发全仓阈值，依 AGENTS 的测试域规则采用上述具体闭包，不运行全仓 pytest、不声称真实Provider E2E。真实 LS 两profile只读预检通过，原分区/ref/digest不变。
