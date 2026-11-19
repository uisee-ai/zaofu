> 状态: done
> 实现: `dbf9f4f2` — fix: resume independent task verification from bound host probes

# TaskVerify 预授权宿主验证与续跑

来源：真实混合 8×8 Run `workflow-5d021bd50a5ec52b`，TOOL-MASK 的
`evt-5ee82a5de073` 准入了 verifier_execution_failure；只读宿主预览返回
`verification command belongs to a different stage`。冻结合同的验收责任为
candidate_verify，但 Task Pipeline 入口仍为 verify_only。

1. 宿主命令选择沿用冻结合同的验证责任 -> verify: 真实 snapshot/dispatch
   的只验证任务可执行其预授权命令；普通任务不能因此执行未来阶段命令。
2. 宿主执行回执完整且绑定当前合同/目标、旧调用结束后，沿已有 Verify retry
   状态转换恢复独立验证 -> verify: mock Run Manager 生产回执并续派验证，
   保留 TaskRef/Impl，不伪造产品通过；失败退出码仍交独立判断。
3. 缺失、过期、未授权、摘要错误、活动调用、暂停、已变更目标和重复请求
   不可续跑 -> verify: 定向反例及现有恢复影响闭包。
4. 更新实现文档，先 mock/哨兵，再真实恢复验证 -> verify: 分开记录源码测试、
   人工维护和无人资格；新补丁未加载前不声称真实运行已验证。

不修改活动 Run 的 canonical JSON、冻结 Plan、命令白名单或沙箱权限。

源码验证完成：正确 kernel 来源夹具在 dev f8c66cc9 复现3失败/1通过；
修复专项16通过；调度/接力/8槽位及流程mock120通过；最终影响闭包1396通过，
哨兵53通过。首次闭包遗漏了共享回执函数的既有调用方，14项失败，修正调用方后
全绿；一次测试路径误填未执行，已更正。详见 reports/task-audit-host-recovery-20261119。
真实失败任务的只读预览通过；合并后受控维护恢复及全流程资格另在P2记录，
不以源码测试代替真实无人通过。

已合入dev `00d6a0f8`；合并后89项和哨兵53项通过。真实运行经一次明确记录的
源码维护后，Run Manager 自动执行TOOL-NORMALIZE的预授权Docker命令
（evt-6bce3af23699，exit0、source_stable），自动请求Verify重试
（evt-523cccab6ed1）并实际派发下一代（evt-c063e3fa2bc2）。新handoff摘要绑定
宿主原始输出，详见 reports/task-audit-host-recovery-20261119/task-host-real-handoff-effect.json。
这证明本补丁的真实执行接续，不是完整产品/无人资格通过；P2继续保留active。
另两个租约过期任务的shadow策略遗漏独立跟踪，未绕过其权限拒绝或伪造结果。
