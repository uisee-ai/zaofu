> 状态: done；实现：`f8269d79 fix: retain rejected plan candidates across recovery`

# Critic 语义拒绝后的同版候选交接

用户已授权持续监工、修复 blocked task 并恢复。DL 的 `evt-a6d09b3a14ad`
拒绝当前候选后，新代际预演仅继承旧 `dce3ef2a` Plan，未交付当前
`d46b8a92` 候选。原因是恢复引用只覆盖 reader execution failure。

范围：保留当前 Critic 已绑定的候选工件及允许读取路径；自动返工和已结算历史
拒绝的受控恢复均使用这些引用。保留已准入 Plan 基线、语义拒绝、读取要求、
审批和重试边界；不代替 Planner/Critic 修订或批准 Plan。

验收：

- 完整读取后的语义拒绝进入真实结果准入及 fanout 完成入口 -> verify: 新失败事件带同版候选 refs，后续触发不回退旧候选。
- 授权从当前拒绝检查点新建代际 -> verify: 校验唯一准入、Run/stage、合同/manifest 摘要及读取范围，绑定当前候选。
- 篡改、错代、未准入和已被替代的检查点 -> verify: 拒绝恢复；旧 reader failure 恢复仍通过。
- DL 账本只读预演 -> verify: 只恢复 `d46b8a92` 工件，不写旧事件、Plan 或 worker verdict。

验证：修改前两项针对性回归失败；修复后相关 37 项通过。`dev-verify.py plan/run
--base dev` 的 18 文件影响闭包共 542 项通过，包含实际结果准入、受控恢复与
`test_replan_checkpoint_closure_mock_e2e.py` 三个确定性场景；哨兵门 53 项通过。
未运行 full pytest 或真实 Provider E2E。

DL 只读预演取回当前候选 25 份原始引用并校验全部摘要；原派发及明确连接它的
同请求传输重派归为同一检查点，无关联/身份变化的重复派发仍拒绝。未修改运行态。

本任务关闭运行时交接缺陷；实际产品 Plan 修订与独立审批仍由后续工作流承担。
