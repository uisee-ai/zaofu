# Plan Reader 已准入历史基线的输入生命周期一致性

> 状态: active

用户授权持续监工修复。FMS03 OA return_to_plan 受控派发允许读取任务保留的历史已准入包，真实Planner却在artifact index/recover收到superseded、requires_rebind，abstained evt-3af8cfa13854/准入evt-578f938ea7f2。当前handoff预注册/结果准入与input_lifecycle判定不一致；不解除真正过期或未准入输入。

- 在当前dev复现真实调用链不一致 -> verify: 实际prepare/started/公开artifact index/recover仍误报，回归先红。
- 依据活动operation不可变请求/manifest/lease和历史准入证明读取Plan基线 -> verify: domain/type/identity/digest/cancel/new attempt/settlement/包撤销均fail-closed；不授予执行、重绑定或最终结果准入权。
- 保留原恢复上下文与全部正文 -> verify: 真实事件前缀只读回放旧superseded变pinned，正式controlled reader retry后新attempt读状态正确；不得修改旧冻结attempt或全局计划。
- 完成focused、影响闭包、sentinel、相关mock，合并部署并记录 -> verify: 在途worker保留，原FMS03输入修复继续，未完成产品验收不计done。

关联[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)、[198](../docs/design/198-context-index-write-read-resume.md)、[实现说明](../docs/impl/plan-reader-input-lifecycle.md)。

回归先3失败12通过。真实155592条事件前缀只读回放证明运行期间新判断pinned，当前已settled的同一旧attempt仍superseded。自动恢复已经请求新Planner operation，部署后观察该正式后继，无须额外重复重派。

验证：dev-verify run --base dev 选定16个影响测试文件195通过，sentinel53通过；另OA基线与199/replan checkpoint mock19通过。属于定向/影响闭包与mock验证，不是全套或真实provider E2E。
