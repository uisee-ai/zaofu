# 修复 Owner 需求版本重放与失效审批

> 状态: active

用户授权持续监工、修复阻塞并记录。FMS03 后继计划初次准入使用受控 Owner 需求修订；相同 task_map.ready 再处理时显式端口比较却使用原始 requirement，误报 changed input 并取消准入。审批请求没有随取消退场，CLI 仍接受批准，虽登记 Task generation，但包 reducer 正确隔离所有同 source 的已取消准入，最终卡在 implementation-initial handoff。

- 初次准入与重放采用同一受控 Owner 需求权威 -> verify: 公共准入入口先RED，带旧ref/inline端口的相同计划安全重放；新Owner修订、其他输入变更仍拒绝旧generation。
- 已取消准入的审批退场 -> verify: CLI/共享决定入口不发approved/rejected；旧批准不可作为新计划等价授权；非kernel/无匹配因果/其他取消不误清除请求。
- 受控恢复 -> verify: 保留隔离取消历史，修复后以新trigger重试原已审核输出，经过正常准入/审批，证明FMS03真实派发；其余在途会话不受影响。

现场：task_map.ready evt-3f43e35576fc；cancel evt-9c85f9ffdbf0；批准evt-473c564a5fc2；deferred evt-cf9ef9fd73b6。监工审批前核验了正文、scope和继承，但遗漏检查已有取消事件，本轮一并修正共享审批投影，不能把错误归为Worker代码失败。

实现：[Owner重放与失效审批](../docs/impl/plan-owner-replay-and-aborted-approval.md)。新增8项先RED、4项保护例已通过；补充新Owner修订仍拒绝旧代的回归后，6文件56 PASS。dev-verify解释273文件影响闭包，执行3843 PASS；serial10、哨兵53、design199及Kanban审批mock3通过。未执行完整pytest或真实Provider E2E。现场相同source/package回放：baseline端口误拒绝且取消审批仍pending，fixed端口通过且审批退场；没有写准入事件或Task状态。
